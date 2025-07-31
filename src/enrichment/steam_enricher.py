// ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import re
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import STEAM_API_URL, STEAM_SEARCH_URL, DEFAULT_CACHE_TTL, CACHE_DIR
from src.utils.clean_title import clean_title_for_search
import os

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
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
        cleaned_title = clean_title_for_search(game_title)
        if not cleaned_title:
            return None

        search_url = STEAM_SEARCH_URL.format(query=cleaned_title)
        logger.info(f"[{self.__class__.__name__}] Searching for App ID for '{cleaned_title}' at {search_url}")
        
        html_content = await self._fetch(search_url, is_json=False)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # The search results are in <a> tags with a data-ds-appid attribute
        first_result = soup.select_one('a.search_result_row[data-ds-appid]')
        
        if first_result:
            app_id = first_result['data-ds-appid']
            logger.info(f"✅ [{self.__class__.__name__}] Found App ID '{app_id}' for title '{cleaned_title}'.")
            return app_id
        
        logger.warning(f"⚠️ [{self.__class__.__name__}] No App ID found for title '{cleaned_title}'.")
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
        enriched_data['is_multiplayer'] = any(cat['description'] == 'Multi-player' for cat in details.get('categories', []))
        enriched_data['is_online'] = any(cat['description'] == 'Online Multi-Player' for cat in details.get('categories', []))

        # Trailer
        if details.get('movies'):
            # Prefer the highest quality webm trailer
            trailer = details['movies'][0]
            enriched_data['trailer'] = trailer.get('webm', {}).get('max') or trailer.get('webm', {}).get('480')
        
        # Age rating
        enriched_data['age_rating'] = details.get('required_age') or details.get('content_descriptors', {}).get('notes')

        # Review scores
        recommendations = details.get('recommendations')
        if recommendations and 'total' in recommendations:
            total_reviews = recommendations['total']
            # Steam API sometimes gives review summary text instead
            if 'review_score_desc' in details:
                 match = re.search(r'(\d+)% of the (\d+)', details['review_score_desc'])
                 if match:
                    enriched_data['steam_overall_score'] = int(match.group(1))
                    enriched_data['steam_overall_reviews_count'] = int(match.group(2).replace(',', ''))

        return enriched_data

    async def enrich(self, game_data: GameData) -> GameData:
        """
        Public method to enrich a single GameData object.
        It finds the App ID if not present, then fetches and parses API data.
        """
        title = game_data.get('title')
        if not title:
            return game_data

        app_id = game_data.get('steam_app_id')
        if not app_id:
            # Only search for app ID if it seems to be a PC game
            store = game_data.get('store', '').lower()
            if store in ['steam', 'epicgames', 'gog', 'other', 'reddit', 'humblestore', 'fanatical', 'microsoftstore']:
                app_id = await self._find_app_id(title)
        
        if not app_id:
            return game_data
        
        # Add the found app_id to the game data immediately
        game_data['steam_app_id'] = app_id
        api_url = STEAM_API_URL.format(app_id=app_id)
        
        response_data = await self._fetch(api_url, is_json=True)
        if not response_data:
            return game_data
            
        parsed_data = self._parse_steam_api_response(app_id, response_data)
        if parsed_data:
            # Merge the new data into the existing game_data, preferring new non-empty values
            for key, value in parsed_data.items():
                if value is not None and (not isinstance(value, list) or value):
                    game_data[key] = value

        return game_data