import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import hashlib # برای تولید ID منحصر به فرد برای آیتم‌های فرعی

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO, # می‌توانید این را به logging.DEBUG تغییر دهید برای لاگ‌های بسیار جزئی
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ایجاد یک لاگر خاص برای این ماژول
logger = logging.getLogger(__name__)

class RedditSource:
    """
    کلاسی برای دریافت بازی‌های رایگان از طریق فید RSS ردیت.
    این نسخه نیازی به کلید API ندارد و لینک‌ها را به درستی استخراج می‌کند.
    """
    def __init__(self):
        self.subreddits = [
            'GameDeals',
            'FreeGameFindings',
            'googleplaydeals',
            'AppHookup'
        ]
        # ذخیره ساب‌ردیت‌ها به صورت دیکشنری برای دسترسی آسان به نام و URL
        self.rss_urls = {sub: f"https://www.reddit.com/r/{sub}/new/.rss" for sub in self.subreddits}
        logger.info("نمونه RedditSource (نسخه RSS اصلاح شده) با موفقیت ایجاد شد.")

    @staticmethod
    def _generate_unique_id(base_id: str, item_url: str) -> str:
        """
        یک ID منحصر به فرد برای آیتم‌های فرعی در پست‌های لیست‌مانند تولید می‌کند.
        """
        # از هش SHA256 برای ایجاد یک ID منحصر به فرد از ترکیب ID پست اصلی و URL آیتم استفاده می‌کنیم.
        # این کار از تداخل IDها جلوگیری می‌کند.
        combined_string = f"{base_id}-{item_url}"
        return hashlib.sha256(combined_string.encode()).hexdigest()

    def _normalize_post_data(self, entry: ET.Element, subreddit_name: str) -> Optional[Dict[str, Any]]: # Any برای انعطاف‌پذیری بیشتر
        """
        یک تابع کمکی برای تبدیل داده‌های یک آیتم RSS به فرمت استاندارد پروژه.
        این نسخه محتوای HTML را برای یافتن لینک اصلی، تصویر و توضیحات تجزیه می‌کند.
        """
        try:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            title_element = entry.find('atom:title', ns)
            content_element = entry.find('atom:content', ns)
            id_element = entry.find('atom:id', ns)

            if title_element is None or content_element is None or id_element is None:
                logger.debug(f"پست RSS ناقص در ساب‌ردیت {subreddit_name} یافت شد (عنوان، محتوا یا ID موجود نیست).")
                return None

            raw_title = title_element.text
            post_id = id_element.text
            
            # تجزیه محتوای HTML با BeautifulSoup برای یافتن لینک اصلی، تصویر و توضیحات
            soup = BeautifulSoup(content_element.text, 'html.parser')
            link_tag = soup.find('a', string='[link]') # به دنبال تگ <a> با متن '[link]' می‌گردیم
            if not link_tag or 'href' not in link_tag.attrs:
                logger.debug(f"لینک اصلی '[link]' در پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد.")
                return None # اگر لینک اصلی پیدا نشد، پست را نادیده می‌گیریم
            
            url = link_tag['href']
            
            # استخراج توضیحات (متن اصلی پست)
            description_tag = soup.find('div', class_='md') # معمولاً محتوای Markdown در این تگ است
            description = description_tag.get_text(strip=True) if description_tag else ""

            # استخراج تصویر (اگر وجود داشته باشد)
            image_tag = soup.find('img', src=True)
            image_url = image_tag['src'] if image_tag else None

            # --- بهبود استخراج نام فروشگاه ---
            store = 'other' # مقدار پیش‌فرض
            
            # 1. تلاش برای استخراج از براکت در عنوان
            store_platform_match = re.search(r'\[([^\]]+)\]', raw_title)
            if store_platform_match:
                platform_str = store_platform_match.group(1).strip().lower()

                if "steam" in platform_str:
                    store = "steam"
                elif "epic games" in platform_str or "epicgames" in platform_str:
                    store = "epic games"
                elif "gog" in platform_str:
                    store = "gog"
                elif "xbox" in platform_str:
                    store = "xbox"
                elif "ps" in platform_str or "playstation" in platform_str:
                    store = "playstation"
                elif "nintendo" in platform_str:
                    store = "nintendo"
                elif "stove" in platform_str:
                    store = "stove"
                elif "indiegala" in platform_str:
                    store = "indiegala"
                elif "itch.io" in platform_str or "itchio" in platform_str:
                    store = "itch.io"
                elif "android" in platform_str or "googleplay" in platform_str or "google play" in platform_str or "apps" in platform_str:
                    if "play.google.com" in url:
                        store = "google play"
                    elif "apps.apple.com" in url:
                        store = "ios app store"
                    else:
                        store = "apps" # یا 'google play' اگر مطمئنید فقط گوگل پلی است
                elif "ios" in platform_str or "apple" in platform_str:
                    if "apps.apple.com" in url:
                        store = "ios app store"
                    elif "play.google.com" in url:
                        store = "google play"
                    else:
                        store = "ios app store" # یا 'apps' عمومی
                elif "windows" in platform_str or "mac" in platform_str or "linux" in platform_str:
                    if "store.steampowered.com" in url:
                        store = "steam"
                    elif "epicgames.com" in url:
                        store = "epic games"
                    elif "gog.com" in url:
                        store = "gog"
                    elif "itch.io" in url:
                        store = "itch.io"
                    elif "indiegala.com" in url:
                        store = "indiegala"
                    else:
                        store = "other"
                elif "multi-platform" in platform_str:
                    if "store.steampowered.com" in url:
                        store = "steam"
                    elif "epicgames.com" in url:
                        store = "epic games"
                    elif "gog.com" in url:
                        store = "gog"
                    elif "play.google.com" in url:
                        store = "google play"
                    elif "apps.apple.com" in url:
                        store = "ios app store"
                    else:
                        store = "other"
            
            # 2. اگر هنوز نام فروشگاه عمومی بود، تلاش برای حدس زدن از URL اصلی (اگر از براکت استخراج نشده بود)
            # این بخش را فقط اگر store هنوز 'other' یا 'apps' یا 'نامشخص' است، اجرا می‌کنیم.
            if store in ['other', 'نامشخص', 'apps']:
                if "play.google.com" in url:
                    store = "google play"
                elif "apps.apple.com" in url:
                    store = "ios app store"
                elif "store.steampowered.com" in url:
                    store = "steam"
                elif "epicgames.com" in url:
                    store = "epic games"
                elif "gog.com" in url:
                    store = "gog"
                elif "xbox.com" in url:
                    store = "xbox"
                elif "itch.io" in url:
                    store = "itch.io"
                elif "indiegala.com" in url:
                    store = "indiegala"
                elif "onstove.com" in url:
                    store = "stove"
            
            # تمیز کردن عنوان: حذف تمام بخش‌های براکتی و عبارات اضافی
            clean_title = re.sub(r'\[[^\]]+\]', '', raw_title).strip()
            clean_title = re.sub(r'\(game\)', '', clean_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'\(\$.*?-> Free\)', '', clean_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'\(\d+%\s*off\)', '', clean_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'\(\s*free\s*\)', '', clean_title, flags=re.IGNORECASE).strip()
            
            # اگر عنوان پس از تمیز کردن خالی شد، از عنوان اصلی استفاده می‌کنیم یا آن را نادیده می‌گیریم
            if not clean_title:
                clean_title = raw_title.strip()
                if not clean_title: # اگر عنوان اصلی هم خالی بود
                    logger.warning(f"⚠️ پست بازی رایگان با عنوان کاملاً خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {post_id}")
                    return None

            return {
                "title": clean_title,
                "store": store,
                "url": url,
                "image_url": image_url, # اضافه شده
                "description": description, # اضافه شده
                "id_in_db": post_id,
                "subreddit": subreddit_name
            }
        except Exception as e:
            logger.error(f"❌ خطا در نرمال‌سازی پست RSS ردیت از ساب‌ردیت {subreddit_name}: {e}", exc_info=True)
            return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[Dict[str, Any]]: # Any برای انعطاف‌پذیری بیشتر
        """
        محتوای HTML پست‌های 'Weekly deals' از r/AppHookup را تجزیه می‌کند
        و آیتم‌های رایگان را استخراج می‌کند.
        """
        found_items = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # پیدا کردن همه تگ‌های <a> که دارای href هستند و در پاراگراف‌ها یا لیست‌ها قرار دارند
        for a_tag in soup.find_all('a', href=True):
            parent_text_element = a_tag.find_parent(['p', 'li'])
            if parent_text_element:
                text_around_link = parent_text_element.get_text().lower()
                item_title = a_tag.get_text().strip()
                item_url = a_tag['href']

                # بررسی الگوهای "رایگان" در متن اطراف لینک
                is_free = False
                if "free" in text_around_link or "-> 0" in text_around_link or "--> 0" in text_around_link:
                    if "off" in text_around_link and "100% off" not in text_around_link and "free" not in text_around_link:
                        is_free = False
                    else:
                        is_free = True

                if is_free:
                    # تلاش برای حدس زدن فروشگاه از URL
                    store = "other"
                    if "apps.apple.com" in item_url:
                        store = "ios app store"
                    elif "play.google.com" in item_url:
                        store = "google play"
                    elif "store.steampowered.com" in item_url:
                        store = "steam"
                    elif "epicgames.com" in item_url:
                        store = "epic games"
                    elif "gog.com" in item_url:
                        store = "gog"
                    elif "xbox.com" in item_url:
                        store = "xbox"
                    elif "itch.io" in item_url:
                        store = "itch.io"
                    elif "indiegala.com" in item_url:
                        store = "indiegala"
                    elif "onstove.com" in item_url:
                        store = "stove"
                    
                    # استخراج توضیحات (اگر در نزدیکی لینک باشد)
                    item_description = parent_text_element.get_text(strip=True)
                    # حذف عنوان و لینک از توضیحات
                    item_description = item_description.replace(item_title, '').replace(item_url, '').strip()
                    # اگر توضیحات خیلی کوتاه بود، از عنوان استفاده کن
                    if len(item_description) < 20:
                        item_description = item_title # Fallback to title if description is too short

                    # استخراج تصویر (اگر در نزدیکی لینک باشد)
                    item_image_tag = parent_text_element.find('img', src=True)
                    item_image_url = item_image_tag['src'] if item_image_tag else None
                    
                    if item_title:
                        found_items.append({
                            "title": item_title,
                            "store": store,
                            "url": item_url,
                            "image_url": item_image_url, # اضافه شده
                            "description": item_description, # اضافه شده
                            "id_in_db": self._generate_unique_id(base_post_id, item_url),
                            "subreddit": "AppHookup"
                        })
                        logger.debug(f"✅ آیتم رایگان داخلی از AppHookup یافت شد: {item_title} (URL: {item_url})")
                    else:
                        logger.warning(f"⚠️ آیتم رایگان داخلی با عنوان خالی از AppHookup نادیده گرفته شد. URL: {item_url}")
            
        return found_items

    async def fetch_free_games(self) -> List[Dict[str, Any]]: # Any برای انعطاف‌پذیری بیشتر
        """
        فید RSS ساب‌ردیت‌های مشخص شده را برای یافتن بازی‌های رایگان اسکن می‌کند.
        """
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از فید RSS ردیت...")
        free_games_list = []
        processed_ids = set()

        try:
            for subreddit_name, url in self.rss_urls.items():
                logger.info(f"در حال اسکن فید RSS: {url} (ساب‌ردیت: {subreddit_name})...")
                async with aiohttp.ClientSession() as session:
                    headers = {'User-agent': 'GameBeaconBot/1.0'}
                    async with session.get(url, headers=headers) as response:
                        if response.status != 200:
                            logger.error(f"❌ خطا در دریافت فید {url}: Status {response.status}")
                            continue
                        
                        rss_content = await response.text()
                        root = ET.fromstring(rss_content)
                        
                        ns = {'atom': 'http://www.w3.org/2005/Atom'}
                        for entry in root.findall('atom:entry', ns):
                            title_element = entry.find('atom:title', ns)
                            content_element = entry.find('atom:content', ns)
                            id_element = entry.find('atom:id', ns)

                            if title_element is None or content_element is None or id_element is None:
                                logger.debug(f"پست RSS ناقص در ساب‌ردیت {subreddit_name} (عنوان، محتوا یا ID موجود نیست).")
                                continue

                            title_lower = title_element.text.lower()
                            post_id = id_element.text

                            is_free_game = False
                            
                            if subreddit_name == 'FreeGameFindings':
                                if "(game)" in title_lower:
                                    if "off" in title_lower and "100% off" not in title_lower:
                                        is_free_game = False
                                    else:
                                        is_free_game = True
                                
                                if "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                                    is_free_game = True
                            
                            elif subreddit_name == 'googleplaydeals' or subreddit_name == 'AppHookup':
                                keywords = ['free', '100% off', '100% discount']
                                if any(keyword in title_lower for keyword in keywords):
                                    is_free_game = True
                                
                                if subreddit_name == 'AppHookup' and ("weekly" in title_lower and ("deals post" in title_lower or "app deals post" in title_lower or "game deals post" in title_lower)):
                                    logger.info(f"🔍 پست 'Weekly Deals' از AppHookup شناسایی شد: {title_element.text}. در حال بررسی آیتم‌های داخلی...")
                                    weekly_items = self._parse_apphookup_weekly_deals(content_element.text, post_id)
                                    for item in weekly_items:
                                        if item['id_in_db'] not in processed_ids:
                                            free_games_list.append(item)
                                            processed_ids.add(item['id_in_db'])
                                            logger.info(f"✅ آیتم رایگان از لیست 'Weekly Deals' ({item['subreddit']}) یافت شد: {item['title']} (فروشگاه: {item['store']})")
                                    continue

                            else: # برای سایر ساب‌ردیت‌ها (مثل GameDeals)
                                keywords = ['free', '100% off', '100% discount']
                                if any(keyword in title_lower for keyword in keywords):
                                    is_free_game = True

                            if is_free_game:
                                normalized_game = self._normalize_post_data(entry, subreddit_name)
                                if normalized_game and normalized_game['id_in_db'] not in processed_ids:
                                    if normalized_game['title'].strip():
                                        free_games_list.append(normalized_game)
                                        processed_ids.add(normalized_game['id_in_db'])
                                        logger.info(f"✅ پست بازی رایگان از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                                    else:
                                        logger.warning(f"⚠️ پست بازی رایگان با عنوان خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {normalized_game['id_in_db']}")
                                else:
                                    logger.debug(f"ℹ️ پست '{title_element.text}' از {subreddit_name} یا از قبل پردازش شده بود یا نرمال‌سازی نشد.")
                            else:
                                logger.debug(f"🔍 پست '{title_element.text}' از {subreddit_name} شرایط 'بازی رایگان' را نداشت و نادیده گرفته شد.")

        except Exception as e:
            logger.critical(f"🔥 یک خطای بحرانی پیش‌بینی نشده در ماژول Reddit (RSS) رخ داد: {e}", exc_info=True)
            
        if not free_games_list:
            logger.info("ℹ️ در حال حاضر پست بازی رایگان جدیدی در فیدهای RSS ردیت یافت نشد.")
            
        return free_games_list
