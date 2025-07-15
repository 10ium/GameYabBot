import logging
import aiohttp
from typing import List, Dict, Any
import xml.etree.ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ITADSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فیدهای RSS رسمی سایت IsThereAnyDeal.
    این نسخه نیازی به کلید API ندارد و از دو فید مجزا استفاده می‌کند.
    """
    # آدرس‌های فید RSS جدید و رسمی
    RSS_URLS = [
        "https://isthereanydeal.com/feeds/US/USD/deals.rss?filter=100", # بازی‌های ۱۰۰٪ رایگان
        "https://isthereanydeal.com/feeds/US/giveaways.rss"             # بازی‌های هدیه
    ]
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    def _normalize_game_data(self, item: ET.Element) -> Dict[str, str]:
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.
        """
        title = item.findtext('title', default='بدون عنوان')
        link = item.findtext('link', default='#')
        
        # استخراج نام فروشگاه از عنوان (مثال: "Game Name (on Steam)")
        store = "نامشخص"
        if '(' in title and ')' in title:
            try:
                # جدا کردن بخش داخل پرانتز
                store_part = title.split('(')[-1].split(')')[0]
                # حذف کلمات اضافی مانند on یا from
                store = store_part.replace('on ', '').replace('from ', '').strip()
                # حذف بخش فروشگاه از عنوان اصلی
                title = title.split('(')[0].strip()
            except IndexError:
                pass

        return {
            "title": title,
            "store": store,
            "url": link,
            "id_in_db": link  # URL بهترین شناسه منحصر به فرد است
        }

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
                        
                        # فیدهای RSS معمولاً از تگ <item> در <channel> استفاده می‌کنند
                        for item in root.findall('.//channel/item'):
                            normalized_game = self._normalize_game_data(item)
                            # جلوگیری از اضافه کردن بازی‌های تکراری از فیدهای مختلف
                            if normalized_game['url'] not in processed_urls:
                                free_games_list.append(normalized_game)
                                processed_urls.add(normalized_game['url'])
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
