# ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import re
import hashlib
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import os  # <--- **خط اصلاح شده در اینجا قرار دارد**

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR, COMMON_HEADERS
from src.utils.game_utils import clean_title, infer_store_from_game_data

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
        """Fetches a Reddit permalink using SerpApi to bypass 403 errors."""
        if not SERPAPI_API_KEY:
            logger.error("❌ [RedditSource] SERPAPI_API_KEY not set. Cannot fetch permalinks via SerpApi.")
            return None
            
        logger.info(f"➡️ [RedditSource] Fetching permalink '{permalink_url}' via SerpApi...")
        params = {
            "api_key": SERPAPI_API_KEY,
            "url": permalink_url,
            "output": "html" # We want the raw HTML content
        }
        
        try:
            async with self._session.get("https://serpapi.com/search", params=params, timeout=30) as response:
                response.raise_for_status()
                # SerpApi with output=html returns the raw HTML directly
                html_content = await response.text()
                
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
            else:
                logger.warning(f"[{self.__class__.__name__}] Could not resolve external URL from '{permalink_from_rss}'. Using Reddit permalink as fallback.")
        
        soup_content = BeautifulSoup(content_elem.text, 'lxml')
        description = soup_content.get_text(strip=True, separator=' ')
        image_tag = soup_content.find('img', src=True)
        image_url = image_tag['src'] if image_tag else None

        title = clean_title(raw_title)
        if not title.strip():
            return None

        is_free = False
        discount_text = None
        title_lower = raw_title.lower()

        if subreddit_name.lower() == 'freegamefindings' or "free" in title_lower or "100% off" in title_lower:
            is_free = True
            discount_text = "100% Off"
        elif "off" in title_lower or "discount" in title_lower:
            match = re.search(r'(\d+%\s*off)', title_lower)
            discount_text = match.group(1) if match else "Discount"
            is_free = False

        if is_free or discount_text:
            return GameData(
                title=title,
                store=infer_store_from_game_data({"url": deal_url, "title": raw_title}),
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
            is_free = False; discount_text = None
            if "free" in item_text_lower or "100% off" in item_text_lower or "-> 0" in item_text_lower:
                is_free = True; discount_text = "100% Off"
            elif "off" in item_text_lower:
                match = re.search(r'(\d+%\s*off)', item_text_lower)
                discount_text = match.group(1) if match else "Discount"; is_free = False
            if is_free or discount_text:
                item_description = item_elem.get_text(separator=' ', strip=True)
                item_image_tag = item_elem.find('img', src=True)
                item_image_url = item_image_tag['src'] if item_image_tag else None
                item_title = clean_title(item_title_raw)
                if not item_title.strip(): continue
                found_items.append(GameData(title=item_title, store=infer_store_from_game_data({"url": item_url, "title": item_title_raw}), url=item_url, image_url=item_image_url, description=item_description, id_in_db=hashlib.sha256(f"{base_post_id}-{item_url}".encode()).hexdigest(), subreddit="AppHookup", is_free=is_free, discount_text=discount_text))
        return found_items

    async def fetch_free_games(self) -> List[GameData]:
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {', '.join(self.rss_urls.keys())}")
        all_games: List[GameData] = []
        for subreddit_name, url in self.rss_urls.items():
            rss_content = await self._fetch(url, is_json=False, headers=COMMON_HEADERS)
            if not rss_content:
                logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch RSS for r/{subreddit_name}. Skipping.")
                continue
            try:
                root = ET.fromstring(rss_content)
            except ET.ParseError as e:
                logger.error(f"❌ [{self.__class__.__name__}] Failed to parse XML for r/{subreddit_name}: {e}")
                continue
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', ns)
            for entry in entries:
                raw_title_elem = entry.find('atom:title', ns)
                content_elem = entry.find('atom:content', ns)
                id_elem = entry.find('atom:id', ns)
                if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None]): continue
                raw_title = raw_title_elem.text or ""
                post_id = id_elem.text
                if subreddit_name.lower() == 'apphookup' and ("weekly" in raw_title.lower() and "deals" in raw_title.lower()):
                    internal_deals = self._parse_apphookup_weekly_deals(content_elem.text, post_id)
                    all_games.extend(internal_deals)
                    continue
                game_data = await self._normalize_post_data(entry, subreddit_name)
                if game_data:
                    all_games.append(game_data)
        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Total potential deals: {len(all_games)}")
        return all_games