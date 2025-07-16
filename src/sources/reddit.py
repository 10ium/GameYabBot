import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import hashlib
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# تابع _clean_title_for_search_common حذف شد و از utils.clean_title_for_search استفاده می‌شود.

class RedditSource:
    def __init__(self):
        self.subreddits = [
            'GameDeals',
            'FreeGameFindings',
            'googleplaydeals',
            'AppHookup'
        ]
        self.rss_urls = {sub: f"https://www.reddit.com/r/{sub}/new/.rss" for sub in self.subreddits}
        logger.info("نمونه RedditSource (نسخه RSS اصلاح شده) با موفقیت ایجاد شد.")

    @staticmethod
    def _generate_unique_id(base_id: str, item_url: str) -> str:
        combined_string = f"{base_id}-{item_url}"
        return hashlib.sha256(combined_string.encode()).hexdigest()

    async def _fetch_and_parse_reddit_permalink(self, session: aiohttp.ClientSession, permalink_url: str) -> Optional[str]:
        """
        یک لینک دائمی ردیت را واکشی کرده و اولین لینک خارجی معتبر را از محتوای آن استخراج می‌کند.
        """
        try:
            logger.info(f"در حال واکشی لینک دائمی ردیت برای یافتن لینک خارجی: {permalink_url}")
            async with session.get(permalink_url, headers={'User-agent': 'GameBeaconBot/1.0'}) as response:
                response.raise_for_status()
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # پیدا کردن div اصلی محتوای پست
                # این سلکتورها ممکن است نیاز به به‌روزرسانی داشته باشند اگر ردیت UI خود را تغییر دهد.
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
                ("apps.apple.com", "ios app store"),
                ("play.google.com", "google play"),
                ("store.steampowered.com", "steam"),
                # Epic Games desktop/mobile links - order matters for specificity
                ("epicgames.com/store/p/.*-android-", "google play"), 
                ("epicgames.com/store/p/.*-ios-", "ios app store"),
                ("epicgames.com/store/p/", "epic games"), # General Epic Desktop, if not mobile
                ("gog.com", "gog"),
                ("xbox.com", "xbox"),
                ("itch.io", "itch.io"),
                ("indiegala.com", "indiegala"),
                ("onstove.com", "stove"),
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
                    else:
                        final_url = main_post_url
                        # فروشگاه را از لینک [link] اصلی حدس بزن
                        for pattern, store_name in url_store_map_priority:
                            if re.search(pattern, final_url, re.IGNORECASE):
                                detected_store = store_name
                                break
                else:
                    logger.debug(f"لینک [link] در پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد.")
                    return None # اگر هیچ لینکی پیدا نشد، پست را نادیده بگیر

            # 3. اگر هنوز URL معتبری پیدا نشد، از URL اصلی پست RSS استفاده کن (کمترین اولویت)
            if not final_url:
                # این URL معمولا به خود پست ردیت اشاره دارد، اما به عنوان آخرین راه حل استفاده می‌شود.
                # این حالت نباید زیاد پیش بیاید اگر لینک [link] به درستی پردازش شود.
                link_element = entry.find('atom:link', ns)
                if link_element is not None and link_element.get('href'):
                    final_url = link_element.get('href')
                    logger.warning(f"هیچ لینک فروشگاه مستقیمی برای '{raw_title}' یافت نشد. از لینک RSS پست استفاده می‌شود: {final_url}")
                else:
                    logger.warning(f"هیچ URL معتبری برای پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد. نادیده گرفته شد.")
                    return None
            
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

            return {
                "title": clean_title,
                "store": detected_store, # استفاده از فروشگاه شناسایی شده
                "url": final_url, # استفاده از URL نهایی
                "image_url": image_url,
                "description": description,
                "id_in_db": post_id, # شناسه پست ردیت به عنوان id_in_db
                "subreddit": subreddit_name
            }
        except Exception as e:
            logger.error(f"❌ خطا در نرمال‌سازی پست RSS ردیت از ساب‌ردیت {subreddit_name}: {e}", exc_info=True)
            return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[Dict[str, Any]]:
        found_items = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        url_store_map_priority = [
            ("apps.apple.com", "ios app store"),
            ("play.google.com", "google play"),
            ("store.steampowered.com", "steam"),
            ("epicgames.com/store/p/.*-android-", "google play"), 
            ("epicgames.com/store/p/.*-ios-", "ios app store"),
            ("epicgames.com/store/p/", "epic games"),
            ("gog.com", "gog"),
            ("xbox.com", "xbox"),
            ("itch.io", "itch.io"),
            ("indiegala.com", "indiegala"),
            ("onstove.com", "stove"),
        ]

        for a_tag in soup.find_all('a', href=True):
            parent_text_element = a_tag.find_parent(['p', 'li'])
            if parent_text_element:
                text_around_link = parent_text_element.get_text().lower()
                item_title = a_tag.get_text().strip()
                item_url = a_tag['href']

                is_free = False
                # تشخیص "رایگان" یا "تخفیف‌دار"
                if "free" in text_around_link or "-> 0" in text_around_link or "--> 0" in text_around_link:
                    if "off" in text_around_link and "100% off" not in text_around_link and "free" not in text_around_link:
                        is_free = False # تخفیف عادی
                    else:
                        is_free = True # واقعا رایگان
                elif "off" in text_around_link: # اگر فقط "off" بود و "free" نبود
                    is_free = False # این یک تخفیف است، نه رایگان

                if is_free: # فقط بازی‌های واقعا رایگان را اضافه کن
                    store = "other"
                    # 1. تلاش برای حدس زدن از URL اصلی
                    for pattern, store_name in url_store_map_priority:
                        if re.search(pattern, item_url, re.IGNORECASE):
                            store = store_name
                            break
                    
                    item_description = parent_text_element.get_text(separator=' ', strip=True)
                    item_description = item_description.replace(item_title, '').replace(item_url, '').strip()
                    if len(item_description) < 20:
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
                            "subreddit": "AppHookup"
                        })
                        logger.debug(f"✅ آیتم رایگان داخلی از AppHookup یافت شد: {item_title} (URL: {item_url})")
                    else:
                        logger.warning(f"⚠️ آیتم رایگان داخلی با عنوان خالی از AppHookup نادیده گرفته شد. URL: {item_url}")
                else:
                    logger.debug(f"🔍 آیتم داخلی '{item_title}' از AppHookup رایگان نبود و نادیده گرفته شد.")
            
        return found_items

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از فید RSS ردیت...")
        free_games_list = []
        processed_ids = set()

        try:
            for subreddit_name, url in self.rss_urls.items():
                logger.info(f"در حال اسکان فید RSS: {url} (ساب‌ردیت: {subreddit_name})...")
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

                            is_truly_free = False
                            is_discounted_but_not_free = False
                            
                            # منطق تشخیص رایگان بودن/تخفیف‌دار بودن بر اساس کلمات کلیدی در عنوان
                            if "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                                is_truly_free = True
                            elif "off" in title_lower: # اگر کلمه "off" بود ولی "free" یا "100% off" نبود
                                is_discounted_but_not_free = True

                            # مدیریت خاص برای AppHookup weekly deals
                            if subreddit_name == 'AppHookup' and ("weekly" in title_lower and ("deals post" in title_lower or "app deals post" in title_lower or "game deals post" in title_lower)):
                                logger.info(f"🔍 پست 'Weekly Deals' از AppHookup شناسایی شد: {title_element.text}. در حال بررسی آیتم‌های داخلی...")
                                weekly_items = self._parse_apphookup_weekly_deals(content_element.text, post_id)
                                for item in weekly_items:
                                    if item['id_in_db'] not in processed_ids:
                                        free_games_list.append(item)
                                        processed_ids.add(item['id_in_db'])
                                        logger.info(f"✅ آیتم رایگان از لیست 'Weekly Deals' ({item['subreddit']}) یافت شد: {item['title']} (فروشگاه: {item['store']})")
                                continue # پس از پردازش آیتم‌های داخلی، به پست بعدی بروید

                            # پردازش پست‌های عادی (غیر از Weekly Deals)
                            if is_truly_free or is_discounted_but_not_free:
                                normalized_game = await self._normalize_post_data(session, entry, subreddit_name)
                                if normalized_game:
                                    if is_discounted_but_not_free:
                                        normalized_game['store'] = "Not Free (Discount)" # اختصاص دسته‌بندی ویژه
                                        logger.info(f"⚠️ پست تخفیف‌دار از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                                    elif normalized_game['title'].strip(): # اطمینان از خالی نبودن عنوان برای بازی‌های واقعا رایگان
                                        logger.info(f"✅ پست بازی رایگان از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                                    else:
                                        logger.warning(f"⚠️ پست بازی رایگان با عنوان خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {normalized_game['id_in_db']}")
                                        continue # اگر عنوان خالی بود، حتی اگر رایگان تشخیص داده شد، رد کن

                                    if normalized_game['id_in_db'] not in processed_ids:
                                        free_games_list.append(normalized_game)
                                        processed_ids.add(normalized_game['id_in_db'])
                                    else:
                                        logger.debug(f"ℹ️ پست '{title_element.text}' از {subreddit_name} از قبل پردازش شده بود.")
                                else:
                                    logger.debug(f"ℹ️ پست '{title_element.text}' از {subreddit_name} نرمال‌سازی نشد.")
                            else:
                                logger.debug(f"🔍 پست '{title_element.text}' از {subreddit_name} شرایط 'بازی رایگان' یا 'تخفیف‌دار' را نداشت و نادیده گرفته شد.")

        except Exception as e:
            logger.critical(f"🔥 یک خطای بحرانی پیش‌بینی نشده در ماژول Reddit (RSS) رخ داد: {e}", exc_info=True)
            
        if not free_games_list:
            logger.info("ℹ️ در حال حاضر پست بازی رایگان جدیدی در فیدهای RSS ردیت یافت نشد.")
            
        return free_games_list
