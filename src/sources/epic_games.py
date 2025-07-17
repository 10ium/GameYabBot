import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
from datetime import datetime, timezone
import random # برای تأخیر تصادفی

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # تعریف لاگر برای این ماژول

class EpicGamesSource:
    GRAPHQL_API_URL = "https://store-content-ipv4.ak.epicgames.com/api/graphql"
    HEADERS = { # هدرها برای درخواست‌های API
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', # User-Agent عمومی‌تر
        'Referer': 'https://www.epicgames.com/store/', # مهم برای جلوگیری از 403
        'Origin': 'https://www.epicgames.com' # اضافه کردن Origin
    }
    
    def _normalize_game_data(self, game: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        داده‌های خام بازی از Epic Games را به فرمت استاندارد پروژه تبدیل می‌کند.
        """
        try:
            title = game.get('title', 'بدون عنوان')
            description = game.get('description', 'توضیحات موجود نیست.')
            game_id = game.get('id')
            
            image_url = ""
            for img in game.get('keyImages', []):
                if img.get('type') == 'OfferImageWide': # یا 'VaultHandout' یا 'OfferImageTall'
                    image_url = img.get('url')
                    break
            # اگر OfferImageWide پیدا نشد، یک fallback دیگر
            if not image_url:
                for img in game.get('keyImages', []):
                    if img.get('type') == 'VaultHandout':
                        image_url = img.get('url')
                        break
            if not image_url:
                for img in game.get('keyImages', []):
                    if img.get('type') == 'OfferImageTall':
                        image_url = img.get('url')
                        break

            product_slug = game.get('productSlug') or game.get('catalogNs', {}).get('mappings', [{}])[0].get('pageSlug') or game.get('urlSlug')
            if product_slug:
                product_slug = product_slug.replace('/home', '')

            url = f"https://www.epicgames.com/store/p/{product_slug}" if product_slug else "#"
            
            return {
                "title": title, "store": "Epic Games", "url": url,
                "image_url": image_url, "description": description, "id_in_db": f"epic_{game_id}",
                "is_free": True # بازی‌های اپیک گیمز از این API همیشه رایگان هستند
            }
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"❌ خطا در نرمال‌سازی داده‌های اپیک گیمز برای بازی با ID: {game.get('id', 'نامشخص')}: {e}", exc_info=True)
            return None

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logger.info("🚀 شروع فرآیند دریافت بازی‌های رایگان از Epic Games (GraphQL)...")
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
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                    # تأخیر تصادفی بین 3 تا 8 ثانیه برای کاهش بلاک شدن
                    await asyncio.sleep(random.uniform(3, 8)) 
                    logger.debug(f"تلاش {attempt + 1}/{max_retries} برای دریافت داده از Epic Games API...")
                    async with session.post(self.GRAPHQL_API_URL, json=payload) as response:
                        response.raise_for_status() # اگر وضعیت 200 نباشد، خطا پرتاب می‌کند
                        data = await response.json()
                        break # اگر موفق بود، از حلقه retry خارج شو
            except aiohttp.ClientResponseError as e:
                logger.error(f"❌ خطای HTTP هنگام دریافت از Epic Games API (تلاش {attempt + 1}/{max_retries}): Status {e.status}, Message: '{e.message}', URL: '{e.request_info.url}'", exc_info=True)
                if attempt < max_retries - 1 and e.status in [403, 429, 500, 502, 503, 504]: # Retry on specific error codes
                    retry_delay = 2 ** attempt + random.uniform(0, 2) # Exponential backoff + jitter
                    logger.info(f"در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.critical(f"🔥 تمام تلاش‌ها برای دریافت از Epic Games API با شکست مواجه شد.")
                    return [] # اگر تمام تلاش‌ها با شکست مواجه شد، لیست خالی برگردان
            except asyncio.TimeoutError:
                logger.error(f"❌ خطای Timeout هنگام دریافت از Epic Games API (تلاش {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                    retry_delay = 2 ** attempt + random.uniform(0, 2)
                    logger.info(f"در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.critical(f"🔥 تمام تلاش‌ها برای دریافت از Epic Games API به دلیل Timeout با شکست مواجه شد.")
                    return []
            except Exception as e:
                logger.critical(f"🔥 یک خطای پیش‌بینی نشده در ماژول Epic Games (GraphQL) رخ داد: {e}", exc_info=True)
                return [] # در صورت خطای غیرمنتظره، بلافاصله خاتمه بده
        else: # اگر حلقه for بدون break کامل شد (یعنی تمام تلاش‌ها با شکست مواجه شد)
            logger.critical(f"🔥 تمام {max_retries} تلاش برای دریافت از Epic Games API با شکست مواجه شد.")
            return []

        games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
        now = datetime.now(timezone.utc)

        for game in games:
            promotions = game.get('promotions')
            if not promotions or not promotions.get('promotionalOffers'):
                logger.debug(f"بازی '{game.get('title', 'نامشخص')}' پیشنهاد تبلیغاتی فعال ندارد. نادیده گرفته شد.")
                continue
            
            offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
            is_active_free_offer = False
            for offer in offers:
                try:
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                    if start_date <= now <= end_date:
                        is_active_free_offer = True
                        break # اگر یک پیشنهاد فعال پیدا شد، از حلقه خارج شو
                except ValueError as ve:
                    logger.warning(f"⚠️ خطای تجزیه تاریخ برای بازی '{game.get('title', 'نامشخص')}': {ve}")
                    continue

            if is_active_free_offer:
                normalized_game = self._normalize_game_data(game)
                if normalized_game:
                    # اطمینان از اینکه بازی تکراری نیست (بر اساس id_in_db)
                    # Deduplication نهایی در main.py انجام می‌شود، اینجا فقط از تکرار اولیه جلوگیری می‌کنیم
                    if normalized_game['id_in_db'] not in [g['id_in_db'] for g in free_games_list]:
                        free_games_list.append(normalized_game)
                        logger.info(f"✅ بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
                    else:
                        logger.debug(f"ℹ️ بازی '{normalized_game['title']}' از Epic Games قبلاً در لیست موقت اضافه شده بود.")
                else:
                    logger.warning(f"⚠️ نرمال‌سازی داده برای بازی Epic Games '{game.get('title', 'نامشخص')}' با شکست مواجه شد.")
            else:
                logger.debug(f"بازی '{game.get('title', 'نامشخص')}' در حال حاضر رایگان نیست یا پیشنهاد فعال ندارد.")

        if not free_games_list:
            logger.info("ℹ️ در حال حاضر بازی رایگان فعالی از Epic Games یافت نشد.")
            
        return free_games_list
