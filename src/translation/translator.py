# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
from typing import Optional

from src.core.base_client import BaseWebClient
from src.config import GOOGLE_TRANSLATE_URL, MYMEMORY_API_URL, DEFAULT_CACHE_TTL, CACHE_DIR
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class SmartTranslator(BaseWebClient):
    """
    A smart translator that uses free public APIs with fallbacks.
    It does not require an API key.
    """
    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL * 30): # Longer TTL for translations
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "translations"),
            cache_ttl=cache_ttl,
            session=session
        )
        
    async def _translate_with_google(self, text: str) -> Optional[str]:
        """Translates text using the public Google Translate API."""
        params = {'client': 'gtx', 'sl': 'en', 'tl': 'fa', 'dt': 't', 'q': text}
        # We cannot use self._fetch here directly as Google Translate API response format
        # is a bit unusual and needs specific parsing
        try:
            async with self._session.get(GOOGLE_TRANSLATE_URL, params=params, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                # Google's response is a nested list, we need to join the translated parts
                if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    translated_parts = [item[0] for item in data[0] if isinstance(item, list) and len(item) > 0 and isinstance(item[0], str)]
                    return "".join(translated_parts)
                logger.warning(f"[{self.__class__.__name__}] Unexpected Google Translate response format.")
                return None
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] Google Translate failed: {e}")
            return None

    async def _translate_with_mymemory(self, text: str) -> Optional[str]:
        """Translates text using the MyMemory API as a fallback."""
        params = {"q": text, "langpair": "en|fa"}
        # MyMemory API is a more standard JSON API, can use _fetch
        try:
            # Construct full URL with query parameters for caching to work
            full_url = f"{MYMEMORY_API_URL}?{aiohttp.helpers.urlencode(params)}"
            response_data = await self._fetch(full_url, is_json=True)
            
            if response_data and response_data.get("responseStatus") == 200:
                return response_data["responseData"]["translatedText"]
            logger.warning(f"[{self.__class__.__name__}] MyMemory API error: {response_data.get('responseDetails') if response_data else 'No response'}")
            return None
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] MyMemory request failed: {e}")
            return None

    async def translate(self, text: str) -> str:
        """
        Translates an English text to Persian, trying multiple services.
        Returns the original text if all translation attempts fail.
        """
        if not text or not text.strip():
            return ""

        # Check cache first (handled by _fetch for MyMemory, but we need manual for Google's custom URL)
        cache_path = self._get_cache_path(text, extension="txt")
        if self._is_cache_valid(cache_path):
             with open(cache_path, 'r', encoding='utf-8') as f:
                logger.info(f"✅ [{self.__class__.__name__}] Loading translation from cache for: '{text[:30]}...'")
                return f.read()

        logger.info(f"➡️ [{self.__class__.__name__}] Translating text: '{text[:50]}...'")
        
        translated_text = None
        
        # Attempt 1: Google Translate
        translated_text = await self._translate_with_google(text)
        if translated_text:
            logger.info("✅ Translation successful with Google Translate.")
        else:
            # Attempt 2: MyMemory
            logger.info("⚠️ Google failed, trying MyMemory as fallback...")
            translated_text = await self._translate_with_mymemory(text)
            if translated_text:
                logger.info("✅ Translation successful with MyMemory.")
            else:
                logger.error(f"❌ All translation attempts failed for: '{text[:50]}...'. Returning original text.")
                return text # Fallback to original text

        # Save successful translation to cache
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(translated_text)

        return translated_text