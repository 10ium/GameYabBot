// ===== IMPORTS & DEPENDENCIES =====
import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import (
    METACRITIC_BASE_URL,
    METACRITIC_SEARCH_URL,
    CACHE_DIR,
    DEFAULT_CACHE_TTL
)
from src.utils.clean_title import clean_title_for_search

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class MetacriticEnricher(BaseWebClient):
    """Enriches game data with critic and user scores from Metacritic."""

    def __init__(self, session: aiohttp.ClientSession):
        cache_path = f"{CACHE_DIR}/metacritic"
        super().__init__(cache_dir=cache_path, cache_ttl=DEFAULT_CACHE_TTL, session=session)

    async def _get_game_page_url(self, game_title: str) -> Optional[str]:
        """Searches Metacritic and returns the URL of the most likely game page."""
        cleaned_title = clean_title_for_search(game_title)
        if not cleaned_title:
            return None
        
        # Metacritic search uses spaces, not hyphens
        search_query = cleaned_title.replace('-', ' ')
        search_url = METACRITIC_SEARCH_URL.format(query=search_query)
        logger.debug(f"[{self.__class__.__name__}] Searching Metacritic for '{game_title}' at {search_url}")

        html_content = await self._fetch(search_url, is_json=False)
        if not html_content or not isinstance(html_content, str):
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # Updated selector for Metacritic's search results
        first_result = soup.select_one("a.c-finderProductCard")
        
        if first_result and first_result.get('href'):
            game_page_path = first_result['href']
            return f"{METACRITIC_BASE_URL}{game_page_path}"
        
        logger.warning(f"⚠️ [{self.__class__.__name__}] No search results found for '{game_title}' on Metacritic.")
        return None

    async def enrich_data(self, game: GameData) -> GameData:
        """
        Enriches a GameData object with scores from Metacritic.
        """
        game_title = game.get('title', 'N/A')
        game_page_url = await self._get_game_page_url(game_title)

        if not game_page_url:
            return game # Could not find game page, return original data

        logger.info(f"[{self.__class__.__name__}] Enriching '{game_title}' using Metacritic page: {game_page_url}")
        game_page_html = await self._fetch(game_page_url, is_json=False)

        if not game_page_html or not isinstance(game_page_html, str):
            return game
        
        game_soup = BeautifulSoup(game_page_html, 'html.parser')
        
        # Extract critic score
        critic_score_tag = game_soup.select_one("div.c-siteReviewScore_background-positive, div.c-siteReviewScore_background-mixed, div.c-siteReviewScore_background-negative")
        if critic_score_tag and critic_score_tag.text.strip().isdigit():
            game['metacritic_score'] = int(critic_score_tag.text.strip())
            logger.debug(f"[{self.__class__.__name__}] Found critic score for '{game_title}': {game['metacritic_score']}")

        # Extract user score
        user_score_tag = game_soup.select_one("div.c-siteReviewScore.u-flexbox-column.g-text-bold.u-text-uppercase > div")
        if user_score_tag and user_score_tag.text.strip() != 'tbd':
            try:
                game['metacritic_userscore'] = float(user_score_tag.text.strip())
                logger.debug(f"[{self.__class__.__name__}] Found user score for '{game_title}': {game['metacritic_userscore']}")
            except ValueError:
                logger.warning(f"[{self.__class__.__name__}] Could not parse user score '{user_score_tag.text.strip()}' for '{game_title}'.")
        
        logger.info(f"✅ [{self.__class__.__name__}] Successfully enriched data for '{game_title}'.")
        return game
