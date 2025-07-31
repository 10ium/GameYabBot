# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import EPIC_GAMES_API_URL, EPIC_GAMES_HEADERS, DEFAULT_CACHE_TTL, CACHE_DIR
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class EpicGamesSource(BaseWebClient):
    """Fetches free games from the Epic Games Store using its GraphQL API."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "epic_games"),
            cache_ttl=cache_ttl,
            session=session
        )

    def _normalize_game_data(self, game_element: Dict[str, Any]) -> Optional[GameData]:
        """
        Transforms the raw API data for a single game into the standardized GameData format.
        """
        try:
            title = game_element.get('title', 'بدون عنوان')
            game_id = game_element.get('id')
            if not title or not game_id:
                logger.warning(f"[{self.__class__.__name__}] Skipping item with missing title or id.")
                return None

            # Find the best available image URL
            image_url = ""
            key_images = game_element.get('keyImages', [])
            for img_type in ['OfferImageWide', 'VaultHandout', 'OfferImageTall', 'DieselStoreFrontWide']:
                for img in key_images:
                    if img.get('type') == img_type:
                        image_url = img.get('url')
                        break
                if image_url:
                    break
            
            # Construct the product URL from the slug
            product_slug = game_element.get('productSlug') or game_element.get('urlSlug')
            if product_slug:
                # Clean up slug if it contains '/home'
                product_slug = product_slug.replace('/home', '')
                url = f"https://www.epicgames.com/store/p/{product_slug}"
            else:
                url = "https://www.epicgames.com/store/en-US/"
                logger.warning(f"[{self.__class__.__name__}] Product slug not found for '{title}'. Using generic store URL.")

            normalized_data: GameData = {
                "title": title,
                "store": "epicgames",
                "url": url,
                "id_in_db": f"epic_{game_id}",
                "is_free": True,
                "discount_text": "100% Off",
                "description": game_element.get('description'),
                "image_url": image_url,
                "productSlug": product_slug
            }
            return normalized_data
            
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"❌ [{self.__class__.__name__}] Error normalizing Epic Games data for item ID: {game_element.get('id', 'N/A')}. Error: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[GameData]:
        """
        Fetches the list of currently free games from Epic Games.
        """
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch...")
        
        query = """
            query searchStoreQuery($country: String!, $locale: String, $category: String) {
                Catalog {
                    searchStore(country: $country, locale: $locale, category: $category) {
                        elements {
                            title
                            id
                            description
                            productSlug
                            urlSlug
                            keyImages { type url }
                            promotions(category: $category) {
                                promotionalOffers {
                                    promotionalOffers {
                                        startDate
                                        endDate
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """
        variables = {"country": "US", "locale": "en-US", "category": "freegames"}
        payload = {"query": query, "variables": variables}
        
        response_data = await self._fetch(EPIC_GAMES_API_URL, method='POST', payload=payload, headers=EPIC_GAMES_HEADERS)
        if not response_data:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch data from Epic Games API.")
            return []

        games = response_data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
        logger.info(f"[{self.__class__.__name__}] Received {len(games)} raw elements from API.")
        
        now = datetime.now(timezone.utc)
        free_games_list: List[GameData] = []
        
        for game in games:
            promotions = game.get('promotions')
            if not promotions or not promotions.get('promotionalOffers'):
                continue
            
            offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
            is_active_free_offer = any(
                datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00')) <= now <= datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                for offer in offers if 'startDate' in offer and 'endDate' in offer
            )

            if is_active_free_offer:
                normalized_game = self._normalize_game_data(game)
                if normalized_game:
                    free_games_list.append(normalized_game)
                    logger.info(f"✅ [{self.__class__.__name__}] Found active free game: {normalized_game['title']}")
        
        if not free_games_list:
            logger.info(f"ℹ️ [{self.__class__.__name__}] No active free games found at this time.")
            
        return free_games_list