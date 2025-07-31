# ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import os
import time
import hashlib
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError, Page, BrowserContext

from src.models.game import GameData
from src.config import ITAD_DEALS_URL, DEFAULT_CACHE_TTL, CACHE_DIR

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
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
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                
                logger.info(f"🚀 [{self.__class__.__name__}] Navigating to {self.url}")
                await page.goto(self.url, wait_until='networkidle', timeout=90000)
                
                logger.info(f"[{self.__class__.__name__}] Page loaded. Waiting for deals container '#deals-list'.")
                await page.wait_for_selector('#deals-list', state='visible', timeout=45000)
                
                logger.info(f"[{self.__class__.__name__}] Deals container found. Performing scroll routine.")
                await page.evaluate("""
                    async () => {
                        await new Promise((resolve) => {
                            let totalHeight = 0;
                            const distance = 250; // Scroll a bit more each time
                            const timer = setInterval(() => {
                                const scrollHeight = document.body.scrollHeight;
                                window.scrollBy(0, distance);
                                totalHeight += distance;
                                if (totalHeight >= scrollHeight - window.innerHeight) {
                                    clearInterval(timer);
                                    resolve();
                                }
                            }, 200); // Slower interval
                        });
                    }
                """)
                await asyncio.sleep(5) # Final wait for content to load after scroll
                
                logger.info(f"[{self.__class__.__name__}] Finished scrolling. Capturing page content.")
                content = await page.content()
                logger.debug(f"[{self.__class__.__name__}] Captured HTML content length: {len(content)}")
                return content
        except TimeoutError as e:
            logger.error(f"❌ [{self.__class__.__name__}] Playwright timed out on {self.url}. This could be an anti-bot measure or a page structure change. Error: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] An unexpected error occurred during Playwright fetch: {e}", exc_info=True)
            return None
        finally:
            if browser:
                await browser.close()
                logger.debug(f"[{self.__class__.__name__}] Playwright browser closed.")

    def _parse_deal_element(self, deal_tag: BeautifulSoup) -> Optional[GameData]:
        """Parses a single deal HTML element into GameData."""
        title_tag = deal_tag.select_one('a.game-title')
        store_tag = deal_tag.select_one('.deal-shop a')
        cut_tag = deal_tag.select_one('.deal-cut')

        # Debugging: Log the state of each tag found
        if not title_tag:
            logger.debug("[ITADSource Parser] Skipping deal: Title tag 'a.game-title' not found.")
            return None
        if not store_tag:
            logger.debug(f"[ITADSource Parser] Skipping deal '{title_tag.text.strip()}': Store tag '.deal-shop a' not found.")
            return None
        if not cut_tag:
            logger.debug(f"[ITADSource Parser] Skipping deal '{title_tag.text.strip()}': Cut tag '.deal-cut' not found.")
            return None
            
        title = title_tag.get_text(strip=True)
        url = store_tag['href']
        id_in_db = title_tag['href']
        cut_text = cut_tag.get_text(strip=True)
        is_free = "100%" in cut_text or "Free" in cut_text.title()
        discount_text = cut_text if not is_free else "100% Off"
        
        logger.debug(f"[ITADSource Parser] Parsed Deal: Title='{title}', Store='{store_tag.get_text(strip=True)}', Free={is_free}, Discount='{discount_text}'")

        return GameData(
            title=title,
            store=store_tag.get_text(strip=True).lower(),
            url=url,
            id_in_db=id_in_db,
            is_free=is_free,
            discount_text=discount_text,
        )

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
            return []

        soup = BeautifulSoup(html_content, 'lxml')
        deal_elements = soup.select('#deals-list article.deal')
        logger.info(f"[{self.__class__.__name__}] Found {len(deal_elements)} 'article.deal' elements to parse.")
        
        found_games: List[GameData] = []
        for i, element in enumerate(deal_elements):
            game_data = self._parse_deal_element(element)
            if game_data:
                found_games.append(game_data)
        
        logger.info(f"✅ [{self.__class__.__name__}] Successfully parsed {len(found_games)} total deals.")
        return found_games