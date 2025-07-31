# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import re
from typing import Optional
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import METACRITIC_BASE_URL, METACRITIC_SEARCH_URL, DEFAULT_CACHE_TTL, CACHE_DIR
from src.utils.game_utils import clean_title
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
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
        cleaned_title = clean_title(game_title)
        search_query = re.sub(r'\s+', ' ', cleaned_title).strip()
        if not search_query:
            return None
        
        search_url = METACRITIC_SEARCH_URL.format(query=search_query)
        logger.info(f"[{self.__class__.__name__}] Searching Metacritic for '{search_query}'")
        
        html_content = await self._fetch(search_url, is_json=False)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, 'lxml')
        results_container = soup.find('div', id='main-content')
        if not results_container:
            logger.warning(f"[{self.__class__.__name__}] Could not find main content container on Metacritic for '{search_query}'.")
            return None
            
        first_result_link = results_container.select_one('a[href^="/game/"]')
        if first_result_link:
            game_page_path = first_result_link['href']
            full_url = METACRITIC_BASE_URL + game_page_path
            logger.info(f"✅ [{self.__class__.__name__}] Found Metacritic page URL: {full_url}")
            return full_url
            
        logger.warning(f"⚠️ [{self.__class__.__name__}] No game link found in Metacritic search results for '{search_query}'.")
        return None

    def _parse_scores_from_page(self, html_content: str, title: str) -> GameData:
        """Parses the critic and user scores from a Metacritic game page."""
        soup = BeautifulSoup(html_content, 'lxml')
        scores: GameData = {}
        
        # Critic Score
        critic_score_tag = soup.select_one('[data-testid="metascore-value"]')
        if critic_score_tag:
            score_text = critic_score_tag.text.strip()
            if score_text.isdigit():
                scores['metacritic_score'] = int(score_text)
                logger.debug(f"[Metacritic Parser] Found critic score for '{title}': {score_text}")
            else:
                logger.debug(f"[Metacritic Parser] Critic score tag for '{title}' found, but content is not a digit: '{score_text}'")
        else:
            logger.debug(f"[Metacritic Parser] Critic score tag '[data-testid=\"metascore-value\"]' not found for '{title}'.")
        
        # User Score
        user_score_tag = soup.select_one('div[data-testid="userscore-value"] > span')
        if user_score_tag:
            score_text = user_score_tag.text.strip()
            if re.match(r"^\d+(\.\d+)?$", score_text):
                scores['metacritic_userscore'] = float(score_text)
                logger.debug(f"[Metacritic Parser] Found user score for '{title}': {score_text}")
            else:
                 logger.debug(f"[Metacritic Parser] User score tag for '{title}' found, but content is not a float: '{score_text}'")
        else:
            logger.debug(f"[Metacritic Parser] User score tag 'div[data-testid=\"userscore-value\"] > span' not found for '{title}'.")
            
        return scores

    async def enrich(self, game_data: GameData) -> GameData:
        """Enriches a single GameData object with Metacritic scores."""
        title = game_data.get('title')
        if not title:
            return game_data

        game_page_url = await self._find_game_page_url(title)
        if not game_page_url:
            return game_data
            
        page_html = await self._fetch(game_page_url, is_json=False)
        if not page_html:
            return game_data
            
        scores = self._parse_scores_from_page(page_html, title)
        if scores:
            game_data.update(scores)
            logger.info(f"✅ [{self.__class__.__name__}] Successfully enriched '{title}' with Metacritic scores: {scores}")

        return game_data