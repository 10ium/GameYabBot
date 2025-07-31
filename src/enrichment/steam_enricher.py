// ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import (
    STEAM_API_URL,
    STEAM_SEARCH_URL,
    CACHE_DIR,
    DEFAULT_CACHE_TTL
)
from src.utils.clean_title import clean_title_for_search

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class SteamEnricher(BaseWebClient):
    """Enriches game data with information from Steam, including reviews and metadata."""

    def __init__(self, session: aiohttp.ClientSession):
        cache_path = f"{CACHE_DIR}/steam"
        super().__init__(cache_dir=cache_path, cache_ttl=DEFAULT_CACHE_TTL, session=session)

    async def _find_steam_app_id(self, game_title: str) -> Optional[str]:
        """Finds the Steam App ID by searching for the game title."""
        cleaned_title = clean_title_for_search(game_title)
        if not cleaned_title:
            return None

        search_url = STEAM_SEARCH_URL.format(query=cleaned_title)
        logger.debug(f"[{self.__class__.__name__}] Searching for App ID for '{game_title}' at {search_url}")

        html_content = await self._fetch(search_url, is_json=False)
        if not html_content or not isinstance(html_content, str):
            logger.warning(f"[{self.__class__.__name__}] Failed to fetch or received non-HTML content from Steam search for '{game_title}'.")
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        first_result = soup.select_one('a.search_result_row[data-ds-appid]')
        
        if first_result and first_result['data-ds-appid']:
            app_id = first_result['data-ds-appid']
            logger.info(f"✅ [{self.__class__.__name__}] Found App ID for '{game_title}': {app_id}")
            return app_id
        
        logger.warning(f"⚠️ [{self.__class__.__name__}] Could not find App ID for '{game_title}' in search results.")
        return None

    async def enrich_data(self, game: GameData) -> GameData:
        """
        Enriches a GameData object with details from the Steam API.
        It prioritizes an existing app_id but will search if one isn't present.
        """
        game_title = game.get('title', 'N/A')
        app_id = game.get('steam_app_id')

        # Only enrich if the game is from a relevant store or already has an app_id
        if game.get('store') not in ['steam', 'epicgames', 'other', 'reddit'] and not app_id:
            logger.debug(f"[{self.__class__.__name__}] Skipping Steam enrichment for '{game_title}' from store '{game.get('store')}'.")
            return game

        if not app_id:
            app_id = await self._find_steam_app_id(game_title)
            if not app_id:
                return game # No App ID found, nothing to enrich
            game['steam_app_id'] = app_id

        api_url = STEAM_API_URL.format(app_id=app_id)
        logger.info(f"[{self.__class__.__name__}] Enriching '{game_title}' using Steam API for App ID {app_id}")
        
        api_data = await self._fetch(api_url, is_json=True)

        if not api_data or not api_data.get(str(app_id), {}).get('success'):
            logger.warning(f"⚠️ [{self.__class__.__name__}] Failed to get successful API response for App ID {app_id}.")
            return game
            
        details = api_data[str(app_id)]['data']

        # Prioritize more detailed data from Steam
        game['description'] = details.get('about_the_game', game.get('description'))
        if not game.get('image_url'): # Only set image if not already present
            game['image_url'] = details.get('header_image')
        
        game['genres'] = [g['description'] for g in details.get('genres', [])]
        
        # Player categorization
        is_multiplayer = False
        is_online = False
        if details.get('categories'):
            for category in details['categories']:
                cat_desc = category.get('description', '').lower()
                if 'multi-player' in cat_desc or 'co-op' in cat_desc:
                    is_multiplayer = True
                if 'online' in cat_desc:
                    is_online = True
        game['is_multiplayer'] = is_multiplayer
        game['is_online'] = is_online

        if details.get('movies'):
            game['trailer'] = details['movies'][0].get('webm', {}).get('max', '')

        game['age_rating'] = details.get('required_age') or (details.get('content_descriptors', {}).get('notes'))
        
        # Steam review scores
        if details.get('recommendations'):
            total_reviews = details['recommendations'].get('total')
            game['steam_overall_reviews_count'] = total_reviews
            # Steam API only provides total recommendations, not positives.
            # This part of the logic needs to be re-evaluated or sourced differently.
            # For now, we'll leave the score calculation part out as the API doesn't directly support it.
            # A different endpoint or scraping might be needed for percentage.
        
        logger.info(f"✅ [{self.__class__.__name__}] Successfully enriched data for '{game_title}'.")
        return game
