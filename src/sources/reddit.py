# ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import re
import hashlib
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR, COMMON_HEADERS
from src.utils.game_utils import clean_title, infer_store_from_game_data
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

# ===== CORE BUSINESS LOGIC =====
class RedditSource(BaseWebClient):
    """Fetches game deals from various subreddits via their RSS feeds,
    using SerpApi for permalink fetching to bypass 403 errors."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "reddit"),
            cache_ttl=cache_ttl,
            session=session
        )
        self.rss_urls = {sub: REDDIT_RSS_URL_TEMPLATE.format(sub=sub) for sub in REDDIT_SUBREDDITS}

    async def _fetch_and_parse_permalink_with_serpapi(self, permalink_url: str) -> Optional[str]:
        """Fetches a Reddit permalink using SerpApi to bypass blocking issues."""
        if not SERPAPI_API_KEY:
            logger.error("❌ [RedditSource] SERPAPI_API_KEY not set. Cannot fetch permalinks.")
            return None
            
        logger.info(f"➡️ [RedditSource] Fetching permalink '{permalink_url}' via SerpApi...")
        
        # **CRITICAL FIX**: Properly URL-encode the permalink before passing it as a parameter.
        # This prevents 400 Bad Request errors from SerpApi.
        encoded_url = quote_plus(permalink_url)
        
        serpapi_url = f"https://serpapi.com/search.json?api_key={SERPAPI_API_KEY}&url={encoded_url}&output=html"
        
        try:
            # We use self._fetch which includes caching and retries.
            html_content = await self._fetch(serpapi_url, is_json=False)
            
            if not html_content:
                logger.warning(f"⚠️ [RedditSource] SerpApi returned no HTML content for {permalink_url}.")
                return None

            soup = BeautifulSoup(html_content, 'lxml')
            main_link = soup.select_one('a[data-testid="outbound-link"]')
            if main_link and main_link.get('href'):
                return main_link['href']
            
            post_content_div = soup.find('div', class_='md')
            if post_content_div:
                for a_tag in post_content_div.find_all('a', href=True):
                    href = a_tag['href']
                    if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                        return href
            
            logger.warning(f"[{self.__class__.__name__}] No external link found in permalink {permalink_url} via SerpApi.")
            return None
        except Exception as e:
            logger.error(f"❌ [RedditSource] SerpApi permalink fetch failed for '{permalink_url}': {e}", exc_info=True)
            return None

    async def _normalize_post_data(self, entry: ET.Element, subreddit_name: str) -> Optional[GameData]:
        """Normalizes a single RSS entry into a structured GameData dict."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem = entry.find('atom:title', ns)
        content_elem = entry.find('atom:content', ns)
        id_elem = entry.find('atom:id', ns)
        link_elem = entry.find('atom:link', ns)

        if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None, link_elem is not None]):
            return None

        raw_title = raw_title_elem.text or ""
        post_id = id_elem.text
        permalink_from_rss = link_elem.get('href')
        deal_url = permalink_from_rss

        if re.search(r'reddit\.com/r/[^/]+/comments/', permalink_from_rss):
            resolved_external_url = await self._fetch_and_parse_permalink_with_serpapi(permalink_from_rss)
            if resolved_external_url:
                deal_url = resolved_external_url
        
        soup_content = BeautifulSoup(content_elem.text, 'lxml')
        description = soup_content.get_text(strip=True, separator=' ')
        image_tag = soup_content.find('img', src=True)
        image_url = image_tag['src'] if image_tag else None
        title = clean_title(raw_title)

        if not title.strip():
            return None

        is_free, discount_text = False, None
        title_lower = raw_title.lower()

        if subreddit_name.lower() == 'freegamefindings' or "free" in title_lower or "100% off" in title_lower:
            is_free, discount_text = True, "100% Off"
        elif "off" in title_lower or "discount" in title_lower:
            match = re.search(r'(\d+%\s*off)', title_lower)
            discount_text = match.group(1) if match else "Discount"
        
        if is_free or discount_text:
            return GameData(
                title=title,
                store=infer_store_from_game_data({"url": deal_url, "title": raw_title, "subreddit": subreddit_name}),
                url=deal_url,
                image_url=image_url,
                description=description,
                id_in_db=post_id,
                subreddit=subreddit_name,
                is_free=is_free,
                discount_text=discount_text
            )
        return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[GameData]:
        """Parses the HTML content of 'Weekly Deals' posts from AppHookup."""
        found_items: List[GameData] = []
        soup = BeautifulSoup(html_content, 'lxml')
        list_elements = soup.find_all(['p', 'li'])
        for item_elem in list_elements:
            a_tag = item_elem.find('a', href=True)
            if not a_tag: continue
            item_url = a_tag['href']
            item_title_raw = a_tag.get_text(strip=True)
            if not item_url or not item_url.startswith('http') or re.search(r'reddit\.com|redd\.it', item_url): continue
            
            item_text_lower = item_elem.get_text(strip=True).lower()
            is_free, discount_text = False, None
            if "free" in item_text_lower or "100% off" in item_text_lower or "-> 0" in item_text_lower:
                is_free, discount_text = True, "100% Off"
            elif "off" in item_text_lower:
                match = re.search(r'(\d+%\s*off)', item_text_lower)
                discount_text = match.group(1) if match else "Discount"
            
            if is_free or discount_text:
                item_title = clean_title(item_title_raw)
                if not item_title.strip(): continue
                found_items.append(GameData(
                    title=item_title,
                    store=infer_store_from_game_data({"url": item_url, "title": item_title_raw, "subreddit": "AppHookup"}),
                    url=item_url,
                    id_in_db=hashlib.sha256(f"{base_post_id}-{item_url}".encode()).hexdigest(),
                    subreddit="AppHookup",
                    is_free=is_free,
                    discount_text=discount_text
                ))
        return found_items

    async def fetch_free_games(self) -> List[GameData]:
        """Main method to fetch and process deals from all configured subreddits."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {', '.join(self.rss_urls.keys())}")
        all_games: List[GameData] = []
        for subreddit_name, url in self.rss_urls.items():
            rss_content = await self._fetch(url, is_json=False, headers=COMMON_HEADERS)
            if not rss_content: continue
            try:
                root = ET.fromstring(rss_content)
            except ET.ParseError:
                continue
            
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', ns)
            logger.info(f"[{self.__class__.__name__}] Found {len(entries)} entries in r/{subreddit_name} RSS feed.")
            
            tasks = []
            for entry in entries:
                raw_title_elem = entry.find('atom:title', ns)
                content_elem = entry.find('atom:content', ns)
                id_elem = entry.find('atom:id', ns)
                if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None]): continue
                
                raw_title = raw_title_elem.text or ""
                if subreddit_name.lower() == 'apphookup' and ("weekly" in raw_title.lower() and "deals" in raw_title.lower()):
                    all_games.extend(self._parse_apphookup_weekly_deals(content_elem.text, id_elem.text))
                else:
                    tasks.append(self._normalize_post_data(entry, subreddit_name))
            
            normalized_posts = await asyncio.gather(*tasks)
            all_games.extend([game for game in normalized_posts if game is not None])

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Total potential deals: {len(all_games)}")
        return all_games