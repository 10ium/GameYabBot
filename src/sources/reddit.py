// ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import re
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

import aiohttp
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import (
    REDDIT_SUBREDDITS,
    REDDIT_RSS_URL_TEMPLATE,
    CACHE_DIR,
    DEFAULT_CACHE_TTL
)
from src.utils.clean_title import clean_title_for_search
from src.utils.url_utils import normalize_url_for_key

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class RedditSource(BaseWebClient):
    """Fetches free game deals from various subreddits via their RSS feeds."""

    def __init__(self, session: aiohttp.ClientSession):
        cache_path = f"{CACHE_DIR}/reddit"
        super().__init__(cache_dir=cache_path, cache_ttl=DEFAULT_CACHE_TTL, session=session)

    def _generate_unique_id(self, base_id: str, item_url: str) -> str:
        """Creates a unique ID based on the post ID and the item URL to avoid collisions."""
        return hashlib.sha256(f"{base_id}-{item_url}".encode()).hexdigest()

    async def _fetch_and_extract_external_link(self, permalink_url: str) -> Optional[str]:
        """Fetches a Reddit post page and extracts the first valid external link."""
        html_content = await self._fetch(permalink_url, is_json=False)
        if not html_content:
            return None
        
        soup = BeautifulSoup(html_content, 'html.parser')
        # This selector targets the main link in modern Reddit layouts
        link_tag = soup.select_one('a[data-testid="outbound-link"]')
        if link_tag and link_tag.get('href'):
            return link_tag['href']
        
        logger.warning(f"[{self.__class__.__name__}] Could not find a primary external link in {permalink_url}")
        return None

    def _classify_deal(self, title: str, subreddit: str) -> (bool, Optional[str]):
        """Classifies a deal as free or discounted based on title and subreddit."""
        title_lower = title.lower()
        if subreddit == 'FreeGameFindings' or "100% off" in title_lower or "[free]" in title_lower:
            return True, "100% Off / Free"
        
        discount_match = re.search(r'(\d+%\s*off)', title_lower)
        if discount_match:
            return False, discount_match.group(1)
            
        if "free to play" in title_lower or "f2p" in title_lower:
            return False, "Free to Play" # Not a temporary free deal

        return False, None # Not a recognized deal type

    async def _normalize_post_data(self, entry: ET.Element, subreddit: str) -> Optional[GameData]:
        """Converts a single RSS entry to the standard GameData format."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem = entry.find('atom:title', ns)
        content_elem = entry.find('atom:content', ns)
        id_elem = entry.find('atom:id', ns)

        if not all([raw_title_elem, content_elem, id_elem]):
            return None
        
        raw_title = raw_title_elem.text
        post_id = id_elem.text
        
        is_free, discount_text = self._classify_deal(raw_title, subreddit)
        if discount_text is None: # If not a freebie or a clear discount, skip it
            return None

        soup = BeautifulSoup(content_elem.text, 'html.parser')
        link_tag = soup.find('a', string='[link]')
        
        final_url = None
        if link_tag and link_tag.get('href'):
            main_post_url = link_tag['href']
            # If the link points to the Reddit comments, fetch the real external link
            if "reddit.com/r/" in main_post_url and "/comments/" in main_post_url:
                final_url = await self._fetch_and_extract_external_link(main_post_url)
            else:
                final_url = main_post_url
        
        if not final_url:
            logger.warning(f"[{self.__class__.__name__}] Could not extract a valid URL for post: {raw_title}")
            return None

        return GameData(
            title=clean_title_for_search(raw_title),
            store="other", # Store will be determined later in the pipeline
            url=final_url,
            id_in_db=post_id,
            is_free=is_free,
            discount_text=discount_text,
            subreddit=subreddit
        )

    async def fetch_free_games(self) -> List[GameData]:
        """Fetches deals from all configured Reddit RSS feeds."""
        logger.info(f"🚀 [{self.__class__.__name__}] Fetching deals from Reddit...")
        all_games: List[GameData] = []
        
        for subreddit in REDDIT_SUBREDDITS:
            url = REDDIT_RSS_URL_TEMPLATE.format(sub=subreddit)
            logger.debug(f"[{self.__class__.__name__}] Scanning RSS feed for r/{subreddit}...")
            
            rss_content = await self._fetch(url, is_json=False)
            if not rss_content:
                continue

            try:
                root = ET.fromstring(rss_content)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                entries = root.findall('atom:entry', ns)
                
                tasks = [self._normalize_post_data(entry, subreddit) for entry in entries]
                parsed_games = await asyncio.gather(*tasks)
                
                valid_games = [game for game in parsed_games if game]
                all_games.extend(valid_games)
                logger.info(f"[{self.__class__.__name__}] Found {len(valid_games)} valid deals in r/{subreddit}.")

            except ET.ParseError as e:
                logger.error(f"❌ [{self.__class__.__name__}] Failed to parse RSS feed from r/{subreddit}: {e}")

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Found {len(all_games)} total deals.")
        return all_games
