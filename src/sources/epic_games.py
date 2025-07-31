// ===== IMPORTS & DEPENDENCIES =====
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import aiohttp

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import (
    EPIC_GAMES_API_URL,
    EPIC_GAMES_HEADERS,
    CACHE_DIR,
    DEFAULT_CACHE_TTL
)

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class EpicGamesSource(BaseWebClient):
    """Fetches free games from the Epic Games Store using their GraphQL API."""

    def __init__(self, session: aiohttp.ClientSession):
        cache_path = f"{CACHE_DIR}/epic_games"
        super().__init__(cache_dir=cache_path, cache_ttl=DEFAULT_CACHE_TTL, session=session)

    def _normalize_game_data(self, game: Dict[str, Any]) -> Optional[GameData]:
        """Converts raw game data from Epic Games to the standard GameData format."""
        try:
            title = game.get('title', 'N/A')
            game_id = game.get('id')
            if not game_id:
                logger.warning(f"[{self.__class__.__name__}] Skipping game with missing ID: {title}")
                return None

            image_url = ""
            for img in game.get('keyImages', []):
                if img.get('type') in ('OfferImageWide', 'VaultHandout', 'OfferImageTall'):
                    image_url = img.get('url')
                    if img.get('type') == 'OfferImageWide':  # Prioritize the wide image
                        break
            
            # Extract product slug for URL generation
            product_slug = (
                game.get('productSlug') or 
                game.get('catalogNs', {}).get('mappings', [{}])[0].get('pageSlug') or 
                game.get('urlSlug')
            )
            if product_slug:
                product_slug = product_slug.replace('/home', '')
                url = f"https://www.epicgames.com/store/p/{product_slug}"
            else:
                logger.warning(f"[{self.__class__.__name__}] Could not determine URL for game: {title}")
                url = "https://www.epicgames.com/store/"

            return GameData(
                title=title,
                store="epicgames",
                url=url,
                image_url=image_url,
                description=game.get('description', ''),
                id_in_db=f"epic_{game_id}",
                is_free=True,
                productSlug=product_slug
            )
        except (KeyError, IndexError, TypeError) as e:
            logger.error(
                f"❌ [{self.__class__.__name__}] Error normalizing Epic Games data for game ID {game.get('id', 'N/A')}: {e}",
                exc_info=True
            )
            return None

    async def fetch_free_games(self) -> List[GameData]:
        """
        Fetches the list of currently free games from the Epic Games API.
        """
        logger.info(f"🚀 [{self.__class__.__name__}] Fetching free games from Epic Games...")
        
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
                            catalogNs { mappings(pageType: "productHome") { pageSlug } }
                            keyImages { type, url }
                            promotions(category: $category) {
                                promotionalOffers { promotionalOffers { startDate, endDate } }
                            }
                        }
                    }
                }
            }
        """
        variables = {"country": "US", "locale": "en-US", "category": "freegames"}
        payload = {"query": query, "variables": variables}

        data = await self._fetch(EPIC_GAMES_API_URL, method='POST', payload=payload, headers=EPIC_GAMES_HEADERS)
        if not data:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch data from Epic Games API.")
            return []

        games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
        logger.info(f"[{self.__class__.__name__}] Received {len(games)} potential games from API.")

        free_games_list: List[GameData] = []
        processed_ids = set()
        now = datetime.now(timezone.utc)

        for game in games:
            title = game.get('title', 'N/A')
            promotions = game.get('promotions')
            if not promotions or not promotions.get('promotionalOffers'):
                continue
            
            offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
            is_active_free_offer = False
            for offer in offers:
                try:
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                    if start_date <= now <= end_date:
                        is_active_free_offer = True
                        break
                except (ValueError, KeyError):
                    logger.warning(f"⚠️ [{self.__class__.__name__}] Could not parse offer dates for '{title}'.")
                    continue
            
            if is_active_free_offer:
                normalized_game = self._normalize_game_data(game)
                if normalized_game and normalized_game['id_in_db'] not in processed_ids:
                    free_games_list.append(normalized_game)
                    processed_ids.add(normalized_game['id_in_db'])
                    logger.info(f"✅ [{self.__class__.__name__}] Found active free game: {normalized_game['title']}")

        logger.info(f"✅ [{self.__class__.__name__}] Finished fetching. Found {len(free_games_list)} active free games.")
        return free_games_list
