# ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import re
import hashlib
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError
import os

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import REDDIT_SUBREDDITS, REDDIT_RSS_URL_TEMPLATE, DEFAULT_CACHE_TTL, CACHE_DIR, COMMON_HEADERS
from src.utils.game_utils import clean_title, infer_store_from_game_data

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class RedditSource(BaseWebClient):
    """
    Fetches game deals from Reddit RSS feeds. Uses a hybrid approach:
    - aiohttp for fetching the fast and simple RSS feeds.
    - Playwright for fetching individual Reddit posts (permalinks) to bypass anti-bot measures.
    - Incorporates robust logic from the original implementation for deal identification.
    """

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "reddit"),
            cache_ttl=cache_ttl,
            session=session
        )
        self.rss_urls = {sub: REDDIT_RSS_URL_TEMPLATE.format(sub=sub) for sub in REDDIT_SUBREDDITS}

    async def _fetch_permalink_with_playwright(self, permalink_url: str) -> Optional[str]:
        """Fetches a Reddit permalink using Playwright to bypass blocking."""
        logger.info(f"➡️ [RedditSource] Fetching permalink '{permalink_url}' via Playwright...")
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(permalink_url, wait_until='domcontentloaded', timeout=45000)
                
                try:
                    outbound_link_locator = page.locator('a[data-testid="outbound-link"]')
                    await outbound_link_locator.wait_for(timeout=15000)
                    href = await outbound_link_locator.get_attribute('href')
                    if href:
                        logger.info(f"✅ [RedditSource] Successfully extracted outbound link: {href}")
                        return href
                except TimeoutError:
                    logger.warning(f"Primary outbound link not found for {permalink_url}. Checking post content.")
                    post_body = await page.content()
                    soup = BeautifulSoup(post_body, 'lxml')
                    post_content_div = soup.find('div', class_='md')
                    if post_content_div:
                        for a_tag in post_content_div.find_all('a', href=True):
                            href = a_tag['href']
                            if not re.search(r'reddit\.com|redd\.it', href) and href.startswith('http'):
                                logger.info(f"✅ [RedditSource] Found fallback external link: {href}")
                                return href
                
                logger.warning(f"⚠️ [RedditSource] No valid external link found on page: {permalink_url}")
                return None
        except Exception as e:
            logger.error(f"❌ [RedditSource] Playwright fetch failed for '{permalink_url}': {e}", exc_info=True)
            return None
        finally:
            if browser:
                await browser.close()

    async def _normalize_post_data(self, entry: ET.Element, subreddit_name: str) -> Optional[GameData]:
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        raw_title_elem, content_elem, id_elem, link_elem = (entry.find(f'atom:{tag}', ns) for tag in ['title', 'content', 'id', 'link'])
        if not all([raw_title_elem is not None, content_elem is not None, id_elem is not None, link_elem is not None, raw_title_elem.text]):
            return None

        raw_title = raw_title_elem.text
        post_id = id_elem.text
        permalink_from_rss = link_elem.get('href')

        soup_content = BeautifulSoup(content_elem.text, 'lxml')
        deal_url = permalink_from_rss
        link_tag_in_content = soup_content.find('a', string='[link]')
        
        if link_tag_in_content and 'href' in link_tag_in_content.attrs:
            deal_url = link_tag_in_content['href']

        if re.search(r'reddit\.com/r/[^/]+/comments/', deal_url):
            resolved_url = await self._fetch_permalink_with_playwright(deal_url)
            if resolved_url:
                deal_url = resolved_url

        is_free, discount_text = False, None
        title_lower = raw_title.lower()

        if subreddit_name.lower() == 'freegamefindings':
            is_free, discount_text = True, "100% Off"
        elif "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
            is_free, discount_text = True, "100% Off"
        elif "off" in title_lower or "discount" in title_lower:
            is_free = False
            match = re.search(r'(\d+%\s*off)', title_lower)
            discount_text = match.group(1) if match else "Discount"
        
        if not (is_free or discount_text):
            return None

        return GameData(
            title=clean_title(raw_title),
            store=infer_store_from_game_data({"url": deal_url, "title": raw_title, "subreddit": subreddit_name}),
            url=deal_url,
            image_url=(img['src'] if (img := soup_content.find('img', src=True)) else None),
            description=soup_content.get_text(strip=True, separator=' '),
            id_in_db=post_id,
            subreddit=subreddit_name,
            is_free=is_free,
            discount_text=discount_text
        )

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[GameData]:
        found_items: List[GameData] = []
        soup = BeautifulSoup(html_content, 'lxml')
        for item_elem in soup.find_all(['p', 'li']):
            a_tag = item_elem.find('a', href=True)
            if not a_tag: continue
            item_url = a_tag['href']
            item_title_raw = a_tag.get_text(strip=True)
            if not (item_url and item_url.startswith('http') and not re.search(r'reddit\.com|redd\.it', item_url)):
                continue
            
            item_text_lower = item_elem.get_text(strip=True).lower()
            is_free, discount_text = False, None
            if "free" in item_text_lower or "100% off" in item_text_lower or "-> 0" in item_text_lower:
                is_free, discount_text = True, "100% Off"
            elif "off" in item_text_lower:
                discount_text = (match.group(1) if (match := re.search(r'(\d+%\s*off)', item_text_lower)) else "Discount")
            
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
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from subreddits: {', '.join(self.rss_urls.keys())}")
        all_games: List[GameData] = []
        
        for subreddit_name, url in self.rss_urls.items():
            rss_content = await self._fetch(url, is_json=False, headers=COMMON_HEADERS)
            if not rss_content: continue
            
            try:
                root = ET.fromstring(rss_content)
                entries = root.findall('.//atom:entry', {'atom': 'http://www.w3.org/2005/Atom'})
                logger.info(f"[{self.__class__.__name__}] Found {len(entries)} entries in r/{subreddit_name} RSS feed.")
                
                tasks = []
                for entry in entries:
                    content_elem = entry.find('atom:content', {'atom': 'http://www.w3.org/2005/Atom'})
                    title_elem = entry.find('atom:title', {'atom': 'http://www.w3.org/2005/Atom'})
                    id_elem = entry.find('atom:id', {'atom': 'http://www.w3.org/2005/Atom'})
                    if not all([content_elem is not None, title_elem is not None, id_elem is not None, title_elem.text]): continue

                    raw_title = title_elem.text
                    if subreddit_name.lower() == 'apphookup' and ("weekly" in raw_title.lower() and "deals" in raw_title.lower()):
                        all_games.extend(self._parse_apphookup_weekly_deals(content_elem.text, id_elem.text))
                    else:
                        tasks.append(self._normalize_post_data(entry, subreddit_name))
                
                normalized_posts = await asyncio.gather(*tasks)
                all_games.extend([game for game in normalized_posts if game])

            except ET.ParseError as e:
                logger.error(f"❌ [{self.__class__.__name__}] Failed to parse XML for r/{subreddit_name}: {e}")
                continue

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching from Reddit. Total potential deals: {len(all_games)}")
        return all_games