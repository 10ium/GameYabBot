// ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import os
import time
import hashlib
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError, Page

from src.models.game import GameData
from src.config import ITAD_DEALS_URL, DEFAULT_CACHE_TTL, CACHE_DIR

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class ITADSource:
    """Fetches deals from IsThereAnyDeal.com using Playwright for dynamic content."""

    def __init__(self, cache_ttl: int = DEFAULT_CACHE_TTL):
        self._cache_dir = os.path.join(CACHE_DIR, "itad")
        self._cache_ttl = cache_ttl
        os.makedirs(self._cache_dir, exist_ok=True)
        self.url = ITAD_DEALS_URL
        logger.debug(f"[{self.__class__.__name__}] Initialized with cache dir: {self._cache_dir} and TTL: {self._cache_ttl}s")
        
    def _get_cache_path(self) -> str:
        url_hash = hashlib.sha256(self.url.encode('utf-8')).hexdigest()
        return os.path.join(self._cache_dir, f"{url_hash}.html")

    def _is_cache_valid(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        return (time.time() - os.path.getmtime(path)) <= self._cache_ttl

    async def _fetch_with_playwright(self) -> Optional[str]:
        """Fetches the dynamic page content using Playwright."""
        pw_context = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                pw_context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
                )
                page = await pw_context.new_page()
                
                logger.info(f"🚀 [{self.__class__.__name__}] Navigating to {self.url}")
                await page.goto(self.url, wait_until='domcontentloaded', timeout=60000)
                
                # Wait for the main container of deals to be present
                await page.wait_for_selector('#deals-list', state='visible', timeout=30000)
                logger.info(f"[{self.__class__.__name__}] Deals container found. Scrolling to load all content.")

                # Scroll to the bottom to load all dynamic content
                last_height = await page.evaluate("document.body.scrollHeight")
                while True:
                    await page.mouse.wheel(0, last_height)
                    await page.wait_for_timeout(2000) # Wait for new content to load
                    new_height = await page.evaluate("document.body.scrollHeight")
                    if new_height == last_height:
                        break
                    last_height = new_height
                
                logger.info(f"[{self.__class__.__name__}] Finished scrolling. Capturing page content.")
                content = await page.content()
                await browser.close()
                return content
        except TimeoutError:
            logger.error(f"❌ [{self.__class__.__name__}] Playwright timed out waiting for content on {self.url}.")
            if pw_context: await pw_context.close()
            return None
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] An unexpected error occurred during Playwright fetch: {e}", exc_info=True)
            if pw_context: await pw_context.close()
            return None

    def _parse_deal_element(self, deal_tag: BeautifulSoup) -> Optional[GameData]:
        """Parses a single deal HTML element into GameData."""
        title_tag = deal_tag.select_one('a.game-title')
        store_tag = deal_tag.select_one('.deal-shop a')
        cut_tag = deal_tag.select_one('.deal-cut')

        if not all([title_tag, store_tag, cut_tag]):
            return None
            
        title = title_tag.get_text(strip=True)
        url = store_tag['href']
        # The ID for deduplication is the relative link to the ITAD game page
        id_in_db = title_tag['href']

        cut_text = cut_tag.get_text(strip=True)
        is_free = "100%" in cut_text or "Free" in cut_text.title()
        discount_text = cut_text if not is_free else "100% Off"

        return {
            "title": title,
            "store": store_tag.get_text(strip=True).lower(),
            "url": url,
            "id_in_db": id_in_db,
            "is_free": is_free,
            "discount_text": discount_text,
        }

    async def fetch_free_games(self) -> List[GameData]:
        """Main method to fetch all deals from ITAD."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch...")
        cache_path = self._get_cache_path()
        html_content = None

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [{self.__class__.__name__}] Loading content from cache: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        else:
            html_content = await self._fetch_with_playwright()
            if html_content:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                logger.info(f"💾 [{self.__class__.__name__}] Content saved to cache: {cache_path}")

        if not html_content:
            logger.error(f"❌ [{self.__class__.__name__}] Could not retrieve HTML content.")
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        deal_elements = soup.select('#deals-list .deal')
        
        found_games: List[GameData] = []
        for element in deal_elements:
            game_data = self._parse_deal_element(element)
            if game_data:
                found_games.append(game_data)
        
        logger.info(f"✅ [{self.__class__.__name__}] Found {len(found_games)} total deals.")
        return found_games