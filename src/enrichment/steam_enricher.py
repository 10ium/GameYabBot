import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional
import re
import random # برای تأخیر تصادفی

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SteamEnricher:
    """
    کلاسی برای غنی‌سازی داده‌های بازی با استفاده از اطلاعات Steam.
    این کلاس اطلاعاتی مانند Steam App ID، نمرات، ژانرها و تریلر را از Steam API استخراج می‌کند.
    """
    STEAM_API_BASE_URL = "https://store.steampowered.com/api/"
    STEAM_STORE_BASE_URL = "https://store.steampowered.com/app/"
    
    # User-Agent عمومی‌تر برای درخواست‌ها
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Connection': 'keep-alive'
    }

    async def _get_steam_app_id(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        """
        با استفاده از Steam API، Steam App ID را بر اساس عنوان بازی پیدا می‌کند.
        """
        search_url = f"{self.STEAM_API_BASE_URL}storesearch/?term={game_title}&l=english&cc=us"
        logger.info(f"در حال جستجوی Steam App ID برای: '{game_title}'")
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5)) # تأخیر تصادفی
            async with session.get(search_url, headers=self.HEADERS) as response:
                response.raise_for_status()
                data = await response.json()
                if data and 'items' in data and data['items']:
                    # سعی می‌کنیم دقیق‌ترین تطابق را پیدا کنیم
                    for item in data['items']:
                        if item.get('name', '').lower() == game_title.lower():
                            logger.info(f"Steam App ID دقیق برای '{game_title}' یافت شد: {item['id']}")
                            return str(item['id'])
                    # اگر تطابق دقیق نبود، اولین بازی را برمی‌گردانیم
                    first_item_id = str(data['items'][0]['id'])
                    logger.info(f"اولین Steam App ID برای '{game_title}' یافت شد: {first_item_id}")
                    return first_item_id
                logger.warning(f"Steam App ID برای '{game_title}' یافت نشد.")
                return None
        except aiohttp.ClientResponseError as e:
            logger.error(f"خطای HTTP در دریافت Steam App ID برای '{game_title}': {e.status} - {e.message}")
        except Exception as e:
            logger.error(f"خطا در دریافت Steam App ID برای '{game_title}': {e}", exc_info=True)
        return None

    async def _get_game_details(self, session: aiohttp.ClientSession, app_id: str) -> Dict[str, Any]:
        """
        جزئیات بازی را از Steam API بر اساس App ID دریافت می‌کند.
        """
        details_url = f"{self.STEAM_API_BASE_URL}appdetails/?appids={app_id}&l=english"
        logger.info(f"در حال دریافت جزئیات بازی از Steam برای App ID: {app_id}")
        game_details = {}
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5)) # تأخیر تصادفی
            async with session.get(details_url, headers=self.HEADERS) as response:
                response.raise_for_status()
                data = await response.json()
                if data and app_id in data and data[app_id]['success']:
                    details = data[app_id]['data']
                    
                    # نمرات بررسی (overall و recent)
                    reviews = details.get('recommendations', {})
                    if reviews:
                        # نمرات کلی
                        overall_reviews = reviews.get('total')
                        overall_score_match = re.search(r'(\d+)%', reviews.get('summary', ''))
                        if overall_score_match:
                            game_details['steam_overall_score'] = int(overall_score_match.group(1))
                            game_details['steam_overall_reviews_count'] = overall_reviews
                        
                        # نمرات اخیر (اگر موجود باشد)
                        recent_reviews_url = f"https://store.steampowered.com/appreviews/{app_id}?json=1&filter=recent&num_per_page=0"
                        await asyncio.sleep(random.uniform(0.5, 1.5)) # تأخیر تصادفی برای درخواست بررسی‌های اخیر
                        async with session.get(recent_reviews_url, headers=self.HEADERS) as recent_response:
                            if recent_response.status == 200:
                                recent_data = await recent_response.json()
                                if recent_data and recent_data.get('success') and recent_data.get('query_summary'):
                                    recent_summary = recent_data['query_summary']
                                    recent_score_match = re.search(r'(\d+)%', recent_summary.get('review_score_desc', ''))
                                    if recent_score_match:
                                        game_details['steam_recent_score'] = int(recent_score_match.group(1))
                                        game_details['steam_recent_reviews_count'] = recent_summary.get('total_reviews', 0)
                    
                    # ژانرها
                    genres = [g['description'] for g in details.get('genres', [])]
                    if genres:
                        game_details['genres'] = genres

                    # تریلر (اگر موجود باشد، اولین تریلر وب‌ام)
                    movies = details.get('movies', [])
                    for movie in movies:
                        if movie.get('webm') and movie['webm'].get('480'):
                            game_details['trailer'] = movie['webm']['480']
                            break
                    
                    # بررسی حالت چندنفره و آنلاین
                    categories = details.get('categories', [])
                    game_details['is_multiplayer'] = any(cat['description'] in ['Multi-player', 'Co-op', 'MMO'] for cat in categories)
                    game_details['is_online'] = any(cat['description'] in ['Online Multi-Player', 'Online Co-op', 'MMO'] for cat in categories)

                    # رده‌بندی سنی
                    if 'content_descriptors' in details and 'description' in details['content_descriptors']:
                        game_details['age_rating'] = details['content_descriptors']['description']
                    elif 'required_age' in details and details['required_age'] != 0:
                        game_details['age_rating'] = f"{details['required_age']}+"
                    
                    logger.info(f"جزئیات Steam برای App ID {app_id} با موفقیت دریافت شد.")
                else:
                    logger.warning(f"دریافت جزئیات Steam برای App ID {app_id} ناموفق بود.")
        except aiohttp.ClientResponseError as e:
            logger.error(f"خطای HTTP در دریافت جزئیات Steam برای App ID {app_id}: {e.status} - {e.message}")
        except Exception as e:
            logger.error(f"خطا در دریافت جزئیات Steam برای App ID {app_id}: {e}", exc_info=True)
        return game_details

    async def enrich_data(self, game_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        داده‌های بازی را با اطلاعات Steam غنی‌سازی می‌کند.
        """
        title = game_data.get('title')
        steam_app_id = game_data.get('steam_app_id')

        if not title and not steam_app_id:
            logger.warning("عنوان بازی یا Steam App ID برای غنی‌سازی Steam موجود نیست.")
            return game_data

        async with aiohttp.ClientSession() as session:
            if not steam_app_id:
                steam_app_id = await self._get_steam_app_id(session, title)
                if steam_app_id:
                    game_data['steam_app_id'] = steam_app_id # App ID را به داده‌ها اضافه کن

            if steam_app_id:
                steam_details = await self._get_game_details(session, steam_app_id)
                game_data.update(steam_details) # اطلاعات Steam را ادغام کن
                logger.info(f"داده‌های Steam برای '{title}' (App ID: {steam_app_id}) غنی‌سازی شد.")
            else:
                logger.info(f"Steam App ID برای '{title}' یافت نشد. غنی‌سازی Steam انجام نشد.")
        return game_data

