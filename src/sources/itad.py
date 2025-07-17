import logging
import asyncio
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import random # برای تأخیر تصادفی
import re # برای استفاده از regex
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError # وارد کردن Playwright و TimeoutError

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO, # می‌توانید برای جزئیات بیشتر به logging.DEBUG تغییر دهید
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__) # تعریف لاگر برای این ماژول

class ITADSource:
    """
    کلاسی برای دریافت بازی‌های رایگان و تخفیف‌دار از طریق اسکرپینگ مستقیم سایت IsThereAnyDeal با Playwright.
    این نسخه به جای فید RSS یا درخواست‌های ساده HTTP، از یک مرورگر بدون رابط کاربری برای بارگذاری
    دینامیک محتوا و اسکرول کردن صفحه استفاده می‌کند.
    """
    # --- *** آدرس صفحه Deals با فیلتر Freebies (100% Off) *** ---
    # این فیلتر "N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA=="
    # برای نمایش بازی‌های 100% تخفیف (رایگان) است.
    BASE_DEALS_URL = "https://isthereanydeal.com/deals/#filter:N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA=="
    
    # می‌توانید User-Agent را در Playwright تنظیم کنید، اما Playwright به صورت پیش‌فرض
    # User-Agent یک مرورگر واقعی را ارسال می‌کند که معمولاً کافی است.
    # HEADERS = {
    #     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    # }

    async def _parse_deal_element(self, deal_tag: BeautifulSoup) -> Optional[Dict[str, Any]]:
        """
        یک تابع کمکی برای تجزیه تگ HTML یک معامله بازی و استخراج اطلاعات مربوطه.
        این تابع انتظار دارد که یک تگ BeautifulSoup (معمولاً <article class="deal">) را دریافت کند.
        """
        try:
            # استخراج عنوان بازی و لینک اصلی ITAD
            title_tag = deal_tag.select_one('h3.game-title a')
            title = title_tag.get_text(strip=True) if title_tag else "بدون عنوان"
            main_link = title_tag['href'] if title_tag and 'href' in title_tag.attrs else "#"
            logger.debug(f"در حال تجزیه عنصر معامله: عنوان='{title}', لینک اصلی='{main_link}'")

            # استخراج نام فروشگاه و لینک مستقیم معامله
            store_tag = deal_tag.select_one('div.deal-store a')
            store = store_tag.get_text(strip=True) if store_tag else "نامشخص"
            deal_url = store_tag['href'] if store_tag and 'href' in store_tag.attrs else main_link
            logger.debug(f"فروشگاه='{store}', لینک معامله='{deal_url}'")

            is_free = False
            discount_text = None

            # بررسی تخفیف 100% یا رایگان بودن
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
                        is_free = False # صریحاً به false تنظیم شود
                        logger.debug(f"بازی '{title}' به عنوان تخفیف‌دار شناسایی شد: {discount_text}")
                    else:
                        discount_text = "تخفیف" # اگر درصد تخفیف مشخص نبود
                        is_free = False
                        logger.debug(f"بازی '{title}' به عنوان تخفیف‌دار (نامشخص) شناسایی شد.")
            else:
                logger.debug(f"تگ 'deal-cut' برای بازی '{title}' یافت نشد. به عنوان غیررایگان در نظر گرفته شد.")
                is_free = False # اگر تگ تخفیف نبود، رایگان نیست

            return {
                "title": title,
                "store": store,
                "url": deal_url,
                "id_in_db": main_link, # لینک اصلی ITAD بهترین شناسه منحصر به فرد است
                "is_free": is_free,
                "discount_text": discount_text
            }
        except Exception as e:
            logger.error(f"❌ خطا در تجزیه عنصر معامله ITAD برای تگ: {deal_tag.prettify()[:200]}... دلیل: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        """
        صفحه Deals سایت ITAD را با استفاده از Playwright اسکرپ کرده و لیست بازی‌های رایگان را استخراج می‌کند.
        این تابع محتوای بارگذاری شده دینامیک را نیز شناسایی می‌کند.
        """
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از صفحه Deals سایت ITAD با Playwright...")
        free_games_list = []
        processed_ids = set() # برای جلوگیری از تکرار بازی‌ها

        async with async_playwright() as p:
            # مرورگر Chromium را در حالت headless (بدون رابط کاربری) اجرا می‌کنیم
            # می‌توانید headless=False را برای مشاهده مرورگر در حین اجرا تنظیم کنید (فقط برای اشکال‌زدایی)
            browser = await p.chromium.launch(headless=True) 
            page = await browser.new_page()

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"در حال باز کردن صفحه Deals: {self.BASE_DEALS_URL} (تلاش {attempt + 1}/{max_retries})")
                    # منتظر بمان تا DOMContentLoaded و سپس تا شبکه بیکار شود (همه درخواست‌ها کامل شوند)
                    await page.goto(self.BASE_DEALS_URL, wait_until='networkidle', timeout=60000) # افزایش timeout
                    logger.debug("صفحه ITAD با موفقیت بارگذاری شد.")
                    break # اگر موفق بود، از حلقه retry خارج شو
                except TimeoutError:
                    logger.warning(f"⚠️ Timeout هنگام بارگذاری صفحه ITAD (تلاش {attempt + 1}/{max_retries}).")
                    if attempt < max_retries - 1:
                        retry_delay = 2 ** attempt + random.uniform(0, 2)
                        logger.info(f"در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.critical(f"🔥 تمام تلاش‌ها برای بارگذاری صفحه ITAD به دلیل Timeout با شکست مواجه شد.")
                        await browser.close()
                        return []
                except Exception as e:
                    logger.critical(f"🔥 خطای پیش‌بینی نشده هنگام بارگذاری صفحه ITAD (تلاش {attempt + 1}/{max_retries}): {e}", exc_info=True)
                    await browser.close()
                    return []
            else: # اگر حلقه for بدون break کامل شد
                logger.critical(f"🔥 تمام {max_retries} تلاش برای بارگذاری صفحه ITAD با شکست مواجه شد.")
                await browser.close()
                return []

            try:
                # منتظر بمان تا حداقل یک عنصر deal قابل مشاهده باشد
                # این اطمینان می‌دهد که محتوای اصلی رندر شده است.
                await page.wait_for_selector('article.deal', state='visible', timeout=30000) 
                logger.debug("اولین عنصر 'article.deal' در صفحه ITAD یافت شد.")
            except TimeoutError:
                logger.warning("⚠️ هیچ عنصر 'article.deal' در زمان مشخص شده (30 ثانیه) یافت نشد. ممکن است محتوا بارگذاری نشده باشد یا سلکتور HTML تغییر کرده باشد.")
                # اگر هیچ dealی پیدا نشد، ادامه می‌دهیم اما لیست خالی خواهد بود.

            # اسکرول کردن به پایین صفحه برای بارگذاری تمام محتوای دینامیک
            previous_height = -1
            scroll_attempts = 0
            max_scroll_attempts = 15 # افزایش حداکثر تعداد تلاش برای اسکرول
            scroll_pause_time = random.uniform(1.5, 3) # تأخیر تصادفی برای اسکرول

            while scroll_attempts < max_scroll_attempts:
                current_scroll_height = await page.evaluate("document.body.scrollHeight")
                if current_scroll_height == previous_height:
                    logger.info("پایان اسکرول: محتوای جدیدی بارگذاری نشد.")
                    break # اگر ارتفاع صفحه تغییر نکرد، به انتهای صفحه رسیده‌ایم
                
                previous_height = current_scroll_height
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                # منتظر بمان تا شبکه دوباره بیکار شود یا حداقل 'scroll_pause_time' ثانیه
                await page.wait_for_loadstate('networkidle', timeout=10000) 
                await asyncio.sleep(scroll_pause_time) # تأخیر تصادفی برای بارگذاری محتوای جدید

                scroll_attempts += 1
                logger.debug(f"اسکرول انجام شد. ارتفاع جدید: {current_scroll_height} پیکسل. تلاش: {scroll_attempts}")
            else:
                logger.warning(f"⚠️ به حداکثر تعداد تلاش برای اسکرول ({max_scroll_attempts}) رسیدیم. ممکن است تمام محتوا بارگذاری نشده باشد.")

            # دریافت محتوای HTML کامل رندر شده پس از اسکرول
            html_content = await page.content()
            logger.debug(f"محتوای HTML رندر شده (بخشی): {html_content[:500]}...") # نمایش بخشی از محتوای HTML
            soup = BeautifulSoup(html_content, 'html.parser')

            # پیدا کردن تمام عناصر deal (معامله)
            deal_elements = soup.select('article.deal')

            if not deal_elements:
                logger.warning("⚠️ هیچ عنصر 'article.deal' در HTML رندر شده یافت نشد. این می‌تواند به دلیل تغییر در ساختار سایت یا عدم بارگذاری صحیح محتوا باشد.")
                await browser.close()
                return [] # اگر هیچ dealی پیدا نشد، لیست خالی برگردان

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
                            free_games_list.append(normalized_game) # اضافه کردن بازی‌های تخفیف‌دار به لیست
                            processed_ids.add(normalized_game['id_in_db'])
                            logger.info(f"🔍 بازی تخفیف‌دار از ITAD یافت شد: {normalized_game['title']} (فروشگاه: {normalized_game['store']}, تخفیف: {normalized_game['discount_text']})")
                    else:
                        logger.debug(f"ℹ️ بازی '{normalized_game['title']}' از ITAD قبلاً پردازش شده بود (ID: {normalized_game['id_in_db']}).")
                else:
                    logger.warning(f"⚠️ نرمال‌سازی عنصر معامله ITAD شماره {i+1} با شکست مواجه شد. این آیتم نادیده گرفته می‌شود.")

            except Exception as e:
                logger.critical(f"🔥 یک خطای پیش‌بینی نشده در ماژول ITAD (اسکرپینگ Playwright) رخ داد: {e}", exc_info=True)
            finally:
                await browser.close() # بستن مرورگر پس از اتمام کار

        if not free_games_list:
            logger.info("ℹ️ در حال حاضر بازی رایگان یا تخفیف‌دار جدیدی در صفحه Deals سایت ITAD یافت نشد.")
            
        return free_games_list
