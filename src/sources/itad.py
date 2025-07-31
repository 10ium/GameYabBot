# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import DEFAULT_CACHE_TTL, CACHE_DIR
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# The new, reliable RSS feed URL for IsThereAnyDeal
ITAD_RSS_URL = "https://isthereanydeal.com/feeds/US/USD/deals.rss?filter=N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA%253D%253D"

# ===== CORE BUSINESS LOGIC =====
class ITADSource(BaseWebClient):
    """Fetches deals from IsThereAnyDeal.com using its reliable RSS feed."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        # Override cache_ttl to be shorter for RSS feeds as deals change frequently
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "itad_rss"),
            cache_ttl=900,  # 15 minutes
            session=session
        )
        self.rss_url = ITAD_RSS_URL

    def _parse_item_description(self, description_html: str) -> Optional[Dict]:
        """Parses the HTML content within the <description> tag to extract deal details."""
        try:
            soup = BeautifulSoup(description_html, 'lxml')
            
            # Extract discount percentage
            discount_tag = soup.find('i')
            discount_text = discount_tag.get_text(strip=True).replace('(', '').replace(')', '') if discount_tag else ""
            is_free = "-100%" in discount_text

            # Extract store name and URL
            store_tag = soup.find('a')
            if not store_tag or not store_tag.has_attr('href'):
                logger.warning(f"[{self.__class__.__name__}] Could not find a valid store link in description: {description_html}")
                return None
            
            store_name = store_tag.get_text(strip=True)
            deal_url = store_tag['href']

            return {
                "store": store_name.lower().replace(" ", ""),  # Normalize store name e.g., "Epic Game Store" -> "epicgamestore"
                "url": deal_url,
                "is_free": is_free,
                "discount_text": discount_text.replace('-', '') if not is_free else "100% Off"
            }
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to parse item description: {description_html}. Error: {e}", exc_info=True)
            return None

    def _parse_rss_item(self, item_element: ET.Element) -> Optional[GameData]:
        """Parses a single <item> from the RSS feed into GameData."""
        try:
            guid_tag = item_element.find('guid')
            title_tag = item_element.find('title')
            description_tag = item_element.find('description')

            if not all([guid_tag is not None, title_tag is not None, description_tag is not None, title_tag.text, guid_tag.text, description_tag.text]):
                logger.debug(f"[{self.__class__.__name__}] Skipping malformed RSS item (missing title, guid, or description).")
                return None

            title = title_tag.text
            guid = guid_tag.text
            description_html = description_tag.text

            deal_details = self._parse_item_description(description_html)
            if not deal_details:
                logger.warning(f"[{self.__class__.__name__}] Could not parse deal details for '{title}'.")
                return None

            # Create a GameData object by merging the dictionaries
            game_info: GameData = {
                "title": title,
                "id_in_db": guid,
                **deal_details
            }
            return game_info
            
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] Error parsing RSS item element. Error: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[GameData]:
        """Main method to fetch all deals from the ITAD RSS feed."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch from RSS feed...")
        
        rss_content = await self._fetch(self.rss_url, is_json=False)
        if not rss_content:
            logger.error(f"❌ [{self.__class__.__name__}] Could not retrieve RSS content from {self.rss_url}.")
            return []

        try:
            root = ET.fromstring(rss_content)
            items = root.findall('.//channel/item')
            logger.info(f"[{self.__class__.__name__}] Found {len(items)} items in the RSS feed.")
            
            found_games: List[GameData] = []
            for item_element in items:
                game_data = self._parse_rss_item(item_element)
                if game_data:
                    found_games.append(game_data)
            
            logger.info(f"✅ [{self.__class__.__name__}] Successfully parsed {len(found_games)} total deals from RSS feed.")
            return found_games
            
        except ET.ParseError as e:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to parse XML from RSS feed: {e}", exc_info=True)
            return []