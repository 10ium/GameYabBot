# ===== IMPORTS & DEPENDENCIES =====
import logging
import aiohttp
from typing import Optional
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from src.core.base_client import BaseWebClient
from src.config import GOOGLE_TRANSLATE_URL, MYMEMORY_API_URL, DEFAULT_CACHE_TTL, CACHE_DIR
import os

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class SmartTranslator(BaseWebClient):
    """
    A smart translator that uses free public APIs with fallbacks.
    It cleans HTML and truncates text before translation.
    """
    def __init__(self, session: aiohttp.ClientSession, cache_ttl: int = DEFAULT_CACHE_TTL * 30): # Longer TTL for translations
        super().__init__(
            cache_dir=os.path.join(CACHE_DIR, "translations"),
            cache_ttl=cache_ttl,
            session=session
        )

    def _clean_and_truncate_text(self, html_text: str, max_length: int = 1000) -> str:
        """Removes HTML tags and truncates text to a safe length for GET requests."""
        if not html_text:
            return ""
        
        # Use BeautifulSoup to get clean text
        soup = BeautifulSoup(html_text, 'lxml')
        clean_text = soup.get_text(separator=' ', strip=True)
        
        # Truncate to avoid URI too long errors
        if len(clean_text) > max_length:
            # Truncate at the last full word
            clean_text = clean_text[:max_length].rsplit(' ', 1)[0] + '...'
        
        return clean_text

    async def _translate_with_google(self, text: str) -> Optional[str]:
        """Translates text using the public Google Translate API."""
        params = {'client': 'gtx', 'sl': 'en', 'tl': 'fa', 'dt': 't', 'q': text}
        try:
            async with self._session.get(GOOGLE_TRANSLATE_URL, params=params, timeout=15) as response:
                response.raise_for_status()
                data = await response.json()
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
        try:
            full_url = f"{MYMEMORY_API_URL}?{urlencode(params)}"
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
        
        # **CRITICAL FIX**: Clean and truncate the text before doing anything else.
        clean_text_to_translate = self._clean_and_truncate_text(text)
        if not clean_text_to_translate:
            return "" # Return empty if text was only HTML tags

        # Check cache using the cleaned text as the key
        cache_path = self._get_cache_path(clean_text_to_translate, extension="txt")
        if self._is_cache_valid(cache_path):
             with open(cache_path, 'r', encoding='utf-8') as f:
                logger.info(f"✅ [{self.__class__.__name__}] Loading translation from cache for: '{clean_text_to_translate[:30]}...'")
                return f.read()

        logger.info(f"➡️ [{self.__class__.__name__}] Translating text: '{clean_text_to_translate[:50]}...'")
        
        translated_text = None
        
        # Attempt 1: Google Translate
        translated_text = await self._translate_with_google(clean_text_to_translate)
        if translated_text:
            logger.info("✅ Translation successful with Google Translate.")
        else:
            # Attempt 2: MyMemory
            logger.info("⚠️ Google failed, trying MyMemory as fallback...")
            translated_text = await self._translate_with_mymemory(clean_text_to_translate)
            if translated_text:
                logger.info("✅ Translation successful with MyMemory.")
            else:
                logger.error(f"❌ All translation attempts failed for: '{clean_text_to_translate[:50]}...'. Returning original text.")
                return clean_text_to_translate # Fallback to the cleaned original text

        # Save successful translation to cache
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(translated_text)

        return translated_text