# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import re
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import STEAM_API_URL, STEAM_SEARCH_URL, DEFAULT_CACHE_TTL, CACHE_DIR
from src.utils.game_utils import clean_title # Changed from clean_title_for_search to clean_title for consistency
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class SteamEnricher(BaseWebClient):
    """Enriches game data with information from Steam's API and store pages."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "steam"),
            cache_ttl=cache_ttl,
            session=session
        )

    async def _find_app_id(self, game_title: str) -> Optional[str]:
        """Searches the Steam store to find the App ID for a given game title."""
        cleaned_title = clean_title(game_title) # Using the new clean_title
        if not cleaned_title:
            logger.debug(f"[{self.__class__.__name__}] Cleaned title for Steam search is empty for '{game_title}'.")
            return None

        search_url = STEAM_SEARCH_URL.format(query=cleaned_title)
        logger.info(f"[{self.__class__.__name__}] Searching for App ID for '{cleaned_title}' at {search_url}")
        
        html_content = await self._fetch(search_url, is_json=False)
        if not html_content:
            logger.warning(f"[{self.__class__.__name__}] Failed to fetch Steam search results for '{cleaned_title}'.")
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # The search results are in <a> tags with a data-ds-appid attribute
        first_result = soup.select_one('a.search_result_row[data-ds-appid]')
        
        if first_result:
            app_id = first_result['data-ds-appid']
            logger.info(f"✅ [{self.__class__.__name__}] Found App ID '{app_id}' for title '{cleaned_title}'.")
            return app_id
        
        logger.warning(f"⚠️ [{self.__class__.__name__}] No App ID found for title '{cleaned_title}' in search results.")
        return None

    def _parse_steam_api_response(self, app_id: str, response_data: Dict[str, Any]) -> Optional[GameData]:
        """Parses the JSON response from the Steam API into the GameData format."""
        if not response_data or not response_data.get(app_id, {}).get('success'):
            logger.warning(f"[{self.__class__.__name__}] Steam API response for App ID {app_id} was unsuccessful or empty.")
            return None
        
        details = response_data[app_id]['data']
        enriched_data: GameData = {}
        
        # Core metadata
        enriched_data['description'] = details.get('about_the_game')
        enriched_data['image_url'] = details.get('header_image')
        enriched_data['genres'] = [genre['description'] for genre in details.get('genres', [])]

        # Player modes
        enriched_data['is_multiplayer'] = any(cat['description'] in ['Multi-player', 'Online Multi-Player', 'Co-op', 'Online Co-op'] for cat in details.get('categories', []))
        enriched_data['is_online'] = any(cat['description'] in ['Online Multi-Player', 'Online Co-op'] for cat in details.get('categories', []))

        # Trailer
        if details.get('movies'):
            # Prefer the highest quality webm trailer available
            trailer_options = details['movies'][0].get('webm', {})
            enriched_data['trailer'] = trailer_options.get('max') or trailer_options.get('480') or trailer_options.get('250')
        
        # Age rating (sometimes notes, sometimes required_age)
        enriched_data['age_rating'] = details.get('content_descriptors', {}).get('notes') or str(details.get('required_age')) if details.get('required_age') else None

        # Review scores
        recommendations = details.get('recommendations')
        if recommendations and 'total' in recommendations and 'positive' in recommendations:
            total_reviews = recommendations['total']
            positive_reviews = recommendations['positive']
            
            if total_reviews > 0:
                enriched_data['steam_overall_score'] = round((positive_reviews / total_reviews) * 100)
                enriched_data['steam_overall_reviews_count'] = total_reviews
                logger.debug(f"[{self.__class__.__name__}] Steam overall scores found for {app_id}.")
            else:
                logger.debug(f"[{self.__class__.__name__}] No total reviews for Steam app {app_id}.")
        else:
            logger.debug(f"[{self.__class__.__name__}] No recommendations data found for Steam app {app_id}.")


        # Steam API does not directly provide 'recent' review scores in this endpoint.
        # So we leave steam_recent_score and steam_recent_reviews_count as None or rely on other sources if available.
        
        return enriched_data

    async def enrich(self, game_data: GameData) -> GameData:
        """
        Public method to enrich a single GameData object with Steam data.
        It finds the App ID if not present, then fetches and parses API data.
        """
        title = game_data.get('title')
        if not title:
            logger.debug(f"[{self.__class__.__name__}] No title provided for Steam enrichment.")
            return game_data

        app_id = game_data.get('steam_app_id')
        if not app_id:
            # Only search for app ID if it seems to be a PC game
            store = game_data.get('store', '').lower()
            if store in ['steam', 'epicgames', 'gog', 'other', 'reddit', 'humblestore', 'fanatical', 'microsoftstore', 'amazon', 'blizzard', 'eastore', 'ubisoftstore', 'itch.io', 'indiegala', 'stove']:
                app_id = await self._find_app_id(title)
        
        if not app_id:
            logger.info(f"[{self.__class__.__name__}] No App ID found or inferrable for '{title}'. Skipping Steam enrichment.")
            return game_data
        
        # Add the found app_id to the game data immediately
        game_data['steam_app_id'] = app_id
        api_url = STEAM_API_URL.format(app_id=app_id)
        
        response_data = await self._fetch(api_url, is_json=True)
        if not response_data:
            logger.warning(f"[{self.__class__.__name__}] No response data from Steam API for App ID {app_id}.")
            return game_data
            
        parsed_data = self._parse_steam_api_response(app_id, response_data)
        if parsed_data:
            # Merge the new data into the existing game_data, preferring new non-empty values
            for key, value in parsed_data.items():
                # Special handling for lists like genres to merge them
                if isinstance(value, list) and key in game_data and isinstance(game_data[key], list):
                    game_data[key] = list(set(game_data[key] + value))
                elif value is not None and value != '': # Ensure empty strings are not preferred over None
                    game_data[key] = value
            logger.info(f"✅ [{self.__class__.__name__}] Successfully enriched '{title}' (App ID: {app_id}) with Steam data.")
        else:
            logger.info(f"[{self.__class__.__name__}] Failed to parse Steam API response for '{title}' (App ID: {app_id}).")

        return game_data