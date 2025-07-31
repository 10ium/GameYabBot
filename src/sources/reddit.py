// ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import re
import hashlib
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR
from src.utils.clean_title import clean_title_for_search # Assuming file rename
from src.utils.url_utils import normalize_url_for_key # Assuming new utility file
import os

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class RedditSource(BaseWebClient):
    """Fetches game deals from various subreddits via their RSS feeds."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "reddit"),
            cache_ttl=cache_ttl,
            session=session
        )
        self.rss_urls = {sub: REDDIT_RSS_URL_TEMPLATE.format(sub=sub) for sub in REDDIT_SUBREDDITS}

    async def _fetch_and_parse_permalink(self, permalink_url: str) -> Optional[str]:
        """Fetches a Reddit permalink and extracts the first external link."""
        html_content = await self._fetch(permalink_url, is_json=False)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # This selector targets the main link in modern Reddit layouts
        main_link = soup.select_one('a[data-testid="outbound-link"]')
        if main_link and main_link.get('href'):
            logger.debug(f"[{self.__class__.__name__}] Found outbound link via data-testid: {main_link['href']}")
            return main_link['href']
        
        logger.warning(f"[{self.__class__.__name__}] Could not find a primary outbound link in {permalink_url}. Searching all links.")
        # Fallback for other layouts
        content_div = soup.find('div', id="t3_1_content") or soup.body
        if content_div:
            for a_tag in content_div.find_all('a', href=True):
                href = a_tag['href']
                if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                    logger.debug(f"[{self.__class__.__name__}] Found fallback external link: {href}")
                    return href
        return None

    def _normalize_post(self, entry: ET.Element, subreddit: str) -> Optional[GameData]:
        """Normalizes a single RSS entry into a structured GameData dict."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem = entry.find('atom:title', ns)
        content_elem = entry.find('atom:content', ns)
        id_elem = entry.find('atom:id', ns)
        link_elem = entry.find('atom:link', ns)

        if not all([raw_title_elem, content_elem, id_elem, link_elem]):
            return None

        raw_title = raw_title_elem.text
        post_id = id_elem.text
        permalink = link_elem.get('href')

        soup = BeautifulSoup(content_elem.text, 'html.parser')
        deal_link_tag = soup.find('a', string='[link]')
        deal_url = deal_link_tag['href'] if deal_link_tag else permalink
        
        description = soup.get_text(strip=True, separator=' ')
        
        title = clean_title_for_search(raw_title)

        is_free = False
        discount_text = None
        title_lower = raw_title.lower()

        if "free" in title_lower or "100% off" in title_lower:
            is_free = True
            discount_text = "100% Off"
        elif "off" in title_lower:
            is_free = False
            match = re.search(r'(\d+%\s*off)', title_lower)
            discount_text = match.group(1) if match else "Discount"

        if is_free or discount_text:
            return {
                "title": title,
                "url": deal_url,
                "id_in_db": post_id,
                "is_free": is_free,
                "discount_text": discount_text,
                "description": description,
                "subreddit": subreddit,
                "store": "other" # Store will be inferred later
            }
        return None

    async def fetch_free_games(self) -> List[GameData]:
        """Fetches and processes deals from all configured subreddits."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {list(self.rss_urls.keys())}")
        
        tasks = [self._fetch_subreddit(sub, url) for sub, url in self.rss_urls.items()]
        results = await asyncio.gather(*tasks)
        
        all_games = [game for sub_list in results for game in sub_list]
        logger.info(f"✅ [{self.__class__.__name__}] Total raw deals found across all subreddits: {len(all_games)}")
        return all_games

    async def _fetch_subreddit(self, subreddit: str, url: str) -> List[GameData]:
        """Fetches and parses a single subreddit's RSS feed."""
        logger.info(f"[{self.__class__.__name__}] Fetching RSS for r/{subreddit}")
        rss_content = await self._fetch(url, is_json=False)
        if not rss_content:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch RSS for r/{subreddit}.")
            return []

        try:
            root = ET.fromstring(rss_content)
        except ET.ParseError as e:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to parse XML for r/{subreddit}: {e}")
            return []

        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        found_games: List[GameData] = []
        
        for entry in entries:
            game_data = self._normalize_post(entry, subreddit)
            if game_data:
                # If the URL points to a Reddit post, try to resolve the external link
                if 'reddit.com/r/' in game_data['url']:
                    resolved_url = await self._fetch_and_parse_permalink(game_data['url'])
                    if resolved_url:
                        game_data['url'] = resolved_url
                    else:
                        logger.warning(f"Could not resolve external link for '{game_data['title']}'. Keeping permalink.")
                
                found_games.append(game_data)

        logger.info(f"[{self.__class__.__name__}] Found {len(found_games)} potential deals in r/{subreddit}.")
        return found_games