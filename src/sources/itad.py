import logging
import asyncio
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import random # برای تأخیر تصادفی
import re # برای استفاده از regex
from playwright.async_api import async_playwright, Page, BrowserContext # وارد کردن Playwright

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

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
    
    # می‌توان User-Agent را در Playwright تنظیم کرد، اما Playwright به صورت پیش‌فرض
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

            # استخراج نام فروشگاه و لینک مستقیم معامله
            store_tag = deal_tag.select_one('div.deal-store a')
            store = store_tag.get_text(strip=True) if store_tag else "نامشخص"
            deal_url = store_tag['href'] if store_tag and 'href' in store_tag.attrs else main_link

            is_free = False
            discount_text = None

            # بررسی تخفیف 100% یا رایگان بودن
            # ITAD از کلاس‌های مختلف برای نمایش قیمت و تخفیف استفاده می‌کند.
            # برای 100% تخفیف، معمولاً div.deal-cut محتوای "100% off" یا "Free" را دارد.
            cut_tag = deal_tag.select_one('div.deal-cut')
            if cut_tag:
                cut_text = cut_tag.get_text(strip=True).lower()
                if "100% off" in cut_text or "free" in cut_text:
                    is_free = True
                    discount_text = "100% Off / Free"
                else:
                    # اگر تخفیف عادی بود، متن تخفیف را استخراج کن
                    # این بخش برای تخفیف‌های غیر رایگان است، اما با توجه به فیلتر،
                    # انتظار می‌رود بیشتر موارد رایگان باشند.
                    discount_match = re.search(r'(\d+% off)', cut_text)
                    if discount_match:
                        discount_text = discount_match.group(1)

            return {
                "title": title,
                "store": store,
                "url": deal_url,
                "id_in_db": main_link, # لینک اصلی ITAD بهترین شناسه منحصر به فرد برای جلوگیری از تکرار است
                "is_free": is_free,
                "discount_text": discount_text
            }
        except Exception as e:
            logging.error(f"خطا در تجزیه عنصر معامله ITAD: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[Dict[str, Any]]:
        """
        صفحه Deals سایت ITAD را با استفاده از Playwright اسکرپ کرده و لیست بازی‌های رایگان را استخراج می‌کند.
        این تابع محتوای بارگذاری شده دینامیک را نیز شناسایی می‌کند.
        """
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از صفحه Deals سایت ITAD با Playwright...")
        free_games_list = []
        processed_ids = set() # برای جلوگیری از تکرار بازی‌ها

        async with async_playwright() as p:
            # مرورگر Chromium را در حالت headless (بدون رابط کاربری) اجرا می‌کنیم
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                logging.info(f"در حال باز کردن صفحه Deals: {self.BASE_DEALS_URL}")
                await page.goto(self.BASE_DEALS_URL, wait_until='domcontentloaded')
                await asyncio.sleep(random.uniform(2, 4)) # تأخیر اولیه برای بارگذاری کامل صفحه

                # اسکرول کردن به پایین صفحه برای بارگذاری تمام محتوای دینامیک
                previous_height = -1
                scroll_attempts = 0
                max_scroll_attempts = 10 # حداکثر تعداد تلاش برای اسکرول

                while scroll_attempts < max_scroll_attempts:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1.5, 3)) # تأخیر برای بارگذاری محتوای جدید

                    current_height = await page.evaluate("document.body.scrollHeight")
                    if current_height == previous_height:
                        logging.info("پایان اسکرول: محتوای جدیدی بارگذاری نشد.")
                        break # اگر ارتفاع صفحه تغییر نکرد، به انتهای صفحه رسیده‌ایم
                    
                    previous_height = current_height
                    scroll_attempts += 1
                    logging.info(f"اسکرول انجام شد. ارتفاع جدید: {current_height} پیکسل. تلاش: {scroll_attempts}")

                # دریافت محتوای HTML کامل رندر شده پس از اسکرول
                html_content = await page.content()
                soup = BeautifulSoup(html_content, 'html.parser')

                # پیدا کردن تمام عناصر deal (معامله)
                # بر اساس بازرسی دستی، هر معامله در یک تگ <article> با کلاس "deal" قرار دارد.
                deal_elements = soup.select('article.deal')

                if not deal_elements:
                    logging.warning("هیچ عنصر معامله‌ای (article.deal) در صفحه یافت نشد. ممکن است ساختار HTML تغییر کرده باشد.")

                for deal_tag in deal_elements:
                    normalized_game = await self._parse_deal_element(deal_tag)
                    
                    if normalized_game and normalized_game['id_in_db'] not in processed_ids:
                        # از آنجایی که فیلتر 100% تخفیف اعمال شده است، بیشتر موارد باید رایگان باشند.
                        # اما برای اطمینان، باز هم is_free را چک می‌کنیم.
                        if normalized_game['is_free']:
                            free_games_list.append(normalized_game)
                            processed_ids.add(normalized_game['id_in_db'])
                            logging.info(f"بازی رایگان از ITAD یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']}")
                        else:
                            # این بخش برای بازی‌های تخفیف‌دار است که فیلتر 100% را رد کرده‌اند.
                            # با فیلتر فعلی، نباید زیاد رخ دهد.
                            logging.info(f"بازی تخفیف‌دار از ITAD یافت شد (رایگان نیست): {normalized_game['title']} در فروشگاه {normalized_game['store']} (تخفیف: {normalized_game['discount_text']})")

            except Exception as e:
                logging.error(f"یک خطای پیش‌بینی نشده در ماژول ITAD (اسکرپینگ Playwright) رخ داد: {e}", exc_info=True)
            finally:
                await browser.close() # بستن مرورگر پس از اتمام کار

        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در صفحه Deals سایت ITAD یافت نشد.")
            
        return free_games_list
