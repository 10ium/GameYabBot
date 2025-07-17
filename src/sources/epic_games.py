import logging
import asyncio
from typing import List, Dict, Any, Optional
import aiohttp
from datetime import datetime, timezone
import random # برای تأخیر تصادفی
import os
import hashlib
import json # برای ذخیره/بارگذاری JSON در کش
import time # برای بررسی زمان فایل کش

logger = logging.getLogger(__name__)

class EpicGamesSource:
    GRAPHQL_API_URL = "https://store-content-ipv4.ak.epicgames.com/api/graphql"
    HEADERS = { # هدرها برای درخواست‌های API
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', # User-Agent عمومی‌تر
        'Referer': 'https://www.epicgames.com/store/', # مهم برای جلوگیری از 403
        'Origin': 'https://www.epicgames.com' # اضافه کردن Origin
    }

    def __init__(self, cache_dir: str = "cache", cache_ttl: int = 3600): # TTL پیش‌فرض 1 ساعت
        self.cache_dir = os.path.join(cache_dir, "epic_games")
        self.cache_ttl = cache_ttl
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(f"نمونه EpicGamesSource با موفقیت ایجاد شد. دایرکتوری کش: {self.cache_dir}, TTL: {self.cache_ttl} ثانیه.")

    def _get_cache_path(self, query_hash: str) -> str:
        """مسیر فایل کش را بر اساس هش کوئری GraphQL تولید می‌کند."""
        return os.path.join(self.cache_dir, f"{query_hash}.json")

    def _is_cache_valid(self, cache_path: str) -> bool:
        """بررسی می‌کند که آیا فایل کش وجود دارد و منقضی نشده است."""
        if not os.path.exists(cache_path):
            return False
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self.cache_ttl:
            logger.debug(f"[EpicGamesSource - _is_cache_valid] فایل کش {cache_path} منقضی شده است.")
            return False
        logger.debug(f"[EpicGamesSource - _is_cache_valid] فایل کش {cache_path} معتبر است.")
        return True
    
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
            logger.debug(f"[EpicGamesSource - _normalize_game_data] تصویر برای '{title}' یافت شد: {image_url}")

            product_slug = game.get('productSlug') or game.get('catalogNs', {}).get('mappings', [{}])[0].get('pageSlug') or game.get('urlSlug')
            if product_slug:
                product_slug = product_slug.replace('/home', '')
            logger.debug(f"[EpicGamesSource - _normalize_game_data] Product Slug برای '{title}' استخراج شد: {product_slug}")

            url = f"https://www.epicgames.com/store/p/{product_slug}" if product_slug else "#"
            logger.debug(f"[EpicGamesSource - _normalize_game_data] URL نهایی برای '{title}': {url}")
            
            return {
                "title": title, "store": "Epic Games", "url": url,
                "image_url": image_url, "description": description, "id_in_db": f"epic_{game_id}",
                "is_free": True # بازی‌های اپیک گیمز از این API همیشه رایگان هستند
            }
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"❌ [EpicGamesSource - _normalize_game_data] خطا در نرمال‌سازی داده‌های اپیک گیمز برای بازی با ID: {game.get('id', 'نامشخص')}, عنوان: {game.get('title', 'نامشخص')}: {e}", exc_info=True)
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
        
        # تولید هش برای کش از payload
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()
        cache_path = self._get_cache_path(payload_hash)
        
        data = None
        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [EpicGamesSource - fetch_free_games] بارگذاری پاسخ GraphQL از کش: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.loads(f.read())
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ [EpicGamesSource - fetch_free_games] خطای JSONDecodeError در فایل کش {cache_path}. کش نامعتبر است.")
                    os.remove(cache_path) # حذف کش خراب
                    data = None # مجبور به واکشی مجدد از شبکه
        
        if data is None: # اگر کش نامعتبر بود یا وجود نداشت، از شبکه واکشی کن
            logger.debug(f"[EpicGamesSource - fetch_free_games] کش GraphQL معتبر نیست یا وجود ندارد. در حال واکشی از وب‌سایت.")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                        await asyncio.sleep(random.uniform(3, 8)) 
                        logger.debug(f"[EpicGamesSource - fetch_free_games] تلاش {attempt + 1}/{max_retries} برای دریافت داده از Epic Games API.")
                        async with session.post(self.GRAPHQL_API_URL, json=payload, timeout=15) as response:
                            response.raise_for_status()
                            data = await response.json()
                            
                            # ذخیره در کش
                            with open(cache_path, 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=4)
                            logger.info(f"✅ [EpicGamesSource - fetch_free_games] پاسخ GraphQL در کش ذخیره شد: {cache_path}")
                            break
                except aiohttp.ClientResponseError as e:
                    logger.error(f"❌ [EpicGamesSource - fetch_free_games] خطای HTTP هنگام دریافت از Epic Games API (تلاش {attempt + 1}/{max_retries}): Status {e.status}, Message: '{e.message}', URL: '{e.request_info.url}'", exc_info=True)
                    if attempt < max_retries - 1 and e.status in [403, 429, 500, 502, 503, 504]:
                        retry_delay = 2 ** attempt + random.uniform(0, 2)
                        logger.info(f"[EpicGamesSource - fetch_free_games] در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.critical(f"🔥 [EpicGamesSource - fetch_free_games] تمام تلاش‌ها برای دریافت از Epic Games API با شکست مواجه شد. (آخرین خطا: {e.status})")
                        return []
                except asyncio.TimeoutError:
                    logger.error(f"❌ [EpicGamesSource - fetch_free_games] خطای Timeout هنگام دریافت از Epic Games API (تلاش {attempt + 1}/{max_retries}).")
                    if attempt < max_retries - 1:
                        retry_delay = 2 ** attempt + random.uniform(0, 2)
                        logger.info(f"[EpicGamesSource - fetch_free_games] در حال تلاش مجدد در {retry_delay:.2f} ثانیه...")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.critical(f"🔥 [EpicGamesSource - fetch_free_games] تمام تلاش‌ها برای دریافت از Epic Games API به دلیل Timeout با شکست مواجه شد.")
                        return []
                except Exception as e:
                    logger.critical(f"🔥 [EpicGamesSource - fetch_free_games] یک خطای پیش‌بینی نشده در ماژول Epic Games (GraphQL) رخ داد: {e}", exc_info=True)
                    return []
            
        if data is None:
            logger.critical(f"🔥 [EpicGamesSource - fetch_free_games] داده‌ای از Epic Games API دریافت نشد (پس از کش و تلاش‌های مجدد). لیست خالی برگردانده می‌شود.")
            return []

        games = data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', [])
        logger.info(f"[EpicGamesSource - fetch_free_games] تعداد کل عناصر بازی دریافت شده از Epic Games API: {len(games)}")
        now = datetime.now(timezone.utc)

        for game in games:
            title = game.get('title', 'نامشخص')
            promotions = game.get('promotions')
            
            if not promotions or not promotions.get('promotionalOffers'):
                logger.debug(f"[EpicGamesSource - fetch_free_games] بازی '{title}' پیشنهاد تبلیغاتی فعال ندارد. نادیده گرفته شد.")
                continue
            
            offers = promotions['promotionalOffers'][0].get('promotionalOffers', [])
            if not offers:
                logger.debug(f"[EpicGamesSource - fetch_free_games] بازی '{title}' دارای بخش پیشنهادات است اما هیچ پیشنهاد داخلی ندارد. نadیده گرفته شد.")
                continue

            is_active_free_offer = False
            for offer in offers:
                try:
                    start_date = datetime.fromisoformat(offer['startDate'].replace('Z', '+00:00'))
                    end_date = datetime.fromisoformat(offer['endDate'].replace('Z', '+00:00'))
                    if start_date <= now <= end_date:
                        is_active_free_offer = True
                        logger.debug(f"[EpicGamesSource - fetch_free_games] پیشنهاد رایگان فعال برای '{title}' یافت شد (شروع: {start_date}, پایان: {end_date}).")
                        break
                    else:
                        logger.debug(f"[EpicGamesSource - fetch_free_games] پیشنهاد برای '{title}' فعال نیست (شروع: {start_date}, پایان: {end_date}).")
                except ValueError as ve:
                    logger.warning(f"⚠️ [EpicGamesSource - fetch_free_games] خطای تجزیه تاریخ برای بازی '{title}': {ve}. پیشنهاد نادیده گرفته شد.")
                    continue

            if is_active_free_offer:
                normalized_game = self._normalize_game_data(game)
                if normalized_game:
                    if normalized_game['id_in_db'] not in [g['id_in_db'] for g in free_games_list]:
                        free_games_list.append(normalized_game)
                        logger.info(f"✅ [EpicGamesSource - fetch_free_games] بازی رایگان از Epic Games یافت شد: {normalized_game['title']}")
                    else:
                        logger.debug(f"ℹ️ [EpicGamesSource - fetch_free_games] بازی '{normalized_game['title']}' از Epic Games قبلاً در لیست موقت اضافه شده بود.")
                else:
                    logger.warning(f"⚠️ [EpicGamesSource - fetch_free_games] نرمال‌سازی داده برای بازی Epic Games '{title}' با شکست مواجه شد. این بازی نادیده گرفته می‌شود.")
            else:
                logger.debug(f"[EpicGamesSource - fetch_free_games] بازی '{title}' در حال حاضر رایگان نیست یا پیشنهاد فعال ندارد.")

        if not free_games_list:
            logger.info("ℹ️ [EpicGamesSource - fetch_free_games] در حال حاضر بازی رایگان فعالی از Epic Games یافت نشد.")
            
        return free_games_list
