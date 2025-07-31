// ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import re
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import METACRITIC_BASE_URL, METACRITIC_SEARCH_URL, DEFAULT_CACHE_TTL, CACHE_DIR
from src.utils.clean_title import clean_title_for_search
import os

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class MetacriticEnricher(BaseWebClient):
    """Enriches game data with critic and user scores from Metacritic."""

    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL):
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "metacritic"),
            cache_ttl=cache_ttl,
            session=session
        )

    async def _find_game_page_url(self, game_title: str) -> Optional[str]:
        """Searches Metacritic and returns the URL of the first game result."""
        cleaned_title = clean_title_for_search(game_title)
        # Metacritic search uses spaces, not hyphens
        search_query = re.sub(r'\s+', ' ', cleaned_title).strip()
        if not search_query:
            return None
        
        search_url = METACRITIC_SEARCH_URL.format(query=search_query)
        logger.info(f"[{self.__class__.__name__}] Searching Metacritic for '{search_query}' at {search_url}")
        
        html_content = await self._fetch(search_url, is_json=False)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        # Modern Metacritic uses 'search-results-container'
        results_container = soup.find('div', id='main-content')
        if not results_container:
            logger.warning(f"[{self.__class__.__name__}] Could not find search results container on Metacritic for '{search_query}'.")
            return None
            
        # The first link to a game page is usually the best match
        first_result_link = results_container.select_one('a[href^="/game/"]')
        if first_result_link:
            game_page_path = first_result_link['href']
            full_url = METACRITIC_BASE_URL + game_page_path
            logger.info(f"✅ [{self.__class__.__name__}] Found Metacritic page URL: {full_url}")
            return full_url
            
        logger.warning(f"⚠️ [{self.__class__.__name__}] No game page link found in Metacritic search results for '{search_query}'.")
        return None

    def _parse_scores_from_page(self, html_content: str) -> GameData:
        """Parses the critic and user scores from a Metacritic game page."""
        soup = BeautifulSoup(html_content, 'html.parser')
        scores: GameData = {}
        
        # Critic Score
        critic_score_tag = soup.select_one('[data-testid="metascore-value"]')
        if critic_score_tag and critic_score_tag.text.strip().isdigit():
            scores['metacritic_score'] = int(critic_score_tag.text.strip())
        
        # User Score
        user_score_tag = soup.select_one('div[data-testid="userscore-value"] > span')
        if user_score_tag and user_score_tag.text.strip().replace('.', '', 1).isdigit():
            scores['metacritic_userscore'] = float(user_score_tag.text.strip())
            
        return scores

    async def enrich(self, game_data: GameData) -> GameData:
        """
        Public method to enrich a single GameData object with Metacritic scores.
        """
        title = game_data.get('title')
        if not title:
            return game_data

        game_page_url = await self._find_game_page_url(title)
        if not game_page_url:
            return game_data
            
        page_html = await self._fetch(game_page_url, is_json=False)
        if not page_html:
            return game_data
            
        scores = self._parse_scores_from_page(page_html)
        if scores:
            game_data.update(scores)
            logger.info(f"✅ [{self.__class__.__name__}] Successfully enriched '{title}' with scores: {scores}")

        return game_data