import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import hashlib
import random # برای تأخیر تصادفی
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class RedditSource:
    def __init__(self):
        self.subreddits = [
            'GameDeals',
            'FreeGameFindings',
            'googleplaydeals',
            'AppHookup'
        ]
        self.rss_urls = {sub: f"https://www.reddit.com/r/{sub}/new/.rss" for sub in self.subreddits}
        self.HEADERS = {'User-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'} # User-Agent عمومی‌تر برای ردیت
        logger.info("نمونه RedditSource (نسخه RSS اصلاح شده) با موفقیت ایجاد شد.")

    @staticmethod
    def _generate_unique_id(base_id: str, item_url: str) -> str:
        """
        یک شناسه منحصر به فرد بر اساس شناسه اصلی و URL آیتم برای جلوگیری از تکرار ایجاد می‌کند.
        """
        combined_string = f"{base_id}-{item_url}"
        return hashlib.sha256(combined_string.encode()).hexdigest()

    async def _fetch_and_parse_reddit_permalink(self, session: aiohttp.ClientSession, permalink_url: str) -> Optional[str]:
        """
        یک لینک دائمی ردیت را واکشی کرده و اولین لینک خارجی معتبر را از محتوای آن استخراج می‌کند.
        """
        try:
            logger.info(f"در حال واکشی لینک دائمی ردیت برای یافتن لینک خارجی: {permalink_url}")
            await asyncio.sleep(random.uniform(2, 5)) # تأخیر تصادفی برای کاهش بلاک شدن
            async with session.get(permalink_url, headers=self.HEADERS) as response:
                response.raise_for_status() # اگر وضعیت 200 نباشد، خطا پرتاب می‌کند
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # پیدا کردن div اصلی محتوای پست (سلکتورها ممکن است نیاز به به‌روزرسانی داشته باشند)
                # این سلکتورها بر اساس ساختار فعلی ردیت هستند و ممکن است تغییر کنند.
                post_content_div = soup.find('div', class_='s19g0207-1') 
                if not post_content_div:
                    post_content_div = soup.find('div', class_='_292iotee39Lmt0Q_h-B5N') 
                if not post_content_div:
                    post_content_div = soup.find('div', class_='_1qeIAgB0cPwnLhDF9XHzvm') 
                
                if post_content_div:
                    # یافتن تمام لینک‌های داخل محتوای پست
                    for a_tag in post_content_div.find_all('a', href=True):
                        href = a_tag['href']
                        # اطمینان حاصل کن که لینک به reddit.com نیست و یک لینک کامل HTTP/HTTPS است
                        if "reddit.com" not in href and href.startswith("http"):
                            logger.info(f"لینک خارجی از لینک دائمی ردیت یافت شد: {href}")
                            return href
                logger.warning(f"هیچ لینک خارجی معتبری در لینک دائمی ردیت یافت نشد: {permalink_url}")
                return None
        except aiohttp.ClientError as e:
            logger.error(f"خطای شبکه هنگام واکشی لینک دائمی ردیت {permalink_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"خطای پیش‌بینی نشده هنگام تجزیه لینک دائمی ردیت {permalink_url}: {e}", exc_info=True)
            return None

    async def _normalize_post_data(self, session: aiohttp.ClientSession, entry: ET.Element, subreddit_name: str) -> Optional[Dict[str, Any]]:
        """
        داده‌های یک پست RSS ردیت را به فرمت استاندارد پروژه تبدیل می‌کند.
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
            
            soup = BeautifulSoup(content_element.text, 'html.parser')
            
            # --- اولویت‌بندی استخراج URL و فروشگاه ---
            final_url = None
            detected_store = 'other' # مقدار پیش‌فرض

            # الگوهای URL برای شناسایی فروشگاه‌ها (ترتیب مهم است: خاص‌ترها اول)
            url_store_map_priority = [
                (r"epicgames\.com/store/p/.*-android-", "epic games (android)"), # اگر لینک اپیک گیمز به اندروید اشاره دارد
                (r"epicgames\.com/store/p/.*-ios-", "epic games (ios)"),   # اگر لینک اپیک گیمز به iOS اشاره دارد
                (r"epicgames\.com/store/p/", "epic games"), # General Epic Desktop, if not mobile
                (r"store\.steampowered\.com", "steam"),
                (r"play\.google\.com", "google play"),
                (r"apps\.apple\.com", "ios app store"),
                (r"xbox\.com", "xbox"),
                (r"playstation\.com", "playstation"), 
                (r"gog\.com", "gog"),
                (r"itch\.io", "itch.io"),
                (r"indiegala\.com", "indiegala"),
                (r"onstove\.com", "stove"),
            ]

            # 1. تلاش برای یافتن لینک مستقیم فروشگاه از محتوای پست (غیر از لینک [link] اصلی)
            # این شامل لینک‌های اضافی که در متن پست قرار داده شده‌اند، می‌شود.
            all_links_in_content = soup.find_all('a', href=True)
            for a_tag in all_links_in_content:
                href = a_tag['href']
                # از لینک‌های ردیت داخلی و لینک‌های خالی صرف نظر کن
                if "reddit.com" in href or not href.startswith("http"):
                    continue
                
                for pattern, store_name in url_store_map_priority:
                    if re.search(pattern, href, re.IGNORECASE):
                        final_url = href
                        detected_store = store_name
                        break # اولین تطابق با اولویت بالاتر را پیدا کردیم
                if final_url: # اگر لینکی پیدا شد، از حلقه خارج شو
                    break

            # 2. اگر هنوز لینک فروشگاه مستقیم پیدا نشد، لینک [link] اصلی را بررسی کن
            if not final_url:
                link_tag = soup.find('a', string='[link]')
                if link_tag and 'href' in link_tag.attrs:
                    main_post_url = link_tag['href']
                    # اگر لینک [link] یک لینک دائمی ردیت باشد، آن را واکشی و تجزیه کن
                    if "reddit.com" in main_post_url and "/comments/" in main_post_url:
                        fetched_external_url = await self._fetch_and_parse_reddit_permalink(session, main_post_url)
                        if fetched_external_url:
                            final_url = fetched_external_url
                            # پس از واکشی، فروشگاه را دوباره از URL جدید حدس بزن
                            for pattern, store_name in url_store_map_priority:
                                if re.search(pattern, final_url, re.IGNORECASE):
                                    detected_store = store_name
                                    break
                        else:
                            logger.warning(f"لینک خارجی از لینک دائمی ردیت '{main_post_url}' استخراج نشد. از لینک اصلی ردیت استفاده می‌شود.")
                            final_url = main_post_url # Fallback به لینک دائمی ردیت
                            detected_store = "reddit" # صریحاً به reddit تنظیم شود اگر permalink URL نهایی است
                    else:
                        final_url = main_post_url
                        # فروشگاه را از لینک [link] اصلی حدس بزن
                        for pattern, store_name in url_store_map_priority:
                            if re.search(pattern, final_url, re.IGNORECASE):
                                detected_store = store_name
                                break
                else:
                    logger.debug(f"لینک [link] در پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد.")
                    # Fallback به URL اصلی پست RSS اگر هیچ لینک دیگری پیدا نشد
                    link_element = entry.find('atom:link', ns)
                    if link_element is not None and link_element.get('href'):
                        final_url = link_element.get('href')
                        detected_store = "reddit" # اگر از لینک RSS پست استفاده شد، فروشگاه را reddit قرار بده
                        logger.warning(f"هیچ لینک فروشگاه مستقیمی برای '{raw_title}' یافت نشد. از لینک RSS پست استفاده می‌شود: {final_url}")
                    else:
                        logger.warning(f"هیچ URL معتبری برای پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد. نادیده گرفته شد.")
                        return None
            
            # 3. اگر هنوز نام فروشگاه عمومی بود، تلاش برای استخراج از براکت در عنوان
            if detected_store == 'other': # فقط اگر هنوز 'other' است، از براکت استفاده کن
                store_platform_match = re.search(r'\[([^\]]+)\]', raw_title)
                if store_platform_match:
                    platform_str = store_platform_match.group(1).strip().lower()

                    if "steam" in platform_str: detected_store = "steam"
                    elif "epic games" in platform_str or "epicgames" in platform_str: detected_store = "epic games"
                    elif "gog" in platform_str: detected_store = "gog"
                    elif "xbox" in platform_str: detected_store = "xbox"
                    elif "ps" in platform_str or "playstation" in platform_str: detected_store = "playstation"
                    elif "nintendo" in platform_str: detected_store = "nintendo"
                    elif "stove" in platform_str: detected_store = "stove"
                    elif "indiegala" in platform_str: detected_store = "indiegala"
                    elif "itch.io" in platform_str or "itchio" in platform_str: detected_store = "itch.io"
                    elif "android" in platform_str or "googleplay" in platform_str or "google play" in platform_str or "apps" in platform_str:
                        detected_store = "google play"
                    elif "ios" in platform_str or "apple" in platform_str:
                        detected_store = "ios app store"
                    elif "windows" in platform_str or "mac" in platform_str or "linux" in platform_str:
                        # برای پلتفرم‌های دسکتاپ، اگر URL مشخص نیست، همچنان 'other' بهتر است
                        pass # اجازه بده 'other' بماند
                    elif "multi-platform" in platform_str:
                        pass # اجازه بده 'other' بماند

            # --- استخراج توضیحات و تصویر ---
            description_tag = soup.find('div', class_='md')
            description = description_tag.get_text(strip=True) if description_tag else ""

            image_tag = soup.find('img', src=True)
            image_url = image_tag['src'] if image_tag else None

            # تمیز کردن عنوان با استفاده از تابع مشترک
            clean_title = clean_title_for_search(raw_title)
            
            if not clean_title:
                clean_title = raw_title.strip()
                if not clean_title:
                    logger.warning(f"⚠️ پست بازی رایگان با عنوان کاملاً خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {post_id}")
                    return None

            # تعیین is_free و discount_text
            is_truly_free = False
            discount_text = None
            title_lower = raw_title.lower()

            if subreddit_name == 'FreeGameFindings':
                is_truly_free = True # تمام پست‌ها از FreeGameFindings واقعاً رایگان هستند
            elif "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                is_truly_free = True
            elif "off" in title_lower: # اگر کلمه "off" بود ولی "free" یا "100% off" نبود
                is_truly_free = False # تخفیف عادی
                discount_match = re.search(r'(\d+% off)', title_lower)
                if discount_match:
                    discount_text = discount_match.group(1)
                else:
                    discount_text = "تخفیف" # اگر درصد تخفیف مشخص نبود

            return {
                "title": clean_title,
                "store": detected_store, # استفاده از فروشگاه شناسایی شده
                "url": final_url, # استفاده از URL نهایی
                "image_url": image_url,
                "description": description,
                "id_in_db": post_id, # شناسه پست ردیت به عنوان id_in_db
                "subreddit": subreddit_name,
                "is_free": is_truly_free, # اضافه شدن فیلد is_free
                "discount_text": discount_text # اضافه شدن فیلد discount_text
            }
        except Exception as e:
            logger.error(f"❌ خطا در نرمال‌سازی پست RSS ردیت از ساب‌ردیت {subreddit_name}: {e}", exc_info=True)
            return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[Dict[str, Any]]:
        """
        محتوای HTML پست‌های 'Weekly Deals' از ساب‌ردیت AppHookup را تجزیه می‌کند
        و آیتم‌های رایگان داخلی را استخراج می‌کند.
        """
        found_items = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # الگوهای URL برای شناسایی فروشگاه‌ها (ترتیب مهم است: خاص‌ترها اول)
        url_store_map_priority = [
            (r"epicgames\.com/store/p/.*-android-", "epic games (android)"), 
            (r"epicgames\.com/store/p/.*-ios-", "epic games (ios)"),
            (r"epicgames\.com/store/p/", "epic games"),
            (r"store\.steampowered\.com", "steam"),
            (r"play\.google\.com", "google play"),
            (r"apps\.apple\.com", "ios app store"),
            (r"xbox\.com", "xbox"),
            (r"playstation\.com", "playstation"),
            (r"gog\.com", "gog"),
            (r"itch\.io", "itch.io"),
            (r"indiegala\.com", "indiegala"),
            (r"onstove\.com", "stove"),
        ]

        for a_tag in soup.find_all('a', href=True):
            parent_text_element = a_tag.find_parent(['p', 'li'])
            if parent_text_element:
                text_around_link = parent_text_element.get_text().lower()
                item_title = a_tag.get_text().strip()
                item_url = a_tag['href']

                is_truly_free = False
                discount_text = None
                
                # تشخیص "رایگان" یا "تخفیف‌دار"
                if "free" in text_around_link or "-> 0" in text_around_link or "--> 0" in text_around_link or "100% off" in text_around_link:
                    # اگر "off" بود ولی 100% off یا free نبود، یعنی فقط تخفیف است
                    if "off" in text_around_link and "100% off" not in text_around_link and "free" not in text_around_link:
                        is_truly_free = False # تخفیف عادی
                        discount_match = re.search(r'(\d+% off)', text_around_link)
                        if discount_match:
                            discount_text = discount_match.group(1)
                        else:
                            discount_text = "تخفیف"
                    else:
                        is_truly_free = True # واقعا رایگان
                elif "off" in text_around_link: # اگر فقط "off" بود و "free" نبود
                    is_truly_free = False # این یک تخفیف است، نه رایگان
                    discount_match = re.search(r'(\d+% off)', text_around_link)
                    if discount_match:
                        discount_text = discount_match.group(1)
                    else:
                        discount_text = "تخفیف"

                if is_truly_free or (not is_truly_free and discount_text): # اضافه کردن هم رایگان و هم تخفیف‌دار
                    store = "other"
                    # 1. تلاش برای حدس زدن از URL اصلی
                    for pattern, store_name in url_store_map_priority:
                        if re.search(pattern, item_url, re.IGNORECASE):
                            store = store_name
                            break
                    
                    item_description = parent_text_element.get_text(separator=' ', strip=True)
                    item_description = item_description.replace(item_title, '').replace(item_url, '').strip()
                    if len(item_description) < 20: # اگر توضیحات خیلی کوتاه بود، عنوان را به عنوان توضیحات در نظر بگیر
                        item_description = item_title

                    item_image_tag = parent_text_element.find('img', src=True)
                    item_image_url = item_image_tag['src'] if item_image_tag else None
                    
                    if item_title:
                        found_items.append({
                            "title": clean_title_for_search(item_title), # تمیز کردن عنوان آیتم داخلی با تابع مشترک
                            "store": store,
                            "url": item_url,
                            "image_url": item_image_url,
                            "description": item_description,
                            "id_in_db": self._generate_unique_id(base_post_id, item_url),
                            "subreddit": "AppHookup",
                            "is_free": is_truly_free, # اضافه شدن فیلد is_free
                            "discount_text": discount_text # اضافه شدن فیلد discount_text
                        })
                        if is_truly_free:
                            logger.debug(f"✅ آیتم رایگان داخلی از AppHookup یافت شد: {item_title} (URL: {item_url})")
                        else:
                            logger.debug(f"🔍 آیتم تخفیف‌دار داخلی از AppHookup یافت شد: {item_title} (URL: {item_url}, تخفیف: {discount_text})")
                    else:
                        logger.warning(f"⚠️ آیتم رایگان/تخفیف‌دار داخلی با عنوان خالی از AppHookup نادیده گرفته شد. URL: {item_url}")
                else:
                    logger.debug(f"🔍 آیتم داخلی '{item_title}' از AppHookup رایگان/تخفیف‌دار نبود و نادیده گرفته شد.")
                
        return found_items

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از فید RSS ردیت...")
        free_games_list = []
        processed_ids = set()

        try:
            for subreddit_name, url in self.rss_urls.items():
                logger.info(f"در حال اسکان فید RSS: {url} (ساب‌ردیت: {subreddit_name})...")
                async with aiohttp.ClientSession() as session:
                    await asyncio.sleep(random.uniform(1, 3)) # تأخیر تصادفی برای درخواست RSS
                    async with session.get(url, headers=self.HEADERS) as response:
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

                            raw_title = title_element.text
                            post_id = id_element.text

                            is_truly_free_post = False
                            discount_text_post = None
                            title_lower = raw_title.lower()
                            
                            # منطق تشخیص رایگان بودن/تخفیف‌دار بودن بر اساس کلمات کلیدی در عنوان
                            if subreddit_name == 'FreeGameFindings':
                                is_truly_free_post = True # فرض می‌کنیم تمام پست‌ها از FreeGameFindings واقعاً رایگان هستند
                                logger.debug(f"ℹ️ پست از FreeGameFindings به عنوان رایگان در نظر گرفته شد: {raw_title}")
                            elif "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                                is_truly_free_post = True
                            elif "off" in title_lower: # اگر کلمه "off" بود ولی "free" یا "100% off" نبود
                                is_truly_free_post = False # تخفیف عادی
                                discount_match = re.search(r'(\d+% off)', title_lower)
                                if discount_match:
                                    discount_text_post = discount_match.group(1)
                                else:
                                    discount_text_post = "تخفیف"

                            # مدیریت خاص برای AppHookup weekly deals
                            if subreddit_name == 'AppHookup' and ("weekly" in title_lower and ("deals post" in title_lower or "app deals post" in title_lower or "game deals post" in title_lower)):
                                logger.info(f"🔍 پست 'Weekly Deals' از AppHookup شناسایی شد: {raw_title}. در حال بررسی آیتم‌های داخلی...")
                                weekly_items = self._parse_apphookup_weekly_deals(content_element.text, post_id)
                                for item in weekly_items:
                                    if item['id_in_db'] not in processed_ids:
                                        free_games_list.append(item)
                                        processed_ids.add(item['id_in_db'])
                                        if item['is_free']:
                                            logger.info(f"✅ آیتم رایگان از لیست 'Weekly Deals' ({item['subreddit']}) یافت شد: {item['title']} (فروشگاه: {item['store']})")
                                        else:
                                            logger.info(f"🔍 آیتم تخفیف‌دار از لیست 'Weekly Deals' ({item['subreddit']}) یافت شد: {item['title']} (فروشگاه: {item['store']}, تخفیف: {item['discount_text']})")
                                continue # پس از پردازش آیتم‌های داخلی، به پست بعدی بروید

                            # پردازش پست‌های عادی (غیر از Weekly Deals)
                            if is_truly_free_post or (not is_truly_free_post and discount_text_post):
                                normalized_game = await self._normalize_post_data(session, entry, subreddit_name)
                                if normalized_game:
                                    # اطمینان حاصل می‌کنیم که is_free و discount_text از این تابع استفاده شوند
                                    normalized_game['is_free'] = is_truly_free_post
                                    normalized_game['discount_text'] = discount_text_post

                                    if normalized_game['title'].strip(): # اطمینان از خالی نبودن عنوان
                                        if normalized_game['is_free']:
                                            logger.info(f"✅ پست بازی رایگان از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                                        else:
                                            logger.info(f"⚠️ پست تخفیف‌دار از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']}, تخفیف: {normalized_game['discount_text']})")
                                    else:
                                        logger.warning(f"⚠️ پست بازی رایگان/تخفیف‌دار با عنوان خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {normalized_game['id_in_db']}")
                                        continue # اگر عنوان خالی بود، رد کن

                                    if normalized_game['id_in_db'] not in processed_ids:
                                        free_games_list.append(normalized_game)
                                        processed_ids.add(normalized_game['id_in_db'])
                                    else:
                                        logger.debug(f"ℹ️ پست '{raw_title}' از {subreddit_name} از قبل پردازش شده بود.")
                                else:
                                    logger.debug(f"ℹ️ پست '{raw_title}' از {subreddit_name} نرمال‌سازی نشد.")
                            else:
                                logger.debug(f"🔍 پست '{raw_title}' از {subreddit_name} شرایط 'بازی رایگان' یا 'تخفیف‌دار' را نداشت و نادیده گرفته شد.")

        except Exception as e:
            logger.critical(f"🔥 یک خطای بحرانی پیش‌بینی نشده در ماژول Reddit (RSS) رخ داد: {e}", exc_info=True)
            
        if not free_games_list:
            logger.info("ℹ️ در حال حاضر پست بازی رایگان جدیدی در فیدهای RSS ردیت یافت نشد.")
            
        return free_games_list
