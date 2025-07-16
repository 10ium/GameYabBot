import logging
import asyncio
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import random # برای تأخیر تصادفی
import re # برای استفاده از regex

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ITADSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فید RSS سفارشی سایت IsThereAnyDeal.
    این نسخه فقط از فید Deals استفاده می‌کند و Giveaway حذف شده است.
    """
    # --- *** آدرس سفارشی شما به صورت خودکار جایگزین شد *** ---
    RSS_URLS = [
        "https://isthereanydeal.com/feeds/US/USD/deals.rss?filter=N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA%3D%3D"
    ]
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    def _normalize_game_data(self, item: ET.Element) -> Optional[Dict[str, str]]:
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.
        این نسخه محتوای تگ <description> را برای یافتن نام فروشگاه تجزیه می‌کند.
        """
        try:
            title = item.findtext('title', default='بدون عنوان').strip()
            # لینک اصلی بازی در ITAD را به عنوان شناسه یکتا در نظر می‌گیریم
            main_link = item.findtext('link', default='#')
            description_html = item.findtext('description')

            store = "نامشخص"
            deal_url = main_link # اگر لینک فروشگاه پیدا نشد، از لینک اصلی استفاده کن
            is_free = False # پیش‌فرض: رایگان نیست
            discount_text = None

            if description_html:
                soup = BeautifulSoup(description_html, 'html.parser')
                deal_link_tag = soup.find('a') # اولین لینک در توضیحات معمولا لینک فروشگاه است

                if deal_link_tag:
                    # نام فروشگاه، متن داخل تگ <a> است
                    store = deal_link_tag.get_text(strip=True)
                    # لینک مستقیم به تخفیف
                    if deal_link_tag.has_attr('href'):
                        deal_url = deal_link_tag['href']
                
                # بررسی رایگان بودن بر اساس متن توضیحات
                # ITAD Deals RSS می تواند شامل 100% تخفیف باشد
                full_description_text = soup.get_text(strip=True).lower()
                if "100% off" in full_description_text or "free" in full_description_text:
                    is_free = True
                else:
                    # اگر تخفیف عادی بود، متن تخفیف را استخراج کن
                    discount_match = re.search(r'(\d+% off)', full_description_text)
                    if discount_match:
                        discount_text = discount_match.group(1)

            return {
                "title": title,
                "store": store,
                "url": deal_url,
                "id_in_db": main_link,  # لینک اصلی ITAD بهترین شناسه منحصر به فرد است
                "is_free": is_free,
                "discount_text": discount_text
            }
        except Exception as e:
            logging.error(f"خطا در نرمال‌سازی آیتم ITAD: {e}")
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        """
        فید RSS سایت ITAD را خوانده و لیست بازی‌های رایگان را استخراج می‌کند.
        """
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از فید سفارشی ITAD...")
        free_games_list = []
        processed_urls = set()

        for url in self.RSS_URLS:
            try:
                logging.info(f"در حال اسکن فید RSS: {url}")
                async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                    await asyncio.sleep(random.uniform(1, 3)) # تأخیر تصادفی برای کاهش بلاک شدن
                    async with session.get(url) as response:
                        if response.status != 200:
                            logging.error(f"خطا در دریافت فید {url}: Status {response.status}")
                            continue
                        
                        rss_content = await response.text()
                        root = ET.fromstring(rss_content)
                        
                        for item in root.findall('.//channel/item'):
                            normalized_game = self._normalize_game_data(item)
                            
                            if normalized_game and normalized_game['id_in_db'] not in processed_urls:
                                # ITAD Deals RSS ممکن است شامل بازی‌های رایگان واقعی (100% تخفیف) باشد
                                # یا فقط تخفیف‌های عادی. اینجا ما همه را اضافه می‌کنیم و فیلتر
                                # "Not Free (Discount)" در main.py انجام می‌شود.
                                free_games_list.append(normalized_game)
                                processed_urls.add(normalized_game['id_in_db'])
                                logging.info(f"بازی از ITAD RSS یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']} (رایگان: {normalized_game['is_free']})")

            except aiohttp.ClientError as e:
                logging.error(f"خطای شبکه هنگام دریافت فید RSS از {url}: {e}")
            except ET.ParseError as e:
                logging.error(f"خطا در تجزیه محتوای فید RSS از {url}: {e}")
            except Exception as e:
                logging.error(f"یک خطای پیش‌بینی نشده در ماژول ITAD (RSS) رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در فید سفارشی ITAD یافت نشد.")
            
        return free_games_list
