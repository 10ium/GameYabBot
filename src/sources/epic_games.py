import logging
from typing import List, Dict, Any
from datetime import datetime, timezone
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class EpicGamesSource:
    API_URL = "https://store-content-ipv4.ak.epicgames.com/api/en-US/freeGames"

    def _normalize_game_data(self, game: Dict[str, Any]) -> Dict[str, str]:
        title = game.get('title', 'بدون عنوان')
        game_id = game.get('id')
        image_url = ""
        for img in game.get('keyImages', []):
            if img.get('type') == 'OfferImageWide':
                image_url = img.get('url')
                break
        url_slug = game.get('urlSlug', '').replace('/home', '')
        product_url = f"https://store.epicgames.com/p/{url_slug}" if url_slug else ""
        description = game.get('description', 'توضیحات موجود نیست.')
        return {
            "title": title,
            "store": "Epic Games",
            "url": product_url,
            "image_url": image_url,
            "description": description,
            "id_in_db": f"epic_{game_id}" 
        }

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از Epic Games...")
        free_games_list = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.API_URL) as response:
                    response.raise_for_status()
                    data = await response.json()
            games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
            now = datetime.now(timezone.utc)
            for game in games:
                promotions = game.get('promotions')
                if not promotions or not promotions.get('promotionalOffers'):
                    continue
                active_offers = promotions['promotionalOffers']
                if active_offers and active_offers[0].get('promotionalOffer'):
                    offer = active_offers[0]['promotionalOffer']
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                    if start_date <= now <= end_date:
                        price = game.get('price', {}).get('totalPrice', {}).get('discountPrice', -1)
                        if price == 0:
                            normalized_game = self._normalize_game_data(game)
                            free_games_list.append(normalized_game)
                            logging.info(f"بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام ارتباط با API اپیک گیمز: {e}")
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول Epic Games رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در Epic Games یافت نشد.")
        return free_games_list
