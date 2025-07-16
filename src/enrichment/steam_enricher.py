import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class SteamEnricher:
    SEARCH_API_URL = "https://store.steampowered.com/api/storesearch/"
    DETAILS_API_URL = "https://store.steampowered.com/api/appdetails"
    REVIEWS_API_URL = "https://store.steampowered.com/appreviews/{appid}"

    def _clean_title_for_search(self, title: str) -> str:
        """
        عنوان بازی را برای جستجو در APIهای خارجی تمیز می‌کند.
        حذف عبارات مانند (Game), ($X -> Free), [Platform] و سایر جزئیات اضافی.
        """
        # حذف عبارات براکتی مانند [Windows], [Multi-Platform]
        cleaned_title = re.sub(r'\[[^\]]+\]', '', title).strip()
        # حذف عبارات پرانتزی خاص مانند (Game), ($X -> Free), (X% off), (Free)
        cleaned_title = re.sub(r'\s*\(game\)', '', cleaned_title, flags=re.IGNORECASE).strip()
        cleaned_title = re.sub(r'\s*\(\$.*?-> Free\)', '', cleaned_title, flags=re.IGNORECASE).strip()
        cleaned_title = re.sub(r'\s*\(\d+%\s*off\)', '', cleaned_title, flags=re.IGNORECASE).strip()
        cleaned_title = re.sub(r'\s*\(\s*free\s*\)', '', cleaned_title, flags=re.IGNORECASE).strip()
        # حذف عبارات مربوط به قیمت و تخفیف که ممکن است در عنوان باقی مانده باشند
        cleaned_title = re.sub(r'\b(CA\$|€|\$)\d+(\.\d{1,2})?\s*→\s*Free\b', '', cleaned_title, flags=re.IGNORECASE).strip()
        cleaned_title = re.sub(r'\b\d+(\.\d{1,2})?\s*-->\s*0\b', '', cleaned_title, flags=re.IGNORECASE).strip()
        
        # حذف هرگونه فاصله اضافی
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()
        return cleaned_title

    async def _find_app_id(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        cleaned_title = self._clean_title_for_search(game_title)
        if not cleaned_title:
            logging.warning(f"عنوان تمیز شده برای '{game_title}' خالی است. جستجوی App ID در استیم انجام نشد.")
            return None
            
        params = {'term': cleaned_title, 'l': 'english', 'cc': 'US'}
        try:
            async with session.get(self.SEARCH_API_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get('total', 0) > 0 and data.get('items'):
                    app_id = data['items'][0].get('id')
                    logging.info(f"App ID برای '{game_title}' (تمیز شده: '{cleaned_title}') در استیم یافت شد: {app_id}")
                    return str(app_id)
                logging.warning(f"هیچ نتیجه‌ای برای '{game_title}' (تمیز شده: '{cleaned_title}') در جستجوی استیم یافت نشد.")
                return None
        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام جستجوی App ID برای '{game_title}': {e}")
            return None

    async def enrich_data(self, game_info: Dict[str, Any]) -> Dict[str, Any]:
        game_title = game_info.get('title')
        if not game_title:
            return game_info
        logging.info(f"شروع فرآیند غنی‌سازی اطلاعات برای '{game_title}' از استیم...")
        try:
            async with aiohttp.ClientSession() as session:
                app_id = await self._find_app_id(session, game_title)
                if not app_id:
                    return game_info
                
                details_task = asyncio.create_task(
                    session.get(self.DETAILS_API_URL, params={'appids': app_id, 'l': 'english'})
                )
                reviews_task = asyncio.create_task(
                    session.get(self.REVIEWS_API_URL.format(appid=app_id), params={'json': '1'})
                )
                details_response, reviews_response = await asyncio.gather(details_task, reviews_task)
                
                if details_response.status == 200:
                    details_data = await details_response.json()
                    if details_data.get(app_id, {}).get('success'):
                        game_details = details_data[app_id]['data']
                        
                        # اطلاعات کلی
                        if 'genres' in game_details:
                            game_info['genres'] = [genre['description'] for genre in game_details['genres']]
                        if 'movies' in game_details:
                            trailer = next((m['mp4']['max'] for m in game_details['movies'] if 'mp4' in m), None)
                            if trailer:
                                game_info['trailer'] = trailer
                        if 'header_image' in game_details:
                            game_info['image_url'] = game_details['header_image']
                        
                        # توضیحات: اول short_description را امتحان می‌کنیم، اگر نبود about_the_game
                        if 'short_description' in game_details and game_details['short_description'].strip():
                            clean_description = re.sub(r'<[^>]+>', '', game_details['short_description'])
                            game_info['description'] = clean_description.strip()
                        elif 'about_the_game' in game_details and game_details['about_the_game'].strip():
                            clean_description = re.sub(r'<[^>]+>', '', game_details['about_the_game'])
                            game_info['description'] = clean_description.strip()

                        # اطلاعات چند نفره/آنلاین
                        game_info['is_multiplayer'] = False
                        game_info['is_online'] = False
                        if 'categories' in game_details:
                            for category in game_details['categories']:
                                desc = category.get('description', '').lower()
                                if 'multi-player' in desc or 'co-op' in desc:
                                    game_info['is_multiplayer'] = True
                                if 'online' in desc or 'internet' in desc or 'mmo' in desc:
                                    game_info['is_online'] = True
                                
                if reviews_response.status == 200:
                    reviews_data = await reviews_response.json()
                    if reviews_data.get('success'):
                        summary = reviews_data.get('query_summary', {})
                        total_reviews = summary.get('total_reviews', 0)
                        if total_reviews > 5:
                            positive_reviews = summary.get('total_positive', 0)
                            score_percent = round((positive_reviews / total_reviews) * 100)
                            game_info['steam_score'] = score_percent
                            game_info['steam_reviews_count'] = total_reviews
                            logging.info(f"نمره Steam برای '{game_title}' یافت شد: {score_percent}% ({total_reviews} رای)")
                        else:
                            logging.info(f"تعداد رای‌های Steam برای '{game_title}' کافی نیست ({total_reviews}).")
                
            if 'steam_score' in game_info or 'genres' in game_info or 'image_url' in game_info or 'description' in game_info:
                logging.info(f"اطلاعات '{game_title}' با موفقیت از استیم غنی‌سازی شد.")
            else:
                logging.warning(f"غنی‌سازی اطلاعات برای '{game_title}' از استیم کامل نبود.")
            return game_info
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در SteamEnricher برای '{game_title}': {e}", exc_info=True)
            return game_info
