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

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR, COMMON_HEADERS
from src.utils.game_utils import clean_title, infer_store_from_game_data

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY") # Ensure this is read from env

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
            "url": permalink_url, # Specify the URL to scrape
            "output": "json" # We want the raw HTML content in JSON
        }
        
        try:
            # Note: We are using a direct aiohttp call here, not self._fetch from BaseWebClient,
            # because SerpApi has its own caching and retry mechanisms, and we don't want to
            # expose the API key in our local cache's URL hash.
            async with self._session.get("https://serpapi.com/search", params=params, timeout=30) as response:
                response.raise_for_status()
                data = await response.json()
                
                # The HTML content is usually in the 'html' field of the JSON response
                html_content = data.get("html")
                if not html_content:
                    logger.warning(f"⚠️ [RedditSource] SerpApi returned no HTML content for {permalink_url}.")
                    return None

                soup = BeautifulSoup(html_content, 'lxml')
                # Common selectors for outbound links in Reddit posts (new and old UI)
                main_link = soup.select_one('a[data-testid="outbound-link"]')
                if main_link and main_link.get('href'):
                    logger.debug(f"[{self.__class__.__name__}] Found outbound link via data-testid: {main_link['href']} (via SerpApi)")
                    return main_link['href']
                
                # Fallback to search within post content for any external link
                post_content_div = soup.find('div', class_='_292iotee39Lmt0Q_h-B5N') or \
                                   soup.find('div', class_='_1qeIAgB0cPwnLhDF9Xvm') or \
                                   soup.find('div', class_='md')
                
                if post_content_div:
                    for a_tag in post_content_div.find_all('a', href=True):
                        href = a_tag['href']
                        if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                            logger.debug(f"[{self.__class__.__name__}] Found fallback external link in post content: {href} (via SerpApi)")
                            return href
                
                logger.warning(f"[{self.__class__.__name__}] No external link found in permalink {permalink_url} via SerpApi.")
                return None
        except Exception as e:
            logger.error(f"❌ [RedditSource] SerpApi permalink fetch failed for '{permalink_url}': {e}", exc_info=True)
            return None

    async def _normalize_post_data(self, entry: ET.Element, subreddit_name: str) -> Optional[GameData]:
        # ... (بخش ابتدایی بدون تغییر) ...
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem = entry.find('atom:title', ns)
        content_elem = entry.find('atom:content', ns)
        id_elem = entry.find('atom:id', ns)
        link_elem = entry.find('atom:link', ns)

        if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None, link_elem is not None]):
            logger.debug(f"[{self.__class__.__name__}] Skipping malformed RSS entry in {subreddit_name} (missing core elements).")
            return None

        raw_title = raw_title_elem.text or ""
        post_id = id_elem.text
        permalink_from_rss = link_elem.get('href')

        deal_url = permalink_from_rss # Default to permalink
        
        # If the primary link from RSS is a Reddit permalink, try to resolve its external URL via SerpApi
        if re.search(r'reddit\.com/r/[^/]+/comments/', permalink_from_rss):
            resolved_external_url = await self._fetch_and_parse_permalink_with_serpapi(permalink_from_rss)
            if resolved_external_url:
                deal_url = resolved_external_url
                logger.debug(f"[{self.__class__.__name__}] Resolved external URL from permalink: {deal_url} (via SerpApi)")
            else:
                logger.warning(f"[{self.__class__.__name__}] Could not resolve external URL from '{permalink_from_rss}' via SerpApi. Using Reddit permalink as fallback.")
        else:
            # If the RSS link is already external, use it
            deal_url = permalink_from_rss
            logger.debug(f"[{self.__class__.__name__}] RSS link is already external: {deal_url}")
            
        # Parse description and image from the content element (HTML embedded in CDATA)
        soup_content = BeautifulSoup(content_elem.text, 'lxml')
        description_tag = soup_content.find('div', class_='md')
        description = description_tag.get_text(strip=True) if description_tag else ""

        image_tag = soup_content.find('img', src=True)
        image_url = image_tag['src'] if image_tag else None

        title = clean_title(raw_title)
        if not title.strip():
            logger.debug(f"[{self.__class__.__name__}] Skipping post with empty cleaned title from raw: '{raw_title}'")
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
        else:
            logger.debug(f"[{self.__class__.__name__}] Skipping post '{raw_title}' as it is not identified as free or a discount.")
        
        return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[GameData]:
        # ... (این متد بدون تغییر باقی می‌ماند) ...
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
        """Fetches and processes deals from all configured subreddits."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {', '.join(self.rss_urls.keys())}")
        all_games: List[GameData] = []
        for subreddit_name, url in self.rss_urls.items():
            logger.info(f"[{self.__class__.__name__}] Fetching RSS for r/{subreddit_name} from {url}")
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
            logger.info(f"[{self.__class__.__name__}] Found {len(entries)} entries in r/{subreddit_name} RSS feed.")
            for entry in entries:
                raw_title_elem = entry.find('atom:title', ns)
                content_elem = entry.find('atom:content', ns)
                id_elem = entry.find('atom:id', ns)

                if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None]): continue
                raw_title = raw_title_elem.text or ""
                post_id = id_elem.text
                if subreddit_name.lower() == 'apphookup' and ("weekly" in raw_title.lower() and "deals" in raw_title.lower()):
                    logger.info(f"[{self.__class__.__name__}] Detected AppHookup 'Weekly Deals' post: '{raw_title}'. Parsing internal items.")
                    internal_deals = self._parse_apphookup_weekly_deals(content_elem.text, post_id)
                    all_games.extend(internal_deals)
                    continue
                game_data = await self._normalize_post_data(entry, subreddit_name)
                if game_data:
                    all_games.append(game_data)
                    logger.debug(f"[{self.__class__.__name__}] Added post: '{game_data['title']}' from r/{subreddit_name}.")
        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Total potential deals: {len(all_games)}")
        return all_games