import logging
import aiohttp
from typing import List, Dict, Any
import xml.etree.ElementTree as ET # کتابخانه استاندارد پایتون برای کار با XML

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ITADSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فید RSS وب‌سایت IsThereAnyDeal (ITAD).
    این نسخه نیازی به کلید API ندارد.
    """
    # آدرس فید RSS برای معاملاتی که ۱۰۰٪ تخفیف دارند
    RSS_URL = "https://isthereanydeal.com/rss/deals/all/?filter=100"

    def _normalize_game_data(self, item: ET.Element) -> Dict[str, str]:
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.

        Args:
            item (ET.Element): یک آیتم از فید RSS.

        Returns:
            Dict[str, str]: دیکشنری نرمال‌شده با کلیدهای استاندارد.
        """
        # پیدا کردن تگ‌ها در فضای نام (namespace) پیش‌فرض فید
        def find_tag(tag_name):
            return item.find(tag_name, namespaces=None)

        title = find_tag('title').text if find_tag('title') is not None else 'بدون عنوان'
        link = find_tag('link').text if find_tag('link') is not None else '#'
        
        # استخراج نام فروشگاه از عنوان (مثال: "Game Name (on Steam)")
        store = "نامشخص"
        if '(' in title and ')' in title:
            try:
                store = title.split('(')[-1].split(')')[0].replace('on ', '').strip()
                title = title.split('(')[0].strip()
            except IndexError:
                pass # اگر فرمت مورد انتظار نبود، از همان عنوان اصلی استفاده کن

        return {
            "title": title,
            "store": store,
            "url": link,
            "id_in_db": link # URL بهترین شناسه منحصر به فرد است
        }

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        """
        فید RSS سایت ITAD را خوانده و لیست بازی‌هایی که ۱۰۰٪ تخفیف دارند را استخراج می‌کند.

        Returns:
            List[Dict[str, str]]: لیستی از دیکشنری‌های بازی‌های رایگان.
        """
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از فید RSS سایت ITAD...")
        free_games_list = []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.RSS_URL) as response:
                    response.raise_for_status()
                    rss_content = await response.text()

                    # تجزیه محتوای XML فید RSS
                    root = ET.fromstring(rss_content)
                    
                    # پیدا کردن تمام آیتم‌های 'item' در کانال 'channel'
                    for item in root.findall('.//channel/item'):
                        normalized_game = self._normalize_game_data(item)
                        free_games_list.append(normalized_game)
                        logging.info(f"بازی رایگان از فید RSS یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']}")

        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام دریافت فید RSS از ITAD: {e}")
        except ET.ParseError as e:
            logging.error(f"خطا در تجزیه محتوای فید RSS از ITAD: {e}")
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول ITAD (RSS) رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در فید RSS سایت ITAD یافت نشد.")
            
        return free_games_list
