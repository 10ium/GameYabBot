# ===== IMPORTS & DEPENDENCIES =====
import logging
import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from playwright.async_api import async_playwright, TimeoutError

from src.models.game import GameData
from src.config import EPIC_GAMES_API_URL

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class EpicGamesSource:
    """Fetches free games from the Epic Games Store using its GraphQL API via Playwright to bypass bot detection."""

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
                    if img.get('type') == img_type and img.get('url'):
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
                url = "https://www.epicgames.com/store/en-US/free-games"
                logger.warning(f"[{self.__class__.__name__}] Product slug not found for '{title}'. Using generic store URL.")

            return GameData(
                title=title,
                store="epicgames",
                url=url,
                id_in_db=f"epic_{game_id}",
                is_free=True,
                discount_text="100% Off",
                description=game_element.get('description'),
                image_url=image_url,
                productSlug=product_slug
            )
            
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"❌ [{self.__class__.__name__}] Error normalizing Epic Games data for item ID: {game_element.get('id', 'N/A')}. Error: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[GameData]:
        """Fetches the list of currently free games from Epic Games using Playwright."""
        logger.info(f"🚀 [{self.__class__.__name__}] Starting fetch with Playwright to avoid 403 errors...")
        
        query = """
            query searchStoreQuery($country: String!, $locale: String, $category: String) {
                Catalog {
                    searchStore(country: $country, locale: $locale, category: $category) {
                        elements {
                            title, id, description, productSlug, urlSlug,
                            keyImages { type, url },
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
        
        browser = None
        response_data = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                # Use page.evaluate to perform a fetch request from within the browser context.
                # This makes the request appear much more legitimate.
                response_json = await page.evaluate(f"""
                    () => fetch('{EPIC_GAMES_API_URL}', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({json.dumps(payload)})
                    }}).then(response => response.json())
                """)
                response_data = response_json
        except TimeoutError:
            logger.error(f"❌ [{self.__class__.__name__}] Playwright timed out during fetch.")
            return []
        except Exception as e:
            logger.error(f"❌ [{self.__class__.__name__}] An unexpected error occurred during Playwright fetch: {e}", exc_info=True)
            return []
        finally:
            if browser:
                await browser.close()

        if not response_data or 'data' not in response_data:
            logger.error(f"❌ [{self.__class__.__name__}] Failed to fetch valid data from Epic Games API using Playwright. Response: {response_data}")
            return []

        games = response_data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
        logger.info(f"[{self.__class__.__name__}] Received {len(games)} raw elements from API via Playwright.")
        
        now = datetime.now(timezone.utc)
        free_games_list: List[GameData] = []
        
        for game in games:
            promotions = game.get('promotions')
            if not promotions or not promotions.get('promotionalOffers'):
                continue
            
            offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
            is_active = any(
                'startDate' in o and 'endDate' in o and
                datetime.fromisoformat(o['startDate'].replace('Z', '+00:00')) <= now <= datetime.fromisoformat(o['endDate'].replace('Z', '+00:00'))
                for o in offers
            )

            if is_active:
                normalized_game = self._normalize_game_data(game)
                if normalized_game:
                    free_games_list.append(normalized_game)
                    logger.info(f"✅ [{self.__class__.__name__}] Found active free game: {normalized_game['title']}")
        
        return free_games_list