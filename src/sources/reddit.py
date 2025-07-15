import logging
import aiohttp
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class RedditSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فید RSS ردیت.
    این نسخه نیازی به کلید API ندارد و لینک‌ها را به درستی استخراج می‌کند.
    """
    def __init__(self):
        subreddits = [
            'GameDeals',
            'FreeGameFindings',
            'googleplaydeals',
            'AppHookup'
        ]
        self.rss_urls = [f"https://www.reddit.com/r/{sub}/new/.rss" for sub in subreddits]
        logging.info("نمونه RedditSource (نسخه RSS اصلاح شده) با موفقیت ایجاد شد.")

    def _normalize_post_data(self, entry: ET.Element) -> Optional[Dict[str, str]]:
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.
        این نسخه محتوای HTML را برای یافتن لینک اصلی تجزیه می‌کند.
        """
        try:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            title_element = entry.find('atom:title', ns)
            content_element = entry.find('atom:content', ns)
            id_element = entry.find('atom:id', ns)

            if title_element is None or content_element is None or id_element is None:
                return None

            title = title_element.text
            post_id = id_element.text
            
            # --- *** بخش کلیدی اصلاح شده *** ---
            # محتوای HTML را با BeautifulSoup تجزیه می‌کنیم تا لینک اصلی را پیدا کنیم
            soup = BeautifulSoup(content_element.text, 'html.parser')
            link_tag = soup.find('a', string='[link]') # به دنبال تگ <a> با متن '[link]' می‌گردیم
            if not link_tag or 'href' not in link_tag.attrs:
                return None # اگر لینک اصلی پیدا نشد، پست را نادیده می‌گیریم
            
            url = link_tag['href']

            # استخراج نام فروشگاه و تمیز کردن عنوان
            store_match = re.search(r'\[([^\]]+)\]', title)
            store = store_match.group(1).strip() if store_match else 'نامشخص'
            clean_title = re.sub(r'\[[^\]]+\]', '', title).strip()

            return {
                "title": clean_title,
                "store": store,
                "url": url,
                "id_in_db": post_id
            }
        except Exception as e:
            logging.error(f"خطا در نرمال‌سازی پست RSS ردیت: {e}")
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        """
        فید RSS ساب‌ردیت‌های مشخص شده را برای یافتن بازی‌های رایگان اسکن می‌کند.
        """
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از فید RSS ردیت...")
        free_games_list = []
        processed_ids = set()

        try:
            for url in self.rss_urls:
                logging.info(f"در حال اسکن فید RSS: {url}...")
                async with aiohttp.ClientSession() as session:
                    # اضافه کردن هدر User-Agent برای جلوگیری از خطای 429 (Too Many Requests)
                    headers = {'User-agent': 'GameBeaconBot/1.0'}
                    async with session.get(url, headers=headers) as response:
                        if response.status != 200:
                            logging.error(f"خطا در دریافت فید {url}: Status {response.status}")
                            continue # به سراغ فید بعدی می‌رویم
                        
                        rss_content = await response.text()
                        root = ET.fromstring(rss_content)
                        
                        ns = {'atom': 'http://www.w3.org/2005/Atom'}
                        for entry in root.findall('atom:entry', ns):
                            title_lower = (entry.find('atom:title', ns).text or "").lower()
                            keywords = ['free', '100% off', '100% discount']
                            
                            if any(keyword in title_lower for keyword in keywords):
                                normalized_game = self._normalize_post_data(entry)
                                if normalized_game and normalized_game['id_in_db'] not in processed_ids:
                                    free_games_list.append(normalized_game)
                                    processed_ids.add(normalized_game['id_in_db'])
                                    logging.info(f"پست بازی رایگان از RSS ردیت یافت شد: {normalized_game['title']}")
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول Reddit (RSS) رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر پست بازی رایگان جدیدی در فیدهای RSS ردیت یافت نشد.")
            
        return free_games_list
