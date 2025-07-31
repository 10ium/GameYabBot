# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError

from src.models.game import GameData
from src.config import DEFAULT_CACHE_TTL, CACHE_DIR
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

ITAD_RSS_URL = "https://isthereanydeal.com/feeds/US/USD/deals.rss?filter=N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA%3D%3D"

# ===== CORE BUSINESS LOGIC =====
class ITADSource:
    """Fetches deals from IsThereAnyDeal.com using its RSS feed, fetched via Playwright for reliability."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = 900): # Session is not used here but kept for signature consistency
        self.rss_url = ITAD_RSS_URL
        # We don't need BaseWebClient's caching as Playwright will always fetch fresh data
        # Caching logic could be added here if needed, but for now, we prioritize freshness.
        logger.debug(f"[{self.__class__.__name__}] Initialized.")

    async def _fetch_rss_with_playwright(self) -> Optional[str]:
        """Fetches the raw RSS content using Playwright to appear as a real browser."""
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                logger.info(f"🚀 [{self.__class__.__name__}] Navigating to RSS feed with Playwright: {self.rss_url}")
                # Go directly to the RSS feed URL
                response = await page.goto(self.rss_url, wait_until='domcontentloaded', timeout=45000)
                if response and response.ok:
                    content = await response.text()
                    logger.info(f"✅ [{self.__class__.__name__}] Successfully fetched RSS content via Playwright.")
                    return content
                else:
                    logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch RSS feed, status: {response.status if response else 'N/A'}")
                    return None
        except TimeoutError:
            logger.error(f"❌ [{self.__class__.__name__}] Playwright timed out while fetching RSS feed.")
            return None
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] An unexpected error occurred during Playwright fetch: {e}", exc_info=True)
            return None
        finally:
            if browser:
                await browser.close()

    def _parse_item_description(self, description_html: str) -> Optional[Dict]:
        """Parses the HTML content within the <description> tag."""
        try:
            soup = BeautifulSoup(description_html, 'lxml')
            discount_tag = soup.find('i')
            discount_text = discount_tag.get_text(strip=True).replace('(', '').replace(')', '') if discount_tag else ""
            is_free = "-100%" in discount_text
            store_tag = soup.find('a')
            if not store_tag or not store_tag.has_attr('href'): return None
            store_name = store_tag.get_text(strip=True)
            deal_url = store_tag['href']
            return {
                "store": store_name.lower().replace(" ", ""),
                "url": deal_url,
                "is_free": is_free,
                "discount_text": discount_text.replace('-', '') if not is_free else "100% Off"
            }
        except Exception: return None

    def _parse_rss_item(self, item_element: ET.Element) -> Optional[GameData]:
        """Parses a single <item> from the RSS feed into GameData."""
        try:
            guid_tag = item_element.find('guid')
            title_tag = item_element.find('title')
            description_tag = item_element.find('description')
            if not all([guid_tag is not None, title_tag is not None, description_tag is not None, title_tag.text, guid_tag.text, description_tag.text]): return None
            
            deal_details = self._parse_item_description(description_tag.text)
            if not deal_details: return None

            return GameData(title=title_tag.text, id_in_db=guid_tag.text, **deal_details)
        except Exception: return None

    async def fetch_free_games(self) -> List[GameData]:
        """Main method to fetch all deals from the ITAD RSS feed."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from RSS feed using Playwright...")
        
        rss_content = await self._fetch_rss_with_playwright()
        if not rss_content:
            return []

        try:
            root = ET.fromstring(rss_content)
            items = root.findall('.//channel/item')
            logger.info(f"[{self.__class__.__name__}] Found {len(items)} items in the RSS feed.")
            
            found_games: List[GameData] = [game for item in items if (game := self._parse_rss_item(item)) is not None]
            
            logger.info(f"✅ [{self.__class__.__name__}] Successfully parsed {len(found_games)} total deals from RSS feed.")
            return found_games
        except ET.ParseError as e:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to parse XML from RSS feed: {e}", exc_info=True)
            return []