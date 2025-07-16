import logging
import asyncio
from typing import Optional
from deep_translator import GoogleTranslator, MyMemoryTranslator # اضافه کردن این خط
import random

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SmartTranslator:
    """
    کلاسی برای ترجمه هوشمند متن با استفاده از چندین سرویس ترجمه.
    اولویت با Google Translate است، سپس MyMemoryTranslator.
    """
    def __init__(self):
        self.google_translator = GoogleTranslator(source='en', target='fa')
        self.mymemory_translator = MyMemoryTranslator(source='en', target='fa')
        
        logger.info("SmartTranslator مقداردهی اولیه شد. سرویس‌های ترجمه: Google, MyMemory")

    async def translate(self, text: str) -> str:
        """
        متن را از انگلیسی به فارسی ترجمه می‌کند.
        ابتدا از Google Translate استفاده می‌کند، سپس به MyMemoryTranslator فال‌بک می‌کند.
        """
        if not text or not text.strip():
            return ""

        translated_text = None
        
        # 1. تلاش با Google Translate
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5)) # تأخیر تصادفی برای جلوگیری از بلاک شدن
            translated_text = self.google_translator.translate(text)
            if translated_text:
                logger.debug(f"ترجمه موفق با Google Translate: '{text[:30]}...' -> '{translated_text[:30]}...'")
                return translated_text
        except Exception as e:
            logger.warning(f"خطا در ترجمه با Google Translate (فال‌بک به MyMemory): {e}")

        # 2. تلاش با MyMemoryTranslator (فال‌بک)
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5)) # تأخیر تصادفی
            translated_text = self.mymemory_translator.translate(text)
            if translated_text:
                logger.debug(f"ترجمه موفق با MyMemoryTranslator: '{text[:30]}...' -> '{translated_text[:30]}...'")
                return translated_text
        except Exception as e:
            logger.warning(f"خطا در ترجمه با MyMemoryTranslator: {e}")

        logger.error(f"❌ ترجمه متن ناموفق بود: '{text[:100]}...'")
        return text # در صورت عدم موفقیت، متن اصلی را برمی‌گرداند

