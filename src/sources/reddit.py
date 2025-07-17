import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
import hashlib
import random # برای تأخیر تصادفی
import os
import time # برای بررسی زمان فایل کش
import utils.clean_title_for_search as title_cleaner # <--- خط اصلاح شده: وارد کردن ماژول به عنوان title_cleaner
from utils.store_detector import infer_store_from_game_data # وارد کردن تابع از ماژول جدید

logging.basicConfig(
    level=logging.INFO, # می‌توانید برای جزئیات بیشتر به logging.DEBUG تغییر دهید
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class RedditSource:
    def __init__(self, cache_dir: str = "cache", cache_ttl: int = 3600): # TTL پیش‌فرض 1 ساعت
        self.subreddits = [
            'GameDeals',
            'FreeGameFindings',
            'googleplaydeals',
            'AppHookup'
        ]
        self.rss_urls = {sub: f"https://www.reddit.com/r/{sub}/new/.rss" for sub in self.subreddits}
        self.HEADERS = {'User-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'} # User-Agent عمومی‌تر برای ردیت
        
        self.cache_dir = os.path.join(cache_dir, "reddit")
        self.cache_ttl = cache_ttl
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(f"[RedditSource] نمونه RedditSource با موفقیت ایجاد شد. دایرکتوری کش: {self.cache_dir}, TTL: {self.cache_ttl} ثانیه.")

    @staticmethod
    def _generate_unique_id(base_id: str, item_url: str) -> str:
        """
        یک شناسه منحصر به فرد بر اساس شناسه اصلی و URL آیتم برای جلوگیری از تکرار ایجاد می‌کند.
        """
        combined_string = f"{base_id}-{item_url}"
        return hashlib.sha256(combined_string.encode()).hexdigest()

    def _get_cache_path(self, url: str) -> str:
        """مسیر فایل کش را بر اساس هش URL تولید می‌کند."""
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        return os.path.join(self.cache_dir, f"{url_hash}.html") # کش RSS و Permalink هر دو HTML هستند

    def _is_cache_valid(self, cache_path: str) -> bool:
        """بررسی می‌کند که آیا فایل کش وجود دارد و منقضی نشده است."""
        if not os.path.exists(cache_path):
            return False
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self.cache_ttl:
            logger.debug(f"[RedditSource - _is_cache_valid] فایل کش {cache_path} منقضی شده است.")
            return False
        logger.debug(f"[RedditSource - _is_cache_valid] فایل کش {cache_path} معتبر است.")
        return True

    async def _fetch_with_retry(self, session: aiohttp.ClientSession, url: str, max_retries: int = 3, initial_delay: float = 2) -> Optional[str]:
        """
        یک URL را با مکانیزم retry و exponential backoff واکشی می‌کند و محتوای HTML را برمی‌گرداند.
        """
        cache_path = self._get_cache_path(url)

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [RedditSource - _fetch_with_retry] بارگذاری محتوا از کش: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                return f.read()

        logger.debug(f"[RedditSource - _fetch_with_retry] کش برای {url} معتبر نیست یا وجود ندارد. در حال واکشی از وب‌سایت.")
        for attempt in range(max_retries):
            try:
                current_delay = initial_delay * (2 ** attempt) + random.uniform(0, 1) # Exponential backoff + jitter
                logger.debug(f"[RedditSource - _fetch_with_retry] تلاش {attempt + 1}/{max_retries} برای واکشی URL: {url} (تأخیر: {current_delay:.2f} ثانیه)")
                await asyncio.sleep(current_delay)
                async with session.get(url, headers=self.HEADERS, timeout=20) as response: # افزایش timeout برای ردیت
                    response.raise_for_status()
                    html_content = await response.text()
                    
                    # ذخیره در کش
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    logger.info(f"✅ [RedditSource - _fetch_with_retry] محتوا در کش ذخیره شد: {cache_path}")
                    return html_content
            except aiohttp.ClientResponseError as e:
                logger.error(f"❌ [RedditSource - _fetch_with_retry] خطای HTTP هنگام واکشی {url} (تلاش {attempt + 1}/{max_retries}): Status {e.status}, Message: '{e.message}'", exc_info=True)
                if attempt < max_retries - 1 and e.status in [403, 429, 500, 502, 503, 504]: # Retry on specific error codes
                    logger.info(f"[RedditSource - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.critical(f"🔥 [RedditSource - _fetch_with_retry] تمام تلاش‌ها برای واکشی {url} با شکست مواجه شد. (آخرین خطا: {e.status})")
                    return None
            except asyncio.TimeoutError:
                logger.error(f"❌ [RedditSource - _fetch_with_retry] خطای Timeout هنگام واکشی {url} (تلاش {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                    logger.info(f"[RedditSource - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.critical(f"🔥 [RedditSource - _fetch_with_retry] تمام تلاش‌ها برای واکشی {url} به دلیل Timeout با شکست مواجه شد.")
                    return None
            except Exception as e:
                logger.critical(f"🔥 [RedditSource - _fetch_with_retry] خطای پیش‌بینی نشده هنگام واکشی {url} (تلاش {attempt + 1}/{max_retries}): {e}", exc_info=True)
                return None
        return None # اگر تمام تلاش‌ها با شکست مواجه شد

    async def _fetch_and_parse_reddit_permalink(self, session: aiohttp.ClientSession, permalink_url: str) -> Optional[str]:
        """
        یک لینک دائمی ردیت را واکشی کرده و اولین لینک خارجی معتبر را از محتوای آن استخراج می‌کند.
        """
        logger.info(f"[RedditSource - _fetch_and_parse_reddit_permalink] در حال واکشی لینک دائمی ردیت برای یافتن لینک خارجی: {permalink_url}")
        html_content = await self._fetch_with_retry(session, permalink_url)
        if not html_content:
            logger.warning(f"⚠️ [RedditSource - _fetch_and_parse_reddit_permalink] واکشی لینک دائمی ردیت '{permalink_url}' با شکست مواجه شد. لینک خارجی استخراج نمی‌شود.")
            return None

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # پیدا کردن div اصلی محتوای پست (سلکتورها ممکن است نیاز به به‌روزرسانی داشته باشند)
            post_content_div = soup.find('div', class_='s19g0207-1') or \
                               soup.find('div', class_='_292iotee39Lmt0Q_h-B5N') or \
                               soup.find('div', class_='_1qeIAgB0cPwnLhDF9XHzvm') or \
                               soup.find('div', class_='_1qeIAgB0cPwnLhDF9XHzvm')
            
            if post_content_div:
                # یافتن تمام لینک‌های داخل محتوای پست
                for a_tag in post_content_div.find_all('a', href=True):
                    href = a_tag['href']
                    # اطمینان حاصل کن که لینک به reddit.com نیست و یک لینک کامل HTTP/HTTPS است
                    if "reddit.com" not in href and href.startswith("http"):
                        logger.debug(f"[RedditSource - _fetch_and_parse_reddit_permalink] لینک خارجی از لینک دائمی ردیت یافت شد: {href}")
                        return href
                logger.warning(f"[RedditSource - _fetch_and_parse_reddit_permalink] هیچ لینک خارجی معتبری در محتوای لینک دائمی ردیت یافت نشد: {permalink_url}")
                return None
            else:
                logger.warning(f"⚠️ [RedditSource - _fetch_and_parse_reddit_permalink] کانتینر محتوای پست در لینک دائمی ردیت '{permalink_url}' یافت نشد. ساختار HTML ممکن است تغییر کرده باشد.")
                return None
        except Exception as e:
            logger.error(f"❌ [RedditSource - _fetch_and_parse_reddit_permalink] خطای پیش‌بینی نشده هنگام تجزیه لینک دائمی ردیت {permalink_url}: {e}", exc_info=True)
            return None

    async def _normalize_post_data(self, session: aiohttp.ClientSession, entry: ET.Element, subreddit_name: str) -> Optional[Dict[str, Any]]:
        """
        داده‌های یک پست RSS ردیت را به فرمت استاندارد پروژه تبدیل می‌کند.
        """
        raw_title = "نامشخص" # مقداردهی اولیه برای استفاده در لاگ‌های خطا
        try:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            title_element = entry.find('atom:title', ns)
            content_element = entry.find('atom:content', ns)
            id_element = entry.find('atom:id', ns)

            if title_element is None or content_element is None or id_element is None:
                logger.debug(f"[RedditSource - _normalize_post_data] پست RSS ناقص در ساب‌ردیت {subreddit_name} یافت شد (عنوان، محتوا یا ID موجود نیست). نادیده گرفته شد.")
                return None

            raw_title = title_element.text
            post_id = id_element.text
            logger.debug(f"[RedditSource - _normalize_post_data] در حال نرمال‌سازی پست ردیت: عنوان='{raw_title}', ID='{post_id}'")
            
            soup = BeautifulSoup(content_element.text, 'html.parser')
            
            final_url = None
            
            # 1. تلاش برای یافتن لینک مستقیم فروشگاه از محتوای پست (غیر از لینک [link] اصلی)
            all_links_in_content = soup.find_all('a', href=True)
            for a_tag in all_links_in_content:
                href = a_tag['href']
                if "reddit.com" in href or not href.startswith("http"):
                    continue
                final_url = href # اولین لینک معتبر خارجی را به عنوان final_url در نظر بگیر
                logger.debug(f"[RedditSource - _normalize_post_data] لینک خارجی از محتوای پست یافت شد: {final_url}")
                break

            # 2. اگر هنوز لینک فروشگاه مستقیم پیدا نشد، لینک [link] اصلی را بررسی کن
            if not final_url:
                link_tag = soup.find('a', string='[link]')
                if link_tag and 'href' in link_tag.attrs:
                    main_post_url = link_tag['href']
                    if "reddit.com" in main_post_url and "/comments/" in main_post_url:
                        logger.debug(f"[RedditSource - _normalize_post_data] لینک [link] به یک لینک دائمی ردیت اشاره دارد: {main_post_url}. در حال واکشی محتوا...")
                        fetched_external_url = await self._fetch_and_parse_reddit_permalink(session, main_post_url)
                        if fetched_external_url:
                            final_url = fetched_external_url
                            logger.debug(f"[RedditSource - _normalize_post_data] لینک خارجی از لینک دائمی ردیت واکشی شد: {final_url}")
                        else:
                            logger.warning(f"⚠️ [RedditSource - _normalize_post_data] لینک خارجی از لینک دائمی ردیت '{main_post_url}' استخراج نشد. از لینک اصلی ردیت استفاده می‌شود.")
                            final_url = main_post_url # Fallback به لینک دائمی ردیت
                    else: # اگر لینک [link] یک URL مستقیم فروشگاه بود
                        final_url = main_post_url
                        logger.debug(f"[RedditSource - _normalize_post_data] لینک [link] یک URL مستقیم فروشگاه است: {final_url}")
                else:
                    logger.debug(f"[RedditSource - _normalize_post_data] لینک [link] در پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد.")
                    link_element = entry.find('atom:link', ns)
                    if link_element is not None and link_element.get('href'):
                        final_url = link_element.get('href')
                        logger.warning(f"⚠️ [RedditSource - _normalize_post_data] هیچ لینک فروشگاه مستقیمی برای '{raw_title}' یافت نشد. از لینک RSS پست استفاده می‌شود: {final_url}")
                    else:
                        logger.warning(f"⚠️ [RedditSource - _normalize_post_data] هیچ URL معتبری برای پست '{raw_title}' از ساب‌ردیت {subreddit_name} یافت نشد. نادیده گرفته شد.")
                        return None
            
            # استفاده از infer_store_from_game_data برای استنتاج نهایی فروشگاه
            # این تابع از utils.store_detector وارد شده است.
            detected_store = infer_store_from_game_data({"url": final_url, "title": raw_title})
            logger.debug(f"[RedditSource - _normalize_post_data] فروشگاه نهایی استنتاج شده برای '{raw_title}': {detected_store}")

            # --- استخراج توضیحات و تصویر ---
            description_tag = soup.find('div', class_='md')
            description = description_tag.get_text(strip=True) if description_tag else ""

            image_tag = soup.find('img', src=True)
            image_url = image_tag['src'] if image_tag else None

            # تمیز کردن عنوان با استفاده از تابع مشترک
            clean_title = title_cleaner.clean_title_for_search(raw_title) # <--- فراخوانی اصلاح شده
            
            if not clean_title:
                clean_title = raw_title.strip()
                if not clean_title:
                    logger.warning(f"⚠️ [RedditSource - _normalize_post_data] پست بازی رایگان با عنوان کاملاً خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {post_id}")
                    return None

            # تعیین is_free و discount_text
            is_truly_free = False
            discount_text = None
            title_lower = raw_title.lower()

            if subreddit_name == 'FreeGameFindings':
                is_truly_free = True 
                logger.debug(f"ℹ️ [RedditSource - _normalize_post_data] پست از FreeGameFindings به عنوان رایگان در نظر گرفته شد: {raw_title}")
            elif "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                is_truly_free = True
                logger.debug(f"[RedditSource - _normalize_post_data] پست '{raw_title}' به عنوان رایگان (کلمه کلیدی) شناسایی شد.")
            elif "off" in title_lower: 
                is_truly_free = False 
                discount_match = re.search(r'(\d+% off)', title_lower)
                if discount_match:
                    discount_text = discount_match.group(1)
                else:
                    discount_text = "تخفیف"
                logger.debug(f"[RedditSource - _normalize_post_data] پست '{raw_title}' به عنوان تخفیف‌دار (کلمه کلیدی) شناسایی شد: {discount_text}")

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
            logger.error(f"❌ [RedditSource - _normalize_post_data] خطا در نرمال‌سازی پست RSS ردیت از ساب‌ردیت {subreddit_name} (عنوان: '{raw_title[:50]}...'): {e}", exc_info=True)
            return None

    def _parse_apphookup_weekly_deals(self, html_content: str, base_post_id: str) -> List[Dict[str, Any]]:
        """
        محتوای HTML پست‌های 'Weekly Deals' از ساب‌ردیت AppHookup را تجزیه می‌کند
        و آیتم‌های رایگان داخلی را استخراج می‌کند.
        """
        found_items = []
        soup = BeautifulSoup(html_content, 'html.parser')
        logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] در حال تجزیه محتوای HTML برای پست Weekly Deals (ID: {base_post_id}).")
        
        # الگوهای URL برای شناسایی فروشگاه‌ها (ترتیب مهم است: خاص‌ترها اول) - اینها دیگر استفاده نمی‌شوند
        # url_store_map_priority = [...]

        # سلکتورهای احتمالی برای آیتم‌های لیست در پست‌های AppHookup
        list_items = soup.find_all(['p', 'li'])
        logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] تعداد تگ‌های <p> یا <li> یافت شده در Weekly Deals: {len(list_items)}")

        for item_element in list_items:
            a_tag = item_element.find('a', href=True)
            if not a_tag:
                logger.debug("[RedditSource - _parse_apphookup_weekly_deals] تگ <a> در عنصر لیست Weekly Deals یافت نشد. نادیده گرفته شد.")
                continue

            text_around_link = item_element.get_text().lower()
            item_title = a_tag.get_text().strip()
            item_url = a_tag['href']
            logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] آیتم داخلی Weekly Deals: عنوان='{item_title}', URL='{item_url}'")

            # از لینک‌های ردیت داخلی و لینک‌های خالی صرف نظر کن
            if "reddit.com" in item_url or not item_url.startswith("http"):
                logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] لینک داخلی ردیت یا لینک نامعتبر برای '{item_title}'. نادیده گرفته شد.")
                continue

            is_truly_free = False
            discount_text = None
            
            # تشخیص "رایگان" یا "تخفیف‌دار"
            if "free" in text_around_link or "-> 0" in text_around_link or "--> 0" in text_around_link or "100% off" in text_around_link:
                if "off" in text_around_link and "100% off" not in text_around_link and "free" not in text_around_link:
                    is_truly_free = False # تخفیف عادی
                    discount_match = re.search(r'(\d+% off)', text_around_link)
                    if discount_match:
                        discount_text = discount_match.group(1)
                    else:
                        discount_text = "تخفیف"
                    logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] آیتم '{item_title}' به عنوان تخفیف‌دار (متن: '{text_around_link}') شناسایی شد.")
                else:
                    is_truly_free = True # واقعا رایگان
                    logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] آیتم '{item_title}' به عنوان رایگان (متن: '{text_around_link}') شناسایی شد.")
            elif "off" in text_around_link: # اگر فقط "off" بود و "free" نبود
                is_truly_free = False # این یک تخفیف است، نه رایگان
                discount_match = re.search(r'(\d+% off)', text_around_link)
                if discount_match:
                    discount_text = discount_match.group(1)
                else:
                    discount_text = "تخفیف"
                logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] آیتم '{item_title}' به عنوان تخفیف‌دار (متن: '{text_around_link}') شناسایی شد.")

            if is_truly_free or (not is_truly_free and discount_text): # اضافه کردن هم رایگان و هم تخفیف‌دار
                # استفاده از infer_store_from_game_data برای استنتاج نهایی فروشگاه
                detected_store = infer_store_from_game_data({"url": item_url, "title": item_title})
                logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] فروشگاه نهایی استنتاج شده برای '{item_title}': {detected_store}")
                
                item_description = item_element.get_text(separator=' ', strip=True)
                item_description = item_description.replace(item_title, '').replace(item_url, '').strip()
                if len(item_description) < 20: 
                    item_description = item_title
                logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] توضیحات داخلی برای '{item_title}': {item_description[:50]}...")

                item_image_tag = item_element.find('img', src=True)
                item_image_url = item_image_tag['src'] if item_image_tag else None
                logger.debug(f"[RedditSource - _parse_apphookup_weekly_deals] تصویر داخلی برای '{item_title}': {item_image_url}")
                
                if item_title:
                    found_items.append({
                        "title": title_cleaner.clean_title_for_search(item_title), # <--- فراخوانی اصلاح شده
                        "store": detected_store, # استفاده از فروشگاه شناسایی شده
                        "url": item_url,
                        "image_url": item_image_url,
                        "description": item_description,
                        "id_in_db": self._generate_unique_id(base_post_id, item_url),
                        "subreddit": "AppHookup",
                        "is_free": is_truly_free, # اضافه شدن فیلد is_free
                        "discount_text": discount_text # اضافه شدن فیلد discount_text
                    })
                    if is_truly_free:
                        logger.info(f"✅ آیتم رایگان داخلی از لیست 'Weekly Deals' (AppHookup) یافت شد: {item_title} (فروشگاه: {detected_store})")
                    else:
                        logger.info(f"🔍 آیتم تخفیف‌دار داخلی از لیست 'Weekly Deals' (AppHookup) یافت شد: {item_title} (فروشگاه: {detected_store}, تخفیف: {discount_text})")
                else:
                    logger.warning(f"⚠️ [RedditSource - _parse_apphookup_weekly_deals] آیتم رایگان/تخفیف‌دار داخلی با عنوان خالی از AppHookup نادیده گرفته شد. URL: {item_url}")
            else:
                logger.debug(f"🔍 [RedditSource - _parse_apphookup_weekly_deals] آیتم داخلی '{item_title}' از AppHookup رایگان/تخفیف‌دار نبود و نادیده گرفته شد.")
                
        return found_items

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از فید RSS ردیت...")
        free_games_list = []
        processed_ids = set()

        async with aiohttp.ClientSession() as session:
            for subreddit_name, url in self.rss_urls.items():
                logger.info(f"در حال اسکان فید RSS: {url} (ساب‌ردیت: {subreddit_name})...")
                rss_content = await self._fetch_with_retry(session, url)
                if not rss_content:
                    logger.error(f"❌ واکشی فید RSS از {url} با شکست مواجه شد. ادامه به ساب‌ردیت بعدی.")
                    continue

                try:
                    root = ET.fromstring(rss_content)
                    logger.debug(f"فید RSS برای {subreddit_name} با موفقیت تجزیه شد.")
                except ET.ParseError as e:
                    logger.error(f"❌ خطای تجزیه محتوای فید RSS از {url}: {e}", exc_info=True)
                    continue
                
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                entries = root.findall('atom:entry', ns)
                logger.debug(f"تعداد پست‌های یافت شده در فید RSS ساب‌ردیت {subreddit_name}: {len(entries)}")

                for entry in entries:
                    title_element = entry.find('atom:title', ns)
                    content_element = entry.find('atom:content', ns)
                    id_element = entry.find('atom:id', ns)

                    if title_element is None or content_element is None or id_element is None:
                        logger.debug(f"پست RSS ناقص در ساب‌ردیت {subreddit_name} یافت شد (عنوان، محتوا یا ID موجود نیست). نادیده گرفته شد.")
                        continue

                    raw_title = title_element.text
                    post_id = id_element.text

                    is_truly_free_post = False
                    discount_text_post = None
                    title_lower = raw_title.lower()
                    
                    # منطق تشخیص رایگان بودن/تخفیف‌دار بودن بر اساس کلمات کلیدی در عنوان
                    if subreddit_name == 'FreeGameFindings':
                        is_truly_free_post = True 
                        logger.debug(f"ℹ️ [RedditSource - fetch_free_games] پست از FreeGameFindings به عنوان رایگان در نظر گرفته شد: {raw_title}")
                    elif "free" in title_lower or "100% off" in title_lower or "100% discount" in title_lower:
                        is_truly_free_post = True
                        logger.debug(f"[RedditSource - fetch_free_games] پست '{raw_title}' به عنوان رایگان (کلمه کلیدی) شناسایی شد.")
                    elif "off" in title_lower: 
                        is_truly_free_post = False 
                        discount_match = re.search(r'(\d+% off)', title_lower)
                        if discount_match:
                            discount_text_post = discount_match.group(1)
                        else:
                            discount_text_post = "تخفیف"
                        logger.debug(f"[RedditSource - fetch_free_games] پست '{raw_title}' به عنوان تخفیف‌دار (کلمه کلیدی) شناسایی شد: {discount_text_post}")

                    # مدیریت خاص برای AppHookup weekly deals
                    if subreddit_name == 'AppHookup' and ("weekly" in title_lower and ("deals post" in title_lower or "app deals post" in title_lower or "game deals post" in title_lower)):
                        logger.info(f"🔍 [RedditSource - fetch_free_games] پست 'Weekly Deals' از AppHookup شناسایی شد: {raw_title}. در حال بررسی آیتم‌های داخلی...")
                        weekly_items = self._parse_apphookup_weekly_deals(content_element.text, post_id)
                        for item in weekly_items:
                            if item['id_in_db'] not in processed_ids:
                                free_games_list.append(item)
                                processed_ids.add(item['id_in_db'])
                                # لاگ‌های آیتم‌های داخلی در _parse_apphookup_weekly_deals انجام می‌شود
                            else:
                                logger.debug(f"ℹ️ [RedditSource - fetch_free_games] آیتم داخلی '{item['title']}' از Weekly Deals قبلاً پردازش شده بود.")
                        continue # پس از پردازش آیتم‌های داخلی، به پست بعدی بروید

                    # پردازش پست‌های عادی (غیر از Weekly Deals)
                    if is_truly_free_post or (not is_truly_free_post and discount_text_post):
                        normalized_game = await self._normalize_post_data(session, entry, subreddit_name)
                        if normalized_game:
                            normalized_game['is_free'] = is_truly_free_post
                            normalized_game['discount_text'] = discount_text_post

                            if normalized_game['title'].strip():
                                if normalized_game['id_in_db'] not in processed_ids:
                                    free_games_list.append(normalized_game)
                                    processed_ids.add(normalized_game['id_in_db'])
                                    if normalized_game['is_free']:
                                        logger.info(f"✅ [RedditSource - fetch_free_games] پست بازی رایگان از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                                    else:
                                        logger.info(f"🔍 [RedditSource - fetch_free_games] پست تخفیف‌دار از RSS ردیت ({normalized_game['subreddit']}) یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']}, تخفیف: {normalized_game['discount_text']})")
                                else:
                                    logger.debug(f"ℹ️ [RedditSource - fetch_free_games] پست '{raw_title}' از {subreddit_name} از قبل پردازش شده بود.")
                            else:
                                logger.warning(f"⚠️ [RedditSource - fetch_free_games] پست بازی رایگان/تخفیف‌دار با عنوان خالی از RSS ردیت ({subreddit_name}) نادیده گرفته شد. ID: {normalized_game['id_in_db']}")
                                continue 
                        else:
                            logger.debug(f"ℹ️ [RedditSource - fetch_free_games] پست '{raw_title}' از {subreddit_name} نرمال‌سازی نشد. نادیده گرفته شد.")
                    else:
                        logger.debug(f"🔍 [RedditSource - fetch_free_games] پست '{raw_title}' از {subreddit_name} شرایط 'بازی رایگان' یا 'تخفیف‌دار' را نداشت و نادیده گرفته شد.")

        if not free_games_list:
            logger.info("ℹ️ [RedditSource - fetch_free_games] در حال حاضر پست بازی رایگان جدیدی در فیدهای RSS ردیت یافت نشد.")
            
        return free_games_list
