import logging
import asyncio
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import random # برای تأخیر تصادفی
import re # برای استفاده از regex
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError
import os
import hashlib
import time

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO, # می‌توانید برای جزئیات بیشتر به logging.DEBUG تغییر دهید
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ITADSource:
    BASE_DEALS_URL = "https://isthereanydeal.com/deals/#filter:N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA=="
    
    def __init__(self, cache_dir: str = "cache", cache_ttl: int = 3600):
        self.cache_dir = os.path.join(cache_dir, "itad")
        self.cache_ttl = cache_ttl # زمان زندگی کش به ثانیه (مثلاً 3600 ثانیه = 1 ساعت)
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(f"نمونه ITADSource با موفقیت ایجاد شد. دایرکتوری کش: {self.cache_dir}, TTL: {self.cache_ttl} ثانیه.")

    def _get_cache_path(self, url: str) -> str:
        """مسیر فایل کش را بر اساس هش URL تولید می‌کند."""
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        return os.path.join(self.cache_dir, f"{url_hash}.html")

    def _is_cache_valid(self, cache_path: str) -> bool:
        """بررسی می‌کند که آیا فایل کش وجود دارد و منقضی نشده است."""
        if not os.path.exists(cache_path):
            return False
        # بررسی TTL
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self.cache_ttl:
            logger.debug(f"فایل کش {cache_path} منقضی شده است.")
            return False
        logger.debug(f"فایل کش {cache_path} معتبر است.")
        return True

    async def _parse_deal_element(self, deal_tag: BeautifulSoup) -> Optional[Dict[str, Any]]:
        try:
            title_tag = deal_tag.select_one('h3.game-title a')
            title = title_tag.get_text(strip=True) if title_tag else "بدون عنوان"
            main_link = title_tag['href'] if title_tag and 'href' in title_tag.attrs else "#"
            logger.debug(f"در حال تجزیه عنصر معامله: عنوان='{title}', لینک اصلی='{main_link}'")

            store_tag = deal_tag.select_one('div.deal-store a')
            store = store_tag.get_text(strip=True) if store_tag else "نامشخص"
            deal_url = store_tag['href'] if store_tag and 'href' in store_tag.attrs else main_link
            logger.debug(f"فروشگاه='{store}', لینک معامله='{deal_url}'")

            is_free = False
            discount_text = None

            cut_tag = deal_tag.select_one('div.deal-cut')
            if cut_tag:
                cut_text = cut_tag.get_text(strip=True).lower()
                if "100% off" in cut_text or "free" in cut_text:
                    is_free = True
                    discount_text = "100% Off / Free"
                    logger.debug(f"بازی '{title}' به عنوان رایگان شناسایی شد (متن: '{cut_text}')")
                else:
                    discount_match = re.search(r'(\d+% off)', cut_text)
                    if discount_match:
                        discount_text = discount_match.group(1)
                        is_free = False
                        logger.debug(f"بازی '{title}' به عنوان تخفیف‌دار شناسایی شد: {discount_text}")
                    else:
                        discount_text = "تخفیف"
                        is_free = False
                        logger.debug(f"بازی '{title}' به عنوان تخفیف‌دار (نامشخص) شناسایی شد.")
            else:
                logger.debug(f"تگ 'deal-cut' برای بازی '{title}' یافت نشد. به عنوان غیررایگان در نظر گرفته شد.")
                is_free = False

            return {
                "title": title,
                "store": store,
                "url": deal_url,
                "id_in_db": main_link,
                "is_free": is_free,
                "discount_text": discount_text
            }
        except Exception as e:
            logger.error(f"❌ خطا در تجزیه عنصر معامله ITAD برای تگ: {deal_tag.prettify()[:200]}... دلیل: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از صفحه Deals سایت ITAD با Playwright...")
        free_games_list = []
        processed_ids = set()

        cache_path = self._get_cache_path(self.BASE_DEALS_URL)
        html_content = None

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ بارگذاری محتوای ITAD از کش: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        else:
            logger.info(f"کش ITAD معتبر نیست یا وجود ندارد. در حال واکشی از وب‌سایت.")
            browser = None
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True) 
                    page = await browser.new_page()

                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            logger.info(f"در حال باز کردن صفحه Deals: {self.BASE_DEALS_URL} (تلاش {attempt + 1}/{max_retries})")
                            if hasattr(page, 'wait_for_loadstate'):
                                await page.goto(self.BASE_DEALS_URL, wait_until='networkidle', timeout=60000)
                                logger.debug("صفحه ITAD با استفاده از wait_for_loadstate با موفقیت بارگذاری شد.")
                            else:
                                await page.goto(self.BASE_DEALS_URL, timeout=60000)
                                await asyncio.sleep(5)
                                logger.warning("⚠️ 'wait_for_loadstate' در Playwright یافت نشد. از تأخیر ثابت استفاده شد. لطفاً Playwright را به‌روزرسانی کنید.")
                            break
                        except TimeoutError:
                            logger.warning(f"⚠️ Timeout هنگام بارگذاری صفحه ITAD (تلاش {attempt + 1}/{max_retries}).")
                            if attempt < max_retries - 1:
                                retry_delay = 2 ** attempt + random.uniform(0, 2)
                                logger.info(f"در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                                await asyncio.sleep(retry_delay)
                            else:
                                logger.critical(f"🔥 تمام تلاش‌ها برای بارگذاری صفحه ITAD به دلیل Timeout با شکست مواجه شد.")
                                return []
                        except Exception as e:
                            logger.critical(f"🔥 خطای پیش‌بینی نشده هنگام بارگذاری صفحه ITAD (تلاش {attempt + 1}/{max_retries}): {e}", exc_info=True)
                            return []
                    else:
                        logger.critical(f"🔥 تمام {max_retries} تلاش برای بارگذاری صفحه ITAD با شکست مواجه شد.")
                        return []

                    try:
                        await page.wait_for_selector('article.deal', state='visible', timeout=30000) 
                        logger.debug("اولین عنصر 'article.deal' در صفحه ITAD یافت شد.")
                    except TimeoutError:
                        logger.warning("⚠️ هیچ عنصر 'article.deal' در زمان مشخص شده (30 ثانیه) یافت نشد. ممکن است محتوا بارگذاری نشده باشد یا سلکتور HTML تغییر کرده باشد.")

                    previous_height = -1
                    scroll_attempts = 0
                    max_scroll_attempts = 15
                    scroll_pause_time = random.uniform(1.5, 3)

                    while scroll_attempts < max_scroll_attempts:
                        current_scroll_height = await page.evaluate("document.body.scrollHeight")
                        if current_scroll_height == previous_height:
                            logger.info("پایان اسکرول: محتوای جدیدی بارگذاری نشد.")
                            break
                        
                        previous_height = current_scroll_height
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        if hasattr(page, 'wait_for_loadstate'):
                            await page.wait_for_loadstate('networkidle', timeout=10000) 
                        await asyncio.sleep(scroll_pause_time)

                        scroll_attempts += 1
                        logger.debug(f"اسکرول انجام شد. ارتفاع جدید: {current_scroll_height} پیکسل. تلاش: {scroll_attempts}")
                    else:
                        logger.warning(f"⚠️ به حداکثر تعداد تلاش برای اسکرول ({max_scroll_attempts}) رسیدیم. ممکن است تمام محتوا بارگذاری نشده باشد.")

                    html_content = await page.content()
                    logger.debug(f"محتوای HTML رندر شده (بخشی): {html_content[:500]}...")
                    
                    # ذخیره محتوای تازه واکشی شده در کش
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    logger.info(f"✅ محتوای ITAD در کش ذخیره شد: {cache_path}")

            except Exception as e:
                logger.critical(f"🔥 یک خطای بحرانی پیش‌بینی نشده در ماژول ITAD (اسکرپینگ Playwright) رخ داد: {e}", exc_info=True)
                return []
            finally:
                if browser:
                    await browser.close()
                    logger.debug("مرورگر Playwright بسته شد.")

        if not html_content:
            logger.error("❌ محتوای HTML از ITAD (کش یا واکشی) در دسترس نیست.")
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        deal_elements = soup.select('article.deal')

        if not deal_elements:
            logger.warning("⚠️ هیچ عنصر 'article.deal' در HTML رندر شده یافت نشد. این می‌تواند به دلیل تغییر در ساختار سایت یا عدم بارگذاری صحیح محتوا باشد.")
            return []

        logger.info(f"تعداد عناصر 'article.deal' یافت شده: {len(deal_elements)}")

        for i, deal_tag in enumerate(deal_elements):
            normalized_game = await self._parse_deal_element(deal_tag)
            
            if normalized_game:
                if normalized_game['id_in_db'] not in processed_ids:
                    if normalized_game['is_free']:
                        free_games_list.append(normalized_game)
                        processed_ids.add(normalized_game['id_in_db'])
                        logger.info(f"✅ بازی رایگان از ITAD یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']})")
                    else:
                        free_games_list.append(normalized_game)
                        processed_ids.add(normalized_game['id_in_db'])
                        logger.info(f"🔍 بازی تخفیف‌دار از ITAD یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']}, تخفیف: {normalized_game['discount_text']})")
                else:
                    logger.debug(f"ℹ️ بازی '{normalized_game['title']}' از ITAD قبلاً پردازش شده بود (ID: {normalized_game['id_in_db']}).")
            else:
                logger.warning(f"⚠️ نرمال‌سازی عنصر معامله ITAD شماره {i+1} با شکست مواجه شد. این آیتم نادیده گرفته می‌شود.")

        if not free_games_list:
            logger.info("ℹ️ در حال حاضر بازی رایگان یا تخفیف‌دار جدیدی در صفحه Deals سایت ITAD یافت نشد.")
            
        return free_games_list
