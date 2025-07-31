// ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import re
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from src.models.game import GameData
from src.config import ITAD_DEALS_URL

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class ITADSource:
    """Fetches free and discounted games from IsThereAnyDeal.com using Playwright."""

    def _normalize_store_name(self, store_text: str) -> str:
        """Normalizes store names to a consistent lowercase key."""
        return store_text.lower().replace(' ', '')

    async def _parse_deal_element(self, deal_tag: BeautifulSoup) -> Optional[GameData]:
        """Parses a single deal element from the ITAD page."""
        try:
            title_tag = deal_tag.select_one('h3.game-title a')
            if not title_tag:
                return None

            title = title_tag.get_text(strip=True)
            main_link = title_tag.get('href', '#')
            
            store_tag = deal_tag.select_one('div.deal-store a')
            store_text = store_tag.get_text(strip=True) if store_tag else "other"
            deal_url = store_tag.get('href') if store_tag else main_link

            is_free = False
            discount_text = None
            cut_tag = deal_tag.select_one('div.deal-cut')
            if cut_tag:
                cut_text = cut_tag.get_text(strip=True).lower()
                if "100% off" in cut_text or "free" in cut_text:
                    is_free = True
                    discount_text = "100% Off / Free"
                else:
                    discount_match = re.search(r'(\d+%\s*off)', cut_text)
                    discount_text = discount_match.group(1) if discount_match else "Discounted"
            
            return GameData(
                title=title,
                store=self._normalize_store_name(store_text),
                url=deal_url,
                id_in_db=main_link,  # Use the main ITAD link as a unique ID for the deal
                is_free=is_free,
                discount_text=discount_text
            )
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] Error parsing ITAD deal element: {e}", exc_info=True)
            return None

    async def _get_page_content(self) -> Optional[str]:
        """
        Launches Playwright, navigates to the deals page, scrolls to load all content,
        and returns the final HTML.
        """
        logger.info(f"🚀 [{self.__class__.__name__}] Launching Playwright to fetch content from ITAD...")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(ITAD_DEALS_URL, wait_until='networkidle', timeout=60000)
                    await page.wait_for_selector('article.deal', state='visible', timeout=30000)
                    
                    # Scroll to load all deals
                    for _ in range(10):  # Scroll a fixed number of times to load more content
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1.5) # Wait for new content to load
                    
                    content = await page.content()
                    return content
                finally:
                    await browser.close()
        except PlaywrightTimeoutError:
            logger.error(f"❌ [{self.__class__.__name__}] Playwright timed out waiting for content on ITAD.")
            return None
        except Exception as e:
            logger.critical(f"🔥 [{self.__class__.__name__}] A critical error occurred in Playwright: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[GameData]:
        """Fetches and parses all deals from the ITAD deals page."""
        html_content = await self._get_page_content()
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        deal_elements = soup.select('article.deal')
        logger.info(f"[{self.__class__.__name__}] Found {len(deal_elements)} deal elements on the page.")

        tasks = [self._parse_deal_element(deal) for deal in deal_elements]
        parsed_games = await asyncio.gather(*tasks)
        
        # Filter out None values and duplicates based on id_in_db
        final_games: List[GameData] = []
        processed_ids = set()
        for game in parsed_games:
            if game and game['id_in_db'] not in processed_ids:
                final_games.append(game)
                processed_ids.add(game['id_in_db'])
                log_level = "✅" if game['is_free'] else "🔍"
                logger.info(f"{log_level} [{self.__class__.__name__}] Parsed deal: {game['title']} from {game['store']}")

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching. Found {len(final_games)} unique deals.")
        return final_games
