// ===== IMPORTS & DEPENDENCIES =====
import logging
from typing import Optional

import aiohttp

from src.config import GOOGLE_TRANSLATE_URL, MYMEMORY_API_URL

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class SmartTranslator:
    """
    A simple class for translating text using free public APIs.
    It attempts translation with Google Translate first, falling back to MyMemory.
    """

    async def _translate_with_service(self, session: aiohttp.ClientSession, url: str, params: dict) -> Optional[str]:
        """A generic method to attempt translation with a given service."""
        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                
                if "translate.googleapis.com" in url:
                    return "".join([item[0] for item in data[0] if item[0]])
                elif "api.mymemory.translated.net" in url:
                    if data.get("responseStatus") == 200:
                        return data["responseData"]["translatedText"]
                    else:
                        logger.warning(f"MyMemory API error: {data.get('responseDetails')}")
                        return None
        except Exception as e:
            logger.warning(f"Translation service at {url} failed: {e}")
            return None
        return None

    async def translate(self, text: str) -> str:
        """
        Translates an English text to Persian.

        Args:
            text (str): The English text to translate.

        Returns:
            str: The translated Persian text, or the original English text if all attempts fail.
        """
        if not text or not text.strip():
            return ""

        logger.debug(f"[{self.__class__.__name__}] Attempting to translate: '{text[:70]}...'")
        
        async with aiohttp.ClientSession() as session:
            # 1. Try Google Translate
            google_params = {'client': 'gtx', 'sl': 'en', 'tl': 'fa', 'dt': 't', 'q': text}
            translated_text = await self._translate_with_service(session, GOOGLE_TRANSLATE_URL, google_params)
            if translated_text:
                logger.debug(f"[{self.__class__.__name__}] Translation successful with Google Translate.")
                return translated_text

            # 2. Fallback to MyMemory
            logger.info(f"[{self.__class__.__name__}] Google Translate failed, falling back to MyMemory.")
            mymemory_params = {"q": text, "langpair": "en|fa"}
            translated_text = await self._translate_with_service(session, MYMEMORY_API_URL, mymemory_params)
            if translated_text:
                logger.debug(f"[{self.__class__.__name__}] Translation successful with MyMemory.")
                return translated_text

            # 3. If all fails, return original text
            logger.error(f"❌ [{self.__class__.__name__}] All translation services failed for text. Returning original.")
            return text
