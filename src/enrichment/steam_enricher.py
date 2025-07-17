import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
import re
import random
import os
import hashlib
import json # <--- این خط اضافه شد
import time

logger = logging.getLogger(__name__)

class SteamEnricher:
    STEAM_API_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=us&l=english"
    STEAM_SEARCH_URL = "https://store.steampowered.com/search/?term={query}&category1=998" # category1=998 for games
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive'
    }

    def __init__(self, cache_dir: str = "cache", cache_ttl: int = 86400): # TTL پیش‌فرض 24 ساعت
        self.cache_dir = os.path.join(cache_dir, "steam")
        self.cache_ttl = cache_ttl
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(f"[SteamEnricher] نمونه SteamEnricher با موفقیت ایجاد شد. دایرکتوری کش: {self.cache_dir}, TTL: {self.cache_ttl} ثانیه.")

    def _get_cache_path(self, url: str, is_json: bool = True) -> str:
        """مسیر فایل کش را بر اساس هش URL تولید می‌کند."""
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        extension = "json" if is_json else "html"
        return os.path.join(self.cache_dir, f"{url_hash}.{extension}")

    def _is_cache_valid(self, cache_path: str) -> bool:
        """بررسی می‌کند که آیا فایل کش وجود دارد و منقضی نشده است."""
        if not os.path.exists(cache_path):
            return False
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self.cache_ttl:
            logger.debug(f"[SteamEnricher - _is_cache_valid] فایل کش {cache_path} منقضی شده است.")
            return False
        logger.debug(f"[SteamEnricher - _is_cache_valid] فایل کش {cache_path} معتبر است.")
        return True

    async def _fetch_with_retry(self, session: aiohttp.ClientSession, url: str, is_json_expected: bool = True, max_retries: int = 3, initial_delay: float = 2) -> Optional[Any]:
        """
        یک URL را با مکانیزم retry و exponential backoff واکشی می‌کند و پاسخ (HTML یا JSON) را برمی‌گرداند.
        """
        cache_path = self._get_cache_path(url, is_json=is_json_expected)

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [SteamEnricher - _fetch_with_retry] بارگذاری محتوا از کش: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if is_json_expected:
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ [SteamEnricher - _fetch_with_retry] خطای JSONDecodeError در فایل کش {cache_path}. کش نامعتبر است. حذف کش.")
                        os.remove(cache_path) # حذف کش خراب
                        # ادامه پیدا می‌کند تا از شبکه واکشی کند
                return content # برای HTML

        logger.debug(f"[SteamEnricher - _fetch_with_retry] کش برای {url} معتبر نیست یا وجود ندارد. در حال واکشی از وب‌سایت.")
        for attempt in range(max_retries):
            try:
                current_delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.debug(f"[SteamEnricher - _fetch_with_retry] تلاش {attempt + 1}/{max_retries} برای واکشی Steam URL: {url} (تأخیر: {current_delay:.2f} ثانیه)")
                await asyncio.sleep(current_delay)
                async with session.get(url, headers=self.HEADERS, timeout=20) as response: # افزایش timeout
                    response.raise_for_status()
                    content = None
                    if is_json_expected:
                        try:
                            content = await response.json()
                        except aiohttp.ContentTypeError:
                            logger.warning(f"⚠️ [SteamEnricher - _fetch_with_retry] پاسخ غیر JSON از Steam API برای {url} (تلاش {attempt + 1}/{max_retries}).")
                            continue # تلاش مجدد
                    else:
                        content = await response.text()
                    
                    # ذخیره در کش
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        f.write(json.dumps(content, ensure_ascii=False) if is_json_expected else content)
                    logger.info(f"✅ [SteamEnricher - _fetch_with_retry] محتوا در کش ذخیره شد: {cache_path}")
                    return content
            except aiohttp.ClientResponseError as e:
                logger.warning(f"⚠️ [SteamEnricher - _fetch_with_retry] خطای HTTP هنگام واکشی Steam URL {url} (تلاش {attempt + 1}/{max_retries}): Status {e.status}, Message: '{e.message}'")
                if attempt < max_retries - 1 and e.status in [403, 429, 500, 502, 503, 504]:
                    logger.info(f"[SteamEnricher - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.error(f"❌ [SteamEnricher - _fetch_with_retry] تمام تلاش‌ها برای واکشی Steam URL {url} با شکست مواجه شد. (آخرین خطا: {e.status})")
                    return None
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ [SteamEnricher - _fetch_with_retry] خطای Timeout هنگام واکشی Steam URL {url} (تلاش {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                    logger.info(f"[SteamEnricher - _fetch_with_retry] در حال تلاش مجدد برای {url}...")
                else:
                    logger.error(f"❌ [SteamEnricher - _fetch_with_retry] تمام تلاش‌ها برای واکشی Steam URL {url} به دلیل Timeout با شکست مواجه شد.")
                    return None
            except Exception as e:
                logger.error(f"❌ [SteamEnricher - _fetch_with_retry] خطای پیش‌بینی نشده هنگام واکشی Steam URL {url} (تلاش {attempt + 1}/{max_retries}): {e}", exc_info=True)
                return None
        return None

    async def _find_steam_app_id(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        """
        Steam App ID را با جستجو بر اساس عنوان بازی پیدا می‌کند.
        """
        # تمیز کردن عنوان برای جستجو: حذف پرانتزها و محتوای آنها، سپس کاراکترهای خاص
        cleaned_title = re.sub(r'\(.*?\)|\[.*?\]', '', game_title) # حذف (محتوا) و [محتوا]
        cleaned_title = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned_title).strip() # حذف کاراکترهای خاص
        
        if not cleaned_title:
            logger.warning(f"⚠️ [SteamEnricher - _find_steam_app_id] عنوان بازی برای جستجوی Steam App ID خالی است: '{game_title}'")
            return None

        search_url = self.STEAM_SEARCH_URL.format(query=cleaned_title)
        logger.info(f"در حال جستجوی Steam App ID برای: '{game_title}' (URL جستجو: {search_url})")

        html_content = await self._fetch_with_retry(session, search_url, is_json_expected=False)
        if not html_content:
            logger.warning(f"⚠️ [SteamEnricher - _find_steam_app_id] جستجوی Steam App ID برای '{game_title}' با شکست مواجه شد (واکشی HTML).")
            return None
        
        if not isinstance(html_content, str):
            logger.error(f"❌ [SteamEnricher - _find_steam_app_id] پاسخ از Steam Search HTML نیست برای '{game_title}'.")
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        first_result_link = soup.find('a', class_='search_result_row', attrs={'data-ds-appid': True})
        
        if first_result_link:
            app_id = first_result_link['data-ds-appid']
            logger.info(f"✅ [SteamEnricher - _find_steam_app_id] اولین Steam App ID برای '{game_title}' یافت شد: {app_id}")
            return app_id
        else:
            logger.warning(f"⚠️ [SteamEnricher - _find_steam_app_id] Steam App ID برای '{game_title}' یافت نشد. (هیچ نتیجه‌ای در صفحه جستجو).")
            return None

    async def enrich_data(self, game: Dict[str, Any]) -> Dict[str, Any]:
        """
        داده‌های بازی را با اطلاعات از Steam غنی‌سازی می‌کند.
        """
        game_title = game.get('title', 'نامشخص')
        store_name = game.get('store', '').lower().replace(' ', '')

        if store_name != 'steam' and 'steam_app_id' not in game:
            logger.debug(f"[SteamEnricher] بازی '{game_title}' از فروشگاه Steam نیست و Steam App ID ندارد. غنی‌سازی Steam نادیده گرفته شد.")
            return game

        app_id = game.get('steam_app_id')
        if not app_id:
            async with aiohttp.ClientSession() as session:
                app_id = await self._find_steam_app_id(session, game_title)
                if not app_id:
                    logger.info(f"[SteamEnricher] Steam App ID برای '{game_title}' یافت نشد. غنی‌سازی Steam انجام نشد.")
                    return game
                game['steam_app_id'] = app_id

        api_url = self.STEAM_API_URL.format(app_id=app_id)
        logger.info(f"در حال دریافت جزئیات بازی از Steam برای App ID: {app_id} (URL: {api_url})")
        
        async with aiohttp.ClientSession() as session:
            data = await self._fetch_with_retry(session, api_url, is_json_expected=True)

        if data and isinstance(data, dict) and str(app_id) in data and data[str(app_id)]['success']:
            details = data[str(app_id)]['data']
            
            game['description'] = details.get('about_the_game', game.get('description', ''))
            game['image_url'] = details.get('header_image', game.get('image_url', ''))
            game['genres'] = [g['description'] for g in details.get('genres', [])]
            game['is_multiplayer'] = details.get('categories') and any(c['description'] in ['Multi-player', 'Online Multi-Player', 'Co-op', 'Online Co-op'] for c in details['categories'])
            game['is_online'] = details.get('categories') and any(c['description'] in ['Online Multi-Player', 'Online Co-op'] for c in details['categories'])
            game['trailer'] = details.get('movies') and details['movies'][0].get('webm', {}).get('480', '')
            game['age_rating'] = details.get('content_descriptors', {}).get('notes', None) or details.get('required_age', None)
            
            if details.get('type') == 'game' and details.get('recommendations'):
                recommendations = details['recommendations']
                game['steam_overall_reviews_count'] = recommendations.get('total')
                
                if 'reviews' in details:
                    review_summary = details['reviews']
                    if 'overall_positive' in review_summary and 'total_reviews' in review_summary and review_summary['total_reviews'] > 0:
                        game['steam_overall_score'] = round((review_summary['overall_positive'] / review_summary['total_reviews']) * 100)
                    if 'recent_positive' in review_summary and 'recent_reviews' in review_summary and review_summary['recent_reviews'] > 0:
                        game['steam_recent_score'] = round((review_summary['recent_positive'] / review_summary['recent_reviews']) * 100)
                        game['steam_recent_reviews_count'] = review_summary['recent_reviews']
                    logger.debug(f"[SteamEnricher] نمرات Steam (از بخش reviews) برای '{game_title}' یافت شد: کلی={game.get('steam_overall_score')}, اخیر={game.get('steam_recent_score')}")
                elif game['steam_overall_reviews_count'] is not None:
                    logger.debug(f"[SteamEnricher] فقط تعداد کل ریویوها برای '{game_title}' در Steam یافت شد: {game['steam_overall_reviews_count']}. درصد قابل محاسبه نیست.")
            else:
                logger.debug(f"[SteamEnricher] اطلاعات ریویو Steam برای '{game_title}' یافت نشد.")

            logger.info(f"✅ [SteamEnricher] داده‌های Steam برای '{game_title}' (App ID: {app_id}) با موفقیت غنی‌سازی شد.")
        else:
            logger.warning(f"⚠️ [SteamEnricher] جزئیات Steam برای App ID {app_id} یافت نشد یا موفقیت‌آمیز نبود. غنی‌سازی Steam انجام نشد. (پاسخ: {data})")
        return game
