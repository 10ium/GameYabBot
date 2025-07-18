import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
import re
import random
import os
import hashlib
import time # برای بررسی زمان فایل کش

logger = logging.getLogger(__name__)

class MetacriticEnricher:
    BASE_URL = "https://www.metacritic.com"
    SEARCH_URL = "https://www.metacritic.com/search/game/{query}/results"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive'
    }

    def __init__(self, cache_dir: str = "cache", cache_ttl: int = 86400): # TTL پیش‌فرض 24 ساعت
        self.cache_dir = os.path.join(cache_dir, "metacritic")
        self.cache_ttl = cache_ttl
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(f"نمونه MetacriticEnricher با موفقیت ایجاد شد. دایرکتوری کش: {self.cache_dir}, TTL: {self.cache_ttl} ثانیه.")

    def _get_cache_path(self, url: str) -> str:
        """مسیر فایل کش را بر اساس هش URL تولید می‌کند."""
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        return os.path.join(self.cache_dir, f"{url_hash}.html")

    def _is_cache_valid(self, cache_path: str) -> bool:
        """بررسی می‌کند که آیا فایل کش وجود دارد و منقضی نشده است."""
        if not os.path.exists(cache_path):
            return False
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self.cache_ttl:
            logger.debug(f"[MetacriticEnricher - _is_cache_valid] فایل کش {cache_path} منقضی شده است.")
            return False
        logger.debug(f"[MetacriticEnricher - _is_cache_valid] فایل کش {cache_path} معتبر است.")
        return True

    async def _fetch_with_retry(self, session: aiohttp.ClientSession, url: str, max_retries: int = 3, initial_delay: float = 2) -> Optional[str]:
        """
        یک URL را با مکانیزم retry و exponential backoff واکشی می‌کند و محتوای HTML را برمی‌گرداند.
        """
        cache_path = self._get_cache_path(url)

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [MetacriticEnricher - _fetch_with_retry] بارگذاری محتوا از کش: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                return f.read()

        logger.debug(f"[MetacriticEnricher - _fetch_with_retry] کش برای {url} معتبر نیست یا وجود ندارد. در حال واکشی از وب‌سایت.")
        for attempt in range(max_retries):
            try:
                current_delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.debug(f"[MetacriticEnricher - _fetch_with_retry] تلاش {attempt + 1}/{max_retries} برای واکشی Metacritic URL: {url} (تأخیر: {current_delay:.2f} ثانیه)")
                await asyncio.sleep(current_delay)
                async with session.get(url, headers=self.HEADERS, timeout=15) as response:
                    response.raise_for_status()
                    html_content = await response.text()
                    
                    # ذخیره در کش
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    logger.info(f"✅ [MetacriticEnricher - _fetch_with_retry] محتوا در کش ذخیره شد: {cache_path}")
                    return html_content
            except aiohttp.ClientResponseError as e:
                logger.warning(f"⚠️ [MetacriticEnricher - _fetch_with_retry] خطای HTTP هنگام واکشی Metacritic URL {url} (تلاش {attempt + 1}/{max_retries}): Status {e.status}, Message: '{e.message}'")
                if attempt < max_retries - 1 and e.status in [403, 404, 429, 500, 502, 503, 504]:
                    logger.info(f"[MetacriticEnricher - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.error(f"❌ [MetacriticEnricher - _fetch_with_retry] تمام تلاش‌ها برای واکشی Metacritic URL {url} با شکست مواجه شد. (آخرین خطا: {e.status})")
                    return None
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ [MetacriticEnricher - _fetch_with_retry] خطای Timeout هنگام واکشی Metacritic URL {url} (تلاش {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                    logger.info(f"[MetacriticEnricher - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.error(f"❌ [MetacriticEnricher - _fetch_with_retry] تمام تلاش‌ها برای واکشی Metacritic URL {url} به دلیل Timeout با شکست مواجه شد.")
                    return None
            except Exception as e:
                logger.error(f"❌ [MetacriticEnricher - _fetch_with_retry] خطای پیش‌بینی نشده هنگام واکشی Metacritic URL {url} (تلاش {attempt + 1}/{max_retries}): {e}", exc_info=True)
                return None
        return None

    async def enrich_data(self, game: Dict[str, Any]) -> Dict[str, Any]:
        """
        داده‌های بازی را با اطلاعات از Metacritic غنی‌سازی می‌کند.
        """
        game_title = game.get('title')
        if not game_title:
            logger.warning("⚠️ [MetacriticEnricher] عنوان بازی برای جستجو در Metacritic خالی است. غنی‌سازی Metacritic انجام نشد.")
            return game

        # تمیز کردن عنوان برای جستجو: حذف پرانتزها و محتوای آنها، سپس کاراکترهای خاص
        cleaned_title = re.sub(r'\(.*?\)|\[.*?\]', '', game_title) # حذف (محتوا) و [محتوا]
        cleaned_title = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned_title).strip() # حذف کاراکترهای خاص
        
        if not cleaned_title:
            logger.warning(f"⚠️ [MetacriticEnricher] عنوان تمیز شده برای جستجو در Metacritic خالی است: '{game_title}'. غنی‌سازی Metacritic انجام نشد.")
            return game

        search_url = self.SEARCH_URL.format(query=cleaned_title.replace(' ', '-')) # Metacritic uses hyphens
        logger.info(f"در حال جستجو در Metacritic برای: '{game_title}' (URL: {search_url})")

        async with aiohttp.ClientSession() as session:
            html_content = await self._fetch_with_retry(session, search_url)

        if not html_content:
            logger.info(f"[MetacriticEnricher] صفحه Metacritic برای '{game_title}' یافت نشد یا واکشی با شکست مواجه شد. غنی‌سازی انجام نشد.")
            return game

        soup = BeautifulSoup(html_content, 'html.parser')
        
        # سلکتور برای کانتینر نتایج جستجو
        results_container = soup.find('div', class_='search_results') or \
                            soup.find('ul', class_='search_results') or \
                            soup.find('div', class_='results')

        if not results_container:
            logger.warning(f"⚠️ [MetacriticEnricher] کانتینر نتایج جستجو در Metacritic برای '{game_title}' یافت نشد. ساختار HTML ممکن است تغییر کرده باشد.")
            return game

        # یافتن اولین نتیجه بازی که معمولاً دقیق‌ترین است
        first_result_link = results_container.find('a', class_='title')
        if not first_result_link:
            # Fallback برای ساختارهای مختلف یا زمانی که 'title' تنها نیست
            first_result_link = results_container.find('a', class_='search_result_row') 
            if first_result_link:
                first_result_link = first_result_link.find('a', class_='title') # مطمئن شویم که تگ <a> با کلاس 'title' را می‌گیریم

        if first_result_link and 'href' in first_result_link.attrs:
            game_page_url = self.BASE_URL + first_result_link['href']
            logger.debug(f"[MetacriticEnricher] صفحه بازی Metacritic یافت شد: {game_page_url}")

            game_page_html = await self._fetch_with_retry(session, game_page_url)
            if not game_page_html:
                logger.warning(f"⚠️ [MetacriticEnricher] واکشی صفحه بازی Metacritic برای '{game_title}' با شکست مواجه شد. غنی‌سازی انجام نشد.")
                return game

            game_soup = BeautifulSoup(game_page_html, 'html.parser')

            # استخراج نمره متاکریتیک (منتقدان)
            score_tag = game_soup.find('div', class_='metascore_w')
            if score_tag:
                try:
                    game['metacritic_score'] = int(score_tag.get_text(strip=True))
                    logger.debug(f"[MetacriticEnricher] نمره متاکریتیک (منتقدان) برای '{game_title}' یافت شد: {game['metacritic_score']}")
                except ValueError:
                    logger.warning(f"⚠️ [MetacriticEnricher] نمره متاکریتیک (منتقدان) برای '{game_title}' قابل تبدیل به عدد نبود.")
            else:
                logger.debug(f"[MetacriticEnricher] تگ نمره متاکریتیک (منتقدان) برای '{game_title}' یافت نشد.")

            # استخراج نمره کاربران
            userscore_tag = game_soup.find('div', class_='metascore_w user')
            if userscore_tag:
                try:
                    game['metacritic_userscore'] = float(userscore_tag.get_text(strip=True))
                    logger.debug(f"[MetacriticEnricher] نمره متاکریتیک (کاربران) برای '{game_title}' یافت شد: {game['metacritic_userscore']}")
                except ValueError:
                    logger.warning(f"⚠️ [MetacriticEnricher] نمره متاکریتیک (کاربران) برای '{game_title}' قابل تبدیل به عدد نبود.")
            else:
                logger.debug(f"[MetacriticEnricher] تگ نمره متاکریتیک (کاربران) برای '{game_title}' یافت نشد.")
            
            logger.info(f"✅ [MetacriticEnricher] داده‌های Metacritic برای '{game_title}' با موفقیت غنی‌سازی شد.")

        else:
            logger.info(f"[MetacriticEnricher] صفحه Metacritic برای '{game_title}' یافت نشد. غنی‌سازی انجام نشد.")
            
        return game
