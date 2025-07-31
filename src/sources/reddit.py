# ===== IMPORTS & DEPENDENCIES =====
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
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR, COMMON_HEADERS
from src.utils.game_utils import clean_title, infer_store_from_game_data
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
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
        # Use common headers for Reddit permalink fetches
        html_content = await self._fetch(permalink_url, is_json=False, headers=COMMON_HEADERS)
        if not html_content:
            logger.warning(f"[{self.__class__.__name__}] Failed to fetch permalink: {permalink_url}")
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # Common selectors for outbound links in Reddit posts (new and old UI)
        # Prioritize outbound-link, then a regular link within the markdown content
        main_link = soup.select_one('a[data-testid="outbound-link"]')
        if main_link and main_link.get('href'):
            logger.debug(f"[{self.__class__.__name__}] Found outbound link via data-testid: {main_link['href']}")
            return main_link['href']
        
        # Fallback to search within post content for any external link
        post_content_div = soup.find('div', class_='_292iotee39Lmt0Q_h-B5N') or \
                           soup.find('div', class_='_1qeIAgB0cPwnLhDF9Xvm') or \
                           soup.find('div', class_='md') # Markdown div
        
        if post_content_div:
            for a_tag in post_content_div.find_all('a', href=True):
                href = a_tag['href']
                # Ensure it's not a Reddit internal link and is a full HTTP/HTTPS URL
                if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                    logger.debug(f"[{self.__class__.__name__}] Found fallback external link in post content: {href}")
                    return href
        
        logger.warning(f"[{self.__class__.__name__}] No external link found in permalink: {permalink_url}")
        return None

    async def _normalize_post_data(self, entry: ET.Element, subreddit_name: str) -> Optional[GameData]:
        """Normalizes a single RSS entry into a structured GameData dict."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem = entry.find('atom:title', ns)
        content_elem = entry.find('atom:content', ns)
        id_elem = entry.find('atom:id', ns)
        link_elem = entry.find('atom:link', ns) # Primary link from RSS feed

        if not all([raw_title_elem, content_elem, id_elem, link_elem]):
            logger.debug(f"[{self.__class__.__name__}] Skipping malformed RSS entry in {subreddit_name}.")
            return None

        raw_title = raw_title_elem.text
        post_id = id_elem.text
        permalink_from_rss = link_elem.get('href') # This is the Reddit post URL

        soup = BeautifulSoup(content_elem.text, 'html.parser')
        
        # Try to find the direct deal link
        deal_url = permalink_from_rss # Default to permalink
        link_tag_in_content = soup.find('a', string='[link]') # The text "[link]" is common in RSS
        
        if link_tag_in_content and 'href' in link_tag_in_content.attrs:
            potential_deal_url = link_tag_in_content['href']
            # If the '[link]' itself points to a Reddit permalink, try to resolve it
            if re.search(r'reddit\.com/r/[^/]+/comments/', potential_deal_url):
                resolved_external_url = await self._fetch_and_parse_permalink(potential_deal_url)
                if resolved_external_url:
                    deal_url = resolved_external_url
                    logger.debug(f"[{self.__class__.__name__}] Resolved external URL from permalink: {deal_url}")
                else:
                    logger.warning(f"[{self.__class__.__name__}] Could not resolve external URL from '{potential_deal_url}'. Using Reddit permalink.")
            else:
                deal_url = potential_deal_url # It's a direct external link
                logger.debug(f"[{self.__class__.__name__}] Found direct external URL from '[link]': {deal_url}")
        else:
            # As a last resort, check other links in the content, or stick with permalink
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                    deal_url = href
                    logger.debug(f"[{self.__class__.__name__}] Found fallback external URL in content: {deal_url}")
                    break

        description_tag = soup.find('div', class_='md')
        description = description_tag.get_text(strip=True) if description_tag else ""

        image_tag = soup.find('img', src=True)
        image_url = image_tag['src'] if image_tag else None

        title = clean_title(raw_title)
        if not title.strip():
            logger.warning(f"[{self.__class__.__name__}] Skipping post with empty cleaned title: '{raw_title}'")
            return None

        is_free = False
        discount_text = None
        title_lower = raw_title.lower()

        # Logic to determine if it's free or discounted
        if subreddit_name == 'freegamefindings' or "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
            is_free = True
            discount_text = "100% Off"
        elif "off" in title_lower or "discount" in title_lower:
            match = re.search(r'(\d+%\s*off)', title_lower)
            discount_text = match.group(1) if match else "Discount"
            is_free = False # Explicitly set to False if it's a discount

        if is_free or discount_text: # Only include if it's free or has a discount
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
        """Parses the HTML content of 'Weekly Deals' posts from AppHookup subreddit."""
        found_items: List[GameData] = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for list items or paragraphs that contain links
        list_elements = soup.find_all(['p', 'li'])

        for item_elem in list_elements:
            a_tag = item_elem.find('a', href=True)
            if not a_tag:
                continue

            item_url = a_tag['href']
            item_title_raw = a_tag.get_text(strip=True)
            
            if not item_url or not item_url.startswith('http') or re.search(r'reddit\.com|redd\.it', item_url):
                continue # Skip internal or invalid links

            # Check surrounding text for 'free' or discount indications
            item_text_lower = item_elem.get_text(strip=True).lower()
            
            is_free = False
            discount_text = None
            
            if "free" in item_text_lower or "100% off" in item_text_lower or "-> 0" in item_text_lower:
                is_free = True
                discount_text = "100% Off"
            elif "off" in item_text_lower:
                match = re.search(r'(\d+%\s*off)', item_text_lower)
                discount_text = match.group(1) if match else "Discount"
                is_free = False

            if is_free or discount_text:
                item_description = item_elem.get_text(separator=' ', strip=True)
                item_image_tag = item_elem.find('img', src=True)
                item_image_url = item_image_tag['src'] if item_image_tag else None

                item_title = clean_title(item_title_raw)
                if not item_title.strip():
                    logger.warning(f"[{self.__class__.__name__}] Skipping AppHookup item with empty cleaned title: '{item_title_raw}'")
                    continue
                
                found_items.append(GameData(
                    title=item_title,
                    store=infer_store_from_game_data({"url": item_url, "title": item_title_raw}),
                    url=item_url,
                    image_url=item_image_url,
                    description=item_description,
                    id_in_db=hashlib.sha256(f"{base_post_id}-{item_url}".encode()).hexdigest(), # Unique ID for sub-items
                    subreddit="AppHookup",
                    is_free=is_free,
                    discount_text=discount_text
                ))
        return found_items

    async def fetch_free_games(self) -> List[GameData]:
        """Fetches and processes deals from all configured subreddits."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {', '.join(self.rss_urls.keys())}")
        
        all_games: List[GameData] = []
        for subreddit_name, url in self.rss_urls.items():
            logger.info(f"[{self.__class__.__name__}] Fetching RSS for r/{subreddit_name} from {url}")
            rss_content = await self._fetch(url, is_json=False, headers=COMMON_HEADERS) # Pass headers
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

                if not all([raw_title_elem, content_elem, id_elem]):
                    continue # Skip incomplete entries
                
                raw_title = raw_title_elem.text
                post_id = id_elem.text
                
                # Special handling for AppHookup weekly deals posts
                if subreddit_name.lower() == 'apphookup' and ("weekly" in raw_title.lower() and "deals" in raw_title.lower()):
                    logger.info(f"[{self.__class__.__name__}] Detected AppHookup 'Weekly Deals' post: '{raw_title}'. Parsing internal items.")
                    internal_deals = self._parse_apphookup_weekly_deals(content_elem.text, post_id)
                    all_games.extend(internal_deals)
                    continue # Move to next RSS entry after processing internal deals

                # For regular posts (not weekly deals)
                game_data = await self._normalize_post_data(entry, subreddit_name)
                if game_data:
                    all_games.append(game_data)
                    logger.debug(f"[{self.__class__.__name__}] Added post: '{game_data['title']}' from r/{subreddit_name}.")

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Total potential deals: {len(all_games)}")
        return all_games