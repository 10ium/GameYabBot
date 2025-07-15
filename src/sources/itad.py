import logging
import aiohttp
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup # <-- کتابخانه جدید اضافه شد

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ITADSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فیدهای RSS رسمی سایت IsThereAnyDeal.
    این نسخه اصلاح شده با ساختار جدید فید (تجزیه تگ description) سازگار است.
    """
    RSS_URLS = [
        "https://isthereanydeal.com/feeds/US/USD/deals.rss?filter=100", # بازی‌های ۱۰۰٪ رایگان
        "https://isthereanydeal.com/feeds/US/giveaways.rss"           # بازی‌های هدیه
    ]
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    def _normalize_game_data(self, item: ET.Element) -> Optional[Dict[str, str]]:
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.
        ---*** این تابع به طور کامل بازنویسی شده است ***---
        """
        try:
            title = item.findtext('title', default='بدون عنوان').strip()
            # لینک اصلی بازی در ITAD را به عنوان لینک پیش‌فرض در نظر می‌گیریم
            main_link = item.findtext('link', default='#')
            description_html = item.findtext('description')

            store = "نامشخص"
            deal_url = main_link # اگر لینک فروشگاه پیدا نشد، از لینک اصلی استفاده کن

            if description_html:
                soup = BeautifulSoup(description_html, 'html.parser')
                deal_link_tag = soup.find('a') # اولین لینک در توضیحات معمولا لینک فروشگاه است

                if deal_link_tag:
                    # نام فروشگاه، متن داخل تگ <a> است
                    store = deal_link_tag.get_text(strip=True)
                    # لینک مستقیم به تخفیف
                    if deal_link_tag.has_attr('href'):
                        deal_url = deal_link_tag['href']

            return {
                "title": title,
                "store": store,
                "url": deal_url,
                "id_in_db": main_link  # لینک اصلی ITAD بهترین شناسه یکتا است
            }
        except Exception as e:
            logging.error(f"خطا در نرمال‌سازی آیتم ITAD: {e}")
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        """
        فیدهای RSS سایت ITAD را خوانده و لیست بازی‌های رایگان را استخراج می‌کند.
        """
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از فیدهای RSS سایت ITAD...")
        free_games_list = []
        processed_urls = set()

        for url in self.RSS_URLS:
            try:
                logging.info(f"در حال اسکن فید RSS: {url}")
                async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                    async with session.get(url) as response:
                        if response.status != 200:
                            logging.error(f"خطا در دریافت فید {url}: Status {response.status}")
                            continue
                        
                        rss_content = await response.text()
                        root = ET.fromstring(rss_content)
                        
                        for item in root.findall('.//channel/item'):
                            normalized_game = self._normalize_game_data(item)
                            
                            if normalized_game and normalized_game['id_in_db'] not in processed_urls:
                                free_games_list.append(normalized_game)
                                processed_urls.add(normalized_game['id_in_db'])
                                logging.info(f"بازی رایگان از ITAD RSS یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']}")

            except aiohttp.ClientError as e:
                logging.error(f"خطای شبکه هنگام دریافت فید RSS از {url}: {e}")
            except ET.ParseError as e:
                logging.error(f"خطا در تجزیه محتوای فید RSS از {url}: {e}")
            except Exception as e:
                logging.error(f"یک خطای پیش‌بینی نشده در ماژول ITAD (RSS) رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در فیدهای RSS سایت ITAD یافت نشد.")
            
        return free_games_list