import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class SteamEnricher:
    SEARCH_API_URL = "https://store.steampowered.com/api/storesearch/"
    DETAILS_API_URL = "https://store.steampowered.com/api/appdetails"
    REVIEWS_API_URL = "https://store.steampowered.com/appreviews/{appid}"

    async def _find_app_id(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        params = {'term': game_title, 'l': 'english', 'cc': 'US'}
        try:
            async with session.get(self.SEARCH_API_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get('total', 0) > 0 and data.get('items'):
                    app_id = data['items'][0].get('id')
                    logging.info(f"App ID برای '{game_title}' در استیم یافت شد: {app_id}")
                    return str(app_id)
                logging.warning(f"هیچ نتیجه‌ای برای '{game_title}' در جستجوی استیم یافت نشد.")
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
                    session.get(self.DETAILS_API_URL, params={'appids': app_id})
                )
                reviews_task = asyncio.create_task(
                    session.get(self.REVIEWS_API_URL.format(appid=app_id), params={'json': '1'})
                )
                details_response, reviews_response = await asyncio.gather(details_task, reviews_task)
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
            if 'steam_score' in game_info:
                logging.info(f"اطلاعات '{game_title}' با موفقیت از استیم غنی‌سازی شد.")
            else:
                logging.warning(f"غنی‌سازی اطلاعات برای '{game_title}' از استیم کامل نبود.")
            return game_info
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در SteamEnricher برای '{game_title}': {e}", exc_info=True)
            return game_info
