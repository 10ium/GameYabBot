import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp
import re
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class SteamEnricher:
    SEARCH_API_URL = "https://store.steampowered.com/api/storesearch/"
    DETAILS_API_URL = "https://store.steampowered.com/api/appdetails"
    REVIEWS_API_URL = "https://store.steampowered.com/appreviews/{appid}"

    # تابع _clean_title_for_search حذف شد و از utils.clean_title_for_search استفاده می‌شود.

    async def _find_app_id(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        cleaned_title = clean_title_for_search(game_title) # استفاده از تابع مشترک
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
                
                game_info['steam_app_id'] = app_id 

                details_task = asyncio.create_task(
                    session.get(self.DETAILS_API_URL, params={'appids': app_id, 'l': 'english'})
                )
                # درخواست برای نمرات کلی
                reviews_overall_task = asyncio.create_task(
                    session.get(self.REVIEWS_API_URL.format(appid=app_id), params={'json': '1', 'filter': 'all'})
                )
                # درخواست برای نمرات اخیر
                reviews_recent_task = asyncio.create_task(
                    session.get(self.REVIEWS_API_URL.format(appid=app_id), params={'json': '1', 'filter': 'recent'})
                )
                
                details_response, reviews_overall_response, reviews_recent_response = await asyncio.gather(
                    details_task, reviews_overall_task, reviews_recent_task
                )
                
                if details_response.status == 200:
                    details_data = await details_response.json()
                    if details_data.get(app_id, {}).get('success'):
                        game_details = details_data[app_id]['data']
                        
                        if 'genres' in game_details:
                            game_info['genres'] = [genre['description'] for genre in game_details['genres']]
                        if 'movies' in game_details:
                            trailer = next((m['mp4']['max'] for m in game_details['movies'] if 'mp4' in m), None)
                            if trailer:
                                game_info['trailer'] = trailer
                        if 'header_image' in game_details:
                            game_info['image_url'] = game_details['header_image']
                        
                        if 'short_description' in game_details and game_details['short_description'].strip():
                            clean_description = re.sub(r'<[^>]+>', '', game_details['short_description'])
                            game_info['description'] = clean_description.strip()
                        elif 'about_the_game' in game_details and game_details['about_the_game'].strip():
                            clean_description = re.sub(r'<[^>]+>', '', game_details['about_the_game'])
                            game_info['description'] = clean_description.strip()

                        game_info['is_multiplayer'] = False
                        game_info['is_online'] = False
                        if 'categories' in game_details:
                            for category in game_details['categories']:
                                desc = category.get('description', '').lower()
                                if 'multi-player' in desc or 'co-op' in desc:
                                    game_info['is_multiplayer'] = True
                                if 'online' in desc or 'internet' in desc or 'mmo' in desc:
                                    game_info['is_online'] = True
                        
                        # استخراج رده‌بندی سنی
                        if 'content_descriptors' in game_details and 'notes' in game_details['content_descriptors']:
                            # این فیلد معمولا یک لیست از رشته‌هاست. سعی می‌کنیم رده‌بندی سنی را از آن استخراج کنیم.
                            age_notes = game_details['content_descriptors']['notes']
                            if age_notes:
                                # فرض می‌کنیم اولین note ممکن است شامل رده‌بندی باشد
                                game_info['age_rating'] = age_notes[0]
                                logging.info(f"رده‌بندی سنی برای '{game_title}' از Steam یافت شد: {age_notes[0]}")
                        elif 'legal_notice' in game_details and game_details['legal_notice'].strip():
                            # گاهی اوقات رده‌بندی در legal_notice است (مثلاً PEGI)
                            legal_text = game_details['legal_notice']
                            # مثال: "PEGI 12" یا "ESRB Mature"
                            match = re.search(r'(PEGI\s*\d+|ESRB\s*\w+)', legal_text, re.IGNORECASE)
                            if match:
                                game_info['age_rating'] = match.group(0)
                                logging.info(f"رده‌بندی سنی برای '{game_title}' از legal_notice یافت شد: {match.group(0)}")
                                
                # پردازش نمرات کلی Steam
                if reviews_overall_response.status == 200:
                    reviews_data = await reviews_overall_response.json()
                    if reviews_data.get('success'):
                        summary = reviews_data.get('query_summary', {})
                        total_reviews = summary.get('total_reviews', 0)
                        if total_reviews > 5:
                            positive_reviews = summary.get('total_positive', 0)
                            score_percent = round((positive_reviews / total_reviews) * 100)
                            game_info['steam_overall_score'] = score_percent
                            game_info['steam_overall_reviews_count'] = total_reviews
                            logging.info(f"نمره کلی Steam برای '{game_title}' یافت شد: {score_percent}% ({total_reviews} رای)")
                        else:
                            logging.info(f"تعداد رای‌های کلی Steam برای '{game_title}' کافی نیست ({total_reviews}).")
                
                # پردازش نمرات اخیر Steam
                if reviews_recent_response.status == 200:
                    reviews_data = await reviews_recent_response.json()
                    if reviews_data.get('success'):
                        summary = reviews_data.get('query_summary', {})
                        total_reviews = summary.get('total_reviews', 0)
                        if total_reviews > 5:
                            positive_reviews = summary.get('total_positive', 0)
                            score_percent = round((positive_reviews / total_reviews) * 100)
                            game_info['steam_recent_score'] = score_percent
                            game_info['steam_recent_reviews_count'] = total_reviews
                            logging.info(f"نمره اخیر Steam برای '{game_title}' یافت شد: {score_percent}% ({total_reviews} رای)")
                        else:
                            logging.info(f"تعداد رای‌های اخیر Steam برای '{game_title}' کافی نیست ({total_reviews}).")

                if 'steam_overall_score' in game_info or 'steam_recent_score' in game_info or 'genres' in game_info or 'image_url' in game_info or 'description' in game_info or 'age_rating' in game_info:
                    logging.info(f"اطلاعات '{game_title}' با موفقیت از استیم غنی‌سازی شد.")
                else:
                    logging.warning(f"غنی‌سازی اطلاعات برای '{game_title}' از استیم کامل نبود.")
                return game_info
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در SteamEnricher برای '{game_title}': {e}", exc_info=True)
            return game_info
