import logging
from typing import List, Dict, Any
from datetime import datetime, timezone
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class EpicGamesSource:
    # استفاده از نقطه پایانی جدید و معتبرتر
    API_URL = "https://store-content-ipv4.ak.epicgames.com/api/en-US/freeGames"
    
    def _normalize_game_data(self, game: Dict[str, Any]) -> Dict[str, str]:
        title = game.get('title', 'بدون عنوان')
        game_id = game.get('id')
        
        image_url = ""
        for img in game.get('keyImages', []):
            if img.get('type') == 'OfferImageWide':
                image_url = img.get('url')
                break
            
        # ساخت URL صفحه محصول
        product_slug = game.get('productSlug')
        if not product_slug and 'urlSlug' in game:
             product_slug = game.get('urlSlug')
        
        # حذف /home از انتهای اسلاگ در صورت وجود
        if product_slug:
            product_slug = product_slug.replace('/home', '')

        product_url = f"https://store.epicgames.com/p/{product_slug}" if product_slug else ""

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
        
        # پارامترهای ضروری برای درخواست
        params = {'country': 'US'}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.API_URL, params=params) as response:
                    response.raise_for_status()
                    data = await response.json()

            games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
            now = datetime.now(timezone.utc)

            for game in games:
                promotions = game.get('promotions')
                if not promotions:
                    continue
                
                # بررسی هر دو نوع پیشنهاد (فعلی و آینده)
                current_offers = promotions.get('promotionalOffers', [])
                upcoming_offers = promotions.get('upcomingPromotionalOffers', [])
                
                all_offers = []
                if current_offers: all_offers.extend(current_offers[0].get('promotionalOffers', []))
                if upcoming_offers: all_offers.extend(upcoming_offers[0].get('promotionalOffers', []))

                for offer in all_offers:
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))

                    # اگر بازی در حال حاضر رایگان است
                    if start_date <= now <= end_date:
                        price = game.get('price', {}).get('totalPrice', {}).get('discountPrice', -1)
                        if price == 0:
                            normalized_game = self._normalize_game_data(game)
                            if not any(g['id_in_db'] == normalized_game['id_in_db'] for g in free_games_list):
                                free_games_list.append(normalized_game)
                                logging.info(f"بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
                                break # برای جلوگیری از اضافه کردن دوباره همان بازی

        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام ارتباط با API اپیک گیمز: {e}")
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول Epic Games رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در Epic Games یافت نشد.")
            
        return free_games_list
