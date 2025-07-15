import logging
import aiohttp
from typing import List, Dict, Any, Optional
import json
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class EpicGamesSource:
    """
    کلاسی برای دریافت بازی‌های رایگان با استخراج داده‌های اولیه (JSON) از صفحه وب اپیک گیمز.
    این روش پایدارتر است و نیازی به API ندارد.
    """
    PAGE_URL = "https://store.epicgames.com/en-US/free-games"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    def _normalize_game_data(self, game_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
        try:
            title = game_data.get('title', 'بدون عنوان')
            description = game_data.get('description', 'توضیحات موجود نیست.')
            
            product_slug = game_data.get('productSlug') or game_data.get('urlSlug')
            if not product_slug:
                for mapping in game_data.get('catalogNs', {}).get('mappings', []):
                    if mapping.get('pageType') == 'productHome':
                        product_slug = mapping.get('pageSlug')
                        break
            
            if product_slug:
                 product_slug = product_slug.replace('home', '').strip('/')

            url = f"https://www.epicgames.com/store/en-US/p/{product_slug}" if product_slug else "#"

            image_url = ""
            for img in game_data.get('keyImages', []):
                if img.get('type') == 'OfferImageWide':
                    image_url = img.get('url')
                    break
            
            game_id = game_data.get('id')

            return {
                "title": title, "store": "Epic Games", "url": url,
                "image_url": image_url, "description": description, "id_in_db": f"epic_{game_id}"
            }
        except Exception as e:
            logging.error(f"خطا در نرمال‌سازی داده‌های اپیک گیمز: {e}")
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از Epic Games (روش استخراج JSON)...")
        free_games_list = []
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(self.PAGE_URL) as response:
                    response.raise_for_status()
                    html_content = await response.text()

            soup = BeautifulSoup(html_content, 'html.parser')
            next_data_script = soup.find('script', {'id': '__NEXT_DATA__'})
            
            if not next_data_script:
                logging.error("تگ __NEXT_DATA__ در صفحه اپیک گیمز یافت نشد.")
                return []

            page_data = json.loads(next_data_script.string)
            games_data = page_data.get('props', {}).get('pageProps', {}).get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
            if not games_data: # مسیر جایگزین در ساختار JSON
                 games_data = page_data.get('props', {}).get('pageProps', {}).get('games', {}).get('elements', [])
            
            now = datetime.now(timezone.utc)

            for game in games_data:
                promotions = game.get('promotions')
                if not promotions: continue
                
                current_offers = promotions.get('promotionalOffers', [])
                upcoming_offers = promotions.get('upcomingPromotionalOffers', [])
                
                all_offers_data = []
                if current_offers: all_offers_data.extend(current_offers)
                if upcoming_offers: all_offers_data.extend(upcoming_offers)

                for offer_group in all_offers_data:
                    for offer in offer_group.get('promotionalOffers', []):
                        start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                        end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))

                        if start_date <= now <= end_date:
                            price = game.get('price', {}).get('totalPrice', {}).get('discountPrice', -1)
                            if price == 0:
                                normalized_game = self._normalize_game_data(game)
                                if normalized_game and not any(g['id_in_db'] == normalized_game['id_in_db'] for g in free_games_list):
                                    free_games_list.append(normalized_game)
                                    logging.info(f"بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
                                    break
                    if game.get('title') in [g['title'] for g in free_games_list]: break
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول Epic Games رخ داد: {e}", exc_info=True)
        return free_games_list
