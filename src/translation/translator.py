import logging
import aiohttp
import json
from typing import Optional

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class SmartTranslator:
    """
    کلاسی برای ترجمه هوشمند متن با استفاده از سرویس‌های عمومی و رایگان.
    این نسخه نیازی به کلید API ندارد.
    """
    GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
    MYMEMORY_API_URL = "https://api.mymemory.translated.net/get"

    async def _translate_with_google(self, session: aiohttp.ClientSession, text: str) -> str:
        """ترجمه با استفاده از سرویس عمومی گوگل."""
        params = {
            'client': 'gtx',
            'sl': 'en',  # زبان مبدا: انگلیسی
            'tl': 'fa',  # زبان مقصد: فارسی
            'dt': 't',
            'q': text,
        }
        async with session.get(self.GOOGLE_TRANSLATE_URL, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            # پاسخ گوگل یک لیست تودرتو است، متن ترجمه شده در اولین عنصر قرار دارد
            translated_text = "".join([item[0] for item in data[0]])
            return translated_text

    async def _translate_with_mymemory(self, session: aiohttp.ClientSession, text: str) -> str:
        """ترجمه با استفاده از MyMemory API به عنوان جایگزین."""
        params = {"q": text, "langpair": "en|fa"}
        async with session.get(self.MYMEMORY_API_URL, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            if data.get("responseStatus") == 200:
                return data["responseData"]["translatedText"]
            else:
                raise ValueError(f"MyMemory API error: {data.get('responseDetails')}")

    async def translate(self, text: str) -> str:
        """
        یک متن انگلیسی را به فارسی ترجمه می‌کند.

        Args:
            text (str): متن انگلیسی برای ترجمه.

        Returns:
            str: متن ترجمه شده به فارسی، یا متن اصلی انگلیسی در صورت شکست تمام تلاش‌ها.
        """
        if not text or not text.strip():
            return ""

        logging.info(f"شروع فرآیند ترجمه برای متن: '{text[:50]}...'")
        async with aiohttp.ClientSession() as session:
            try:
                # تلاش اول: سرویس عمومی گوگل
                translated_text = await self._translate_with_google(session, text)
                logging.info("ترجمه با سرویس گوگل موفقیت‌آمیز بود.")
                return translated_text
            except Exception as e_google:
                logging.warning(f"ترجمه با سرویس گوگل ناموفق بود: {e_google}. تلاش با سرویس جایگزین...")
                try:
                    # تلاش دوم: MyMemory
                    translated_text = await self._translate_with_mymemory(session, text)
                    logging.info("ترجمه با MyMemory موفقیت‌آمیز بود.")
                    return translated_text
                except Exception as e_mymemory:
                    logging.error(f"ترجمه با MyMemory نیز ناموفق بود: {e_mymemory}. بازگرداندن متن اصلی.")
                    # آخرین راه حل: بازگرداندن متن اصلی
                    return text
