# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
import os
from typing import Optional, Dict
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.models.game import GameData
from src.config import DEFAULT_CACHE_TTL, CACHE_DIR

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# --- API Configuration ---
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
SERPAPI_URL = "https://serpapi.com/search.json"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

# Domains to blacklist for images (e.g., low-quality placeholders, trackers)
IMAGE_DOMAIN_BLACKLIST = ["gravatar.com", "avatar.com"]

# ===== CORE BUSINESS LOGIC =====
class ImageEnricher(BaseWebClient):
    """
    Enriches game data by finding the best possible image using a multi-layered cascade strategy,
    prioritizing free resources before falling back to paid APIs.
    """
    def __init__(self, session: aiohttp.ClientSession):
        # Use a long TTL for image lookups, as game art rarely changes.
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "image_enricher"),
            cache_ttl=DEFAULT_CACHE_TTL * 30, # 30-day cache
            session=session
        )

    def _is_valid_image_url(self, url: Optional[str]) -> bool:
        """A simple validator to check if the URL is a plausible image."""
        if not url or not url.startswith('http'):
            return False
        if any(blacklisted_domain in url for blacklisted_domain in IMAGE_DOMAIN_BLACKLIST):
            logger.debug(f"[ImageEnricher] URL '{url}' is blacklisted.")
            return False
        return True

    async def _get_from_itunes_api(self, query: str, entity: str) -> Optional[str]:
        """Fetches app/game artwork from the Apple iTunes Search API (free)."""
        params = {"term": query, "entity": entity, "limit": 1}
        full_url = f"{ITUNES_SEARCH_URL}?{urlencode(params)}"
        
        # Use our robust _fetch method for this API call
        data = await self._fetch(full_url, is_json=True)
        
        if data and data.get("resultCount", 0) > 0:
            result = data["results"][0]
            # iTunes provides multiple artwork sizes, get the highest resolution one
            artwork_url = result.get("artworkUrl512") or result.get("artworkUrl100")
            if self._is_valid_image_url(artwork_url):
                logger.info(f"✅ [ImageEnricher] Found high-quality image for '{query}' via iTunes API.")
                return artwork_url
        return None

    async def _get_from_serp_api(self, query: str) -> Optional[str]:
        """Fetches an image using SerpApi as a last resort."""
        if not SERPAPI_API_KEY:
            logger.debug("[ImageEnricher] SERPAPI_API_KEY not set. Skipping SerpApi search.")
            return None
        
        params = { "q": f"{query} game cover art", "tbm": "isch", "api_key": SERPAPI_API_KEY }
        # This is a direct API call, not using self._fetch to avoid caching the API key in the URL hash
        try:
            async with self._session.get(SERPAPI_URL, params=params, timeout=20) as response:
                response.raise_for_status()
                results = await response.json()
                
                if "images_results" in results and results["images_results"]:
                    for img in results["images_results"]:
                        img_url = img.get("original")
                        if self._is_valid_image_url(img_url) and img.get("original_width", 0) > img.get("original_height", 0):
                            logger.info(f"✅ [ImageEnricher] Found high-quality image for '{query}' via SerpApi.")
                            return img_url
                    # Fallback to the first valid image
                    first_valid = next((res["original"] for res in results["images_results"] if self._is_valid_image_url(res.get("original"))), None)
                    if first_valid:
                         logger.info(f"✅ [ImageEnricher] Found fallback image for '{query}' via SerpApi.")
                    return first_valid
            return None
        except Exception as e:
            logger.error(f"❌ [ImageEnricher] SerpApi search failed for query '{query}': {e}")
            return None

    async def _scrape_from_meta_tags(self, url: str) -> Optional[str]:
        """Scrapes the og:image or twitter:image meta tag from a URL."""
        html_content = await self._fetch(url, is_json=False)
        if not html_content:
            return None
        
        soup = BeautifulSoup(html_content, 'lxml')
        
        # Prioritize high-quality meta tags
        selectors = [
            "meta[property='og:image']",
            "meta[name='twitter:image']",
            "link[rel='apple-touch-icon']" # Good fallback for mobile apps
        ]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                image_url = tag["content"]
                if self._is_valid_image_url(image_url):
                    logger.info(f"✅ [ImageEnricher] Found '{selector}' meta tag image in {url}")
                    return image_url
        return None

    async def enrich(self, game: GameData, cleaned_title: str) -> GameData:
        """
        Public method to find the best image for a game if one is missing,
        following a cascade of strategies from free to paid.
        """
        # --- Stage 1: Check if a valid image already exists ---
        if self._is_valid_image_url(game.get("image_url")):
            logger.debug(f"[ImageEnricher] Skipping, valid image already exists for '{cleaned_title}'.")
            return game

        logger.info(f"🖼️ [ImageEnricher] No valid image for '{cleaned_title}'. Starting image search cascade...")
        image_url = None

        # --- Stage 2: Free, store-specific API lookups ---
        store = game.get('store', '')
        if store in ['iosappstore', 'ios']:
            image_url = await self._get_from_itunes_api(cleaned_title, "software")
        # Note: Google Play does not have a simple, reliable public API like iTunes.

        # --- Stage 3: Scrape meta tags from the deal URL (Free) ---
        if not image_url and game.get('url'):
            logger.debug("[ImageEnricher] Trying to scrape meta tags from deal URL.")
            image_url = await self._scrape_from_meta_tags(game['url'])

        # --- Stage 4: Paid API search (Last Resort) ---
        if not image_url:
            logger.debug("[ImageEnricher] Falling back to paid search API (SerpApi).")
            image_url = await self._get_from_serp_api(cleaned_title)

        # --- Stage 5: Final Assignment ---
        if self._is_valid_image_url(image_url):
            game["image_url"] = image_url
        else:
            logger.warning(f"⚠️ [ImageEnricher] All methods failed for '{cleaned_title}'. A placeholder will be used.")
            # No need to set a placeholder here; the front-end will handle it.

        return game