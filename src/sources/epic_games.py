import logging
from typing import List, Dict, Any, Optional
import aiohttp
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class EpicGamesSource:
    GRAPHQL_API_URL = "https://store-content-ipv4.ak.epicgames.com/api/graphql"
    
    def _normalize_game_data(self, game: Dict[str, Any]) -> Optional[Dict[str, str]]:
        try:
            title = game.get('title', 'بدون عنوان')
            description = game.get('description', 'توضیحات موجود نیست.')
            game_id = game.get('id')
            
            image_url = ""
            for img in game.get('keyImages', []):
                if img.get('type') == 'OfferImageWide':
                    image_url = img.get('url')
                    break
            
            product_slug = game.get('productSlug') or game.get('catalogNs', {}).get('mappings', [{}])[0].get('pageSlug') or game.get('urlSlug')
            if product_slug:
                product_slug = product_slug.replace('/home', '')

            url = f"https://www.epicgames.com/store/p/{product_slug}" if product_slug else "#"
            
            return {
                "title": title, "store": "Epic Games", "url": url,
                "image_url": image_url, "description": description, "id_in_db": f"epic_{game_id}" 
            }
        except (KeyError, IndexError, TypeError) as e:
            logging.error(f"خطا در نرمال‌سازی داده‌های اپیک گیمز: {e}")
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logging.info("شروع فرآیند دریافت بازی‌های رایگان از Epic Games (GraphQL)...")
        free_games_list = []
        query = """
            query searchStoreQuery($country: String!, $locale: String, $category: String) {
                Catalog {
                    searchStore(country: $country, locale: $locale, category: $category) {
                        elements {
                            title
                            id
                            description
                            productSlug
                            urlSlug
                            catalogNs {
                                mappings(pageType: "productHome") {
                                    pageSlug
                                }
                            }
                            keyImages {
                                type
                                url
                            }
                            promotions(category: $category) {
                                promotionalOffers {
                                    promotionalOffers {
                                        startDate
                                        endDate
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """
        variables = {"country": "US", "locale": "en-US", "category": "freegames"}
        payload = {"query": query, "variables": variables}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.GRAPHQL_API_URL, json=payload) as response:
                    response.raise_for_status()
                    data = await response.json()

            games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
            now = datetime.now(timezone.utc)

            for game in games:
                promotions = game.get('promotions')
                if not promotions or not promotions.get('promotionalOffers'):
                    continue
                
                offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
                for offer in offers:
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                    if start_date <= now <= end_date:
                        normalized_game = self._normalize_game_data(game)
                        if normalized_game and not any(g['id_in_db'] == normalized_game['id_in_db'] for g in free_games_list):
                            free_games_list.append(normalized_game)
                            logging.info(f"بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
                            break
        except Exception as e:
            logging.error(f"خطا در ماژول Epic Games (GraphQL): {e}", exc_info=True)
        return free_games_list
