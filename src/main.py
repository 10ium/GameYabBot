import os
import asyncio
import logging
import json
import re
from typing import List, Dict, Any
import random # برای تأخیر تصادفی
from urllib.parse import urlparse, urlunparse, parse_qs # وارد کردن ماژول‌های تجزیه URL

# وارد کردن ماژول‌های اصلی
from src.core.database import Database
from src.core.telegram_bot import TelegramBot # در حال حاضر غیرفعال است
# وارد کردن منابع داده
from src.sources.itad import ITADSource
from src.sources.reddit import RedditSource
from src.sources.epic_games import EpicGamesSource
# وارد کردن ماژول‌های غنی‌سازی داده
from src.enrichment.steam_enricher import SteamEnricher
from src.enrichment.metacritic_enricher import MetacriticEnricher
# وارد کردن ماژول ترجمه
from src.translation.translator import SmartTranslator
# وارد کردن ابزارهای کمکی
from src.utils.clean_title_for_search import clean_title_for_search as title_cleaner
from src.utils.store_detector import infer_store_from_game_data, normalize_url_for_key

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO, # می‌توانید برای جزئیات بیشتر به logging.DEBUG تغییر دهید
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__) # تعریف لاگر برای این ماژول

# دریافت توکن‌ها از متغیرهای محیطی
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") # این متغیر باید به SmartTranslator پاس داده شود اگر DeepL استفاده می‌شود

# تنظیمات کش سراسری
CACHE_DIR = "cache"
CACHE_TTL = 86400 # 24 ساعت به ثانیه

def _classify_game_type(game: Dict[str, Any]) -> Dict[str, Any]:
    """
    بازی را به عنوان یک بازی کامل یا DLC/محتوای اضافی طبقه‌بندی می‌کند.
    فیلد 'is_dlc_or_addon' را به دیکشنری بازی اضافه می‌کند.
    """
    game['is_dlc_or_addon'] = False # پیش‌فرض

    title_lower = game.get('title', '').lower()
    url_lower = game.get('url', '').lower()
    product_slug_lower = game.get('productSlug', '').lower() # برای Epic Games

    # کلمات کلیدی رایج برای DLC/محتوای اضافی در عنوان
    dlc_keywords = [
        "dlc", "expansion", "season pass", "soundtrack", "artbook", "bonus",
        "pack", "upgrade", "add-on"
    ]
    # کلمات کلیدی که ممکن است در DLC هم باشند اما اگر همراه با "game" یا "full game" باشند، احتمالاً بازی کامل هستند.
    ambiguous_keywords = ["bundle", "edition", "ultimate", "deluxe"]

    # الگوهای خاص برای جلوگیری از false positives برای "bundle", "edition"
    positive_game_keywords = ["game", "full game", "standard edition", "collection", "complete"]

    # بررسی کلمات کلیدی صریح DLC در عنوان
    if any(keyword in title_lower for keyword in dlc_keywords):
        # بررسی برای false positives: اگر کلمه کلیدی DLC وجود دارد اما کلمه کلیدی بازی کامل هم هست
        if not any(pk in title_lower for pk in positive_game_keywords):
            game['is_dlc_or_addon'] = True
            logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان DLC/Addon (عنوان - کلمه کلیدی صریح) طبقه‌بندی شد.")
            return game 
    
    # بررسی کلمات کلیدی مبهم در عنوان (مانند bundle, edition)
    if not game['is_dlc_or_addon'] and any(keyword in title_lower for keyword in ambiguous_keywords):
        if not any(pk in title_lower for pk in positive_game_keywords):
            game['is_dlc_or_addon'] = True
            logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان DLC/Addon (عنوان - کلمه کلیدی مبهم) طبقه‌بندی شد.")
            return game

    # بررسی الگوهای URL/slug برای Epic Games
    if game.get('store', '').lower().replace(' ', '') == 'epicgames':
        if "edition" in product_slug_lower and "standard-edition" not in product_slug_lower:
             game['is_dlc_or_addon'] = True
             logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان DLC/Addon (اسلاگ Epic - Edition) طبقه‌بندی شد.")
        elif any(keyword in product_slug_lower for keyword in dlc_keywords + ambiguous_keywords):
            if not any(pk in title_lower for pk in positive_game_keywords): # اگر اسلاگ شامل کلمه کلیدی DLC/مبهم بود و عنوان شامل کلمه کلیدی بازی نبود
                game['is_dlc_or_addon'] = True
                logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان DLC/Addon (اسلاگ Epic - کلمه کلیدی) طبقه‌بندی شد.")
    
    # بررسی الگوهای URL عمومی
    if "/dlc/" in url_lower or "/addons/" in url_lower or "/soundtrack/" in url_lower or "/artbook/" in url_lower:
        game['is_dlc_or_addon'] = True
        logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان DLC/Addon (URL) طبقه‌بندی شد.")

    logger.debug(f"[_classify_game_type] بازی '{game.get('title')}' به عنوان بازی کامل طبقه‌بندی شد (is_dlc_or_addon: {game['is_dlc_or_addon']}).")
    return game


def _get_deduplication_key(game: Dict[str, Any]) -> str:
    """
    یک کلید منحصر به فرد برای deduplication بازی‌ها ایجاد می‌کند.
    اولویت با URL نرمال‌شده به همراه نام فروشگاه است.
    """
    store_name = infer_store_from_game_data(game) # <--- استفاده از تابع از ماژول جدید

    # اگر بازی تخفیف‌دار است، یک پیشوند اضافه کن تا از پیشنهادهای رایگان متمایز شود
    prefix_discount = "discount_" if not game.get('is_free', True) else ""
    # اگر DLC یا Addon است، یک پیشوند اضافه کن تا از بازی‌های کامل متمایز شود
    prefix_dlc = "dlc_" if game.get('is_dlc_or_addon', False) else ""
    
    # ترکیب پیشوندها
    combined_prefix = f"{prefix_discount}{prefix_dlc}"

    # 1. اولویت با URL نرمال‌شده + نام فروشگاه
    if 'url' in game and game['url'] and game['url'].startswith(('http://', 'https://')):
        normalized_url_part = normalize_url_for_key(game['url']) # <--- استفاده از تابع از ماژول جدید
        if normalized_url_part: # اطمینان حاصل کن که نرمال‌سازی موفقیت‌آمیز بوده و یک کلید معنی‌دار تولید کرده است
            key = f"{combined_prefix}{normalized_url_part}_{store_name}"
            logger.debug(f"[_get_deduplication_key] کلید deduplication بر اساس URL نرمال‌شده و فروشگاه تولید شد: {key}")
            return key
    
    # 2. فال‌بک به Steam App ID + نام فروشگاه (اگر URL مناسب نبود یا موجود نبود)
    if 'steam_app_id' in game and game['steam_app_id']:
        key = f"{combined_prefix}steam_app_{game['steam_app_id']}_{store_name}"
        logger.debug(f"[_get_deduplication_key] کلید deduplication بر اساس Steam App ID و فروشگاه تولید شد: {key}")
        return key
    
    # 3. فال‌بک به عنوان تمیز شده + نام فروشگاه
    cleaned_title = title_cleaner(game.get('title', '')) # <--- فراخوانی اصلاح شده
    if cleaned_title:
        key = f"{combined_prefix}{cleaned_title}_{store_name}"
        logger.debug(f"[_get_deduplication_key] کلید deduplication بر اساس عنوان تمیز شده و فروشگاه تولید شد: {key}")
        return key
    
    # 4. آخرین راه حل: استفاده از id_in_db (شناسه خاص منبع) + هش تصادفی
    fallback_id = game.get('id_in_db', os.urandom(8).hex())
    key = f"{combined_prefix}fallback_{fallback_id}"
    logger.warning(f"⚠️ [_get_deduplication_key] کلید deduplication برای بازی '{game.get('title', 'نامشخص')}' به فال‌بک نهایی متوسل شد: {key}")
    return key

def _merge_game_data(existing_game: Dict[str, Any], new_game: Dict[str, Any]) -> Dict[str, Any]:
    """
    داده‌های یک بازی جدید را در یک بازی موجود ادغام می‌کند و داده‌های کامل‌تر/معتبرتر را اولویت می‌دهد.
    URL اصلی (که بازی رایگان از آن شناسایی شده) حفظ می‌شود.
    """
    merged_game = existing_game.copy()

    # اولویت‌بندی Steam App ID به عنوان شناسه اصلی
    if 'steam_app_id' in new_game and new_game['steam_app_id']:
        merged_game['steam_app_id'] = new_game['steam_app_id']

    # اولویت‌بندی image_url: تصویر با کیفیت بالاتر (معمولا از Steam) یا غیر placeholder
    if 'image_url' in new_game and new_game['image_url']:
        if not merged_game.get('image_url') or \
           "placehold.co" in merged_game['image_url'] or \
           "reddit.com" in merged_game['image_url']:
            merged_game['image_url'] = new_game['image_url']
    
    # اولویت‌بندی description/persian_summary: متن طولانی‌تر یا موجود
    if 'description' in new_game and new_game['description'] and \
       (not merged_game.get('description') or len(new_game['description']) > len(merged_game['description'])):
        merged_game['description'] = new_game['description']
    if 'persian_summary' in new_game and new_game['persian_summary'] and \
       (not merged_game.get('persian_summary') or len(new_game['persian_summary']) > len(merged_game['persian_summary'])):
        merged_game['persian_summary'] = new_game['persian_summary']

    # ادغام نمرات و سایر ویژگی‌ها، با اولویت‌بندی مقادیر غیر خالی
    for key in ['metacritic_score', 'metacritic_userscore',
                 'steam_overall_score', 'steam_overall_reviews_count',
                 'steam_recent_score', 'steam_recent_reviews_count',
                 'genres', 'trailer', 'is_multiplayer', 'is_online', 'age_rating', 'is_free', 'discount_text',
                 'persian_genres', 'persian_age_rating', 'is_dlc_or_addon']:
        if key in new_game and new_game[key]:
            if key in ['is_multiplayer', 'is_online', 'is_free', 'is_dlc_or_addon']:
                merged_game[key] = merged_game.get(key, False) or new_game[key]
            elif key == 'genres' or key == 'persian_genres':
                merged_game[key] = list(set(merged_game.get(key, []) + new_game[key]))
            elif key == 'discount_text' and not merged_game.get('discount_text'):
                merged_game[key] = new_game[key]
            else:
                merged_game[key] = new_game[key]
    
    # اطمینان از اینکه عنوان تمیز شده، بهترین عنوان ممکن است
    if len(title_cleaner(new_game.get('title', ''))) > \
       len(title_cleaner(merged_game.get('title', ''))):
        merged_game['title'] = new_game['title']

    return merged_game

async def enrich_and_translate_game(game: Dict[str, Any], steam_enricher: SteamEnricher, metacritic_enricher: MetacriticEnricher, translator: SmartTranslator) -> Dict[str, Any]:
    """
    بازی را با اطلاعات اضافی غنی‌سازی و توضیحات آن را ترجمه می‌کند،
    با اعمال enricher‌ها بر اساس پلتفرم.
    """
    logger.debug(f"شروع غنی‌سازی و ترجمه برای بازی: '{game.get('title', 'نامشخص')}'")
    
    # استنتاج و به‌روزرسانی فیلد 'store' در دیکشنری بازی
    # این تابع از ماژول store_detector وارد شده است.
    inferred_store = infer_store_from_game_data(game)
    game['store'] = inferred_store 

    store = game.get('store', '').lower().replace(' ', '')

    # تعیین پلتفرم بر اساس فروشگاه
    is_desktop_store = store in ['steam', 'epicgames', 'gog', 'itch.io', 'indiegala', 'stove', 'other', 'reddit', 'microsoftstore', 'humblestore', 'fanatical', 'greenmangaming', 'amazon', 'blizzard', 'eastore', 'ubisoftstore']
    is_console_store = store in ['xbox', 'playstation', 'nintendo']
    is_mobile_store = store in ['google play', 'ios app store', 'epic games (android)', 'epic games (ios)', 'android', 'ios'] # اضافه شدن 'android', 'ios'

    # اعمال SteamEnricher فقط برای بازی‌های دسکتاپ
    if is_desktop_store:
        logger.debug(f"در حال اعمال SteamEnricher برای بازی دسکتاپ: '{game.get('title', 'نامشخص')}'")
        game = await steam_enricher.enrich_data(game)
    else:
        logger.info(f"ℹ️ SteamEnricher برای بازی موبایل/کنسول '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # اعمال MetacriticEnricher برای بازی‌های دسکتاپ، کنسول و موبایل
    if is_desktop_store or is_console_store or is_mobile_store:
        logger.debug(f"در حال اعمال MetacriticEnricher برای بازی: '{game.get('title', 'نامشخص')}'")
        game = await metacritic_enricher.enrich_data(game)
    else:
        logger.info(f"ℹ️ MetacriticEnricher برای بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # ترجمه توضیحات در صورت وجود
    description = game.get('description')
    if description and translator:
        logger.info(f"شروع فرآیند ترجمه برای متن: '{description[:50]}...'")
        game['persian_summary'] = await translator.translate(description)
        logger.info(f"ترجمه با سرویس گوگل موفقیت‌آمیز بود.")
    else:
        logger.debug(f"توضیحات برای بازی '{game.get('title')}' جهت ترجمه موجود نیست یا مترجم فعال نیست.")


    # ترجمه ژانرها در صورت وجود
    genres = game.get('genres')
    if genres and isinstance(genres, list) and translator:
        logger.debug(f"شروع ترجمه ژانرها برای: {genres}")
        translated_genres = []
        for genre in genres:
            translated_genres.append(await translator.translate(genre))
        game['persian_genres'] = translated_genres
        logger.debug(f"ژانرها با موفقیت ترجمه شدند: {translated_genres}")
    else:
        logger.debug(f"ژانرها برای بازی '{game.get('title')}' جهت ترجمه موجود نیستند یا مترجم فعال نیست.")


    # ترجمه رده‌بندی سنی در صورت وجود
    age_rating = game.get('age_rating')
    if age_rating and translator:
        logger.debug(f"شروع ترجمه رده‌بندی سنی برای: {age_rating}")
        game['persian_age_rating'] = await translator.translate(age_rating)
        logger.debug(f"رده‌بندی سنی با موفقیت ترجمه شد: {game['persian_age_rating']}")
    else:
        logger.debug(f"رده‌بندی سنی برای بازی '{game.get('title')}' جهت ترجمه موجود نیست یا مترجم فعال نیست.")


    # طبقه‌بندی نوع بازی (DLC/Addon)
    game = _classify_game_type(game)
    logger.debug(f"طبقه‌بندی نهایی نوع بازی برای '{game.get('title')}': is_dlc_or_addon={game['is_dlc_or_addon']}")

    return game

async def main():
    logger.info("🚀 ربات گیم رایگان شروع به کار کرد...")

    if not TELEGRAM_BOT_TOKEN:
        logger.error("متغیر محیطی TELEGRAM_BOT_TOKEN تنظیم نشده است. برنامه متوقف می‌شود.")
        # return # این خط را کامنت کردم تا برنامه حتی بدون توکن تلگرام هم اجرا شود

    db = Database(db_path="data/games.db")
    # bot = TelegramBot(token=TELEGRAM_BOT_TOKEN, db=db) # غیرفعال کردن موقت ربات تلگرام
    translator = SmartTranslator() 

    # --- مرحله ۱: پردازش دستورات معلق کاربران ---
    # logger.info("در حال پردازش دستورات معلق کاربران (تلگرام غیرفعال است).")
    # await bot.process_pending_updates() # غیرفعال کردن موقت پردازش آپدیت‌ها

    # --- مرحله ۲: نمونه‌سازی و جمع‌آوری داده از تمام منابع ---
    logger.info("🎮 شروع فرآیند یافتن بازی‌های رایگان از منابع مختلف...")
    sources = [
        ITADSource(cache_dir=CACHE_DIR, cache_ttl=CACHE_TTL),
        RedditSource(cache_dir=CACHE_DIR, cache_ttl=CACHE_TTL),
        EpicGamesSource(cache_dir=CACHE_DIR, cache_ttl=CACHE_TTL)
    ]
    
    fetch_tasks = [source.fetch_free_games() for source in sources]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    all_games_raw = []
    for i, result in enumerate(results):
        source_name = sources[i].__class__.__name__
        if isinstance(result, list):
            all_games_raw.extend(result)
            logger.info(f"✅ {len(result)} بازی خام از منبع '{source_name}' یافت شد.")
        elif isinstance(result, Exception):
            logger.error(f"❌ خطای بحرانی در منبع '{source_name}': {result}", exc_info=True) # exc_info=True برای نمایش traceback

    if not all_games_raw:
        logger.info("هیچ بازی از منابع یافت نشد. فایل خروجی با لیست خالی به‌روز می‌شود.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logger.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logger.info(f"✅ مجموعاً {len(all_games_raw)} بازی خام از منابع مختلف جمع‌آوری شد.")

    # --- مرحله ۳: غنی‌سازی و ترجمه تمام بازی‌های یافت شده (قبل از deduplication) ---
    logger.info("✨ شروع فرآیند غنی‌سازی و ترجمه بازی‌ها...")
    steam_enricher = SteamEnricher(cache_dir=CACHE_DIR, cache_ttl=CACHE_TTL)
    metacritic_enricher = MetacriticEnricher(cache_dir=CACHE_DIR, cache_ttl=CACHE_TTL)
    
    enrich_tasks = [
        enrich_and_translate_game(game, steam_enricher, metacritic_enricher, translator)
        for game in all_games_raw
    ]
    
    enriched_games_with_potential_duplicates = await asyncio.gather(*enrich_tasks)
    logger.info(f"✅ {len(enriched_games_with_potential_duplicates)} بازی غنی‌سازی و ترجمه شدند (شامل تکراری‌ها).")
    
    # --- مرحله ۴: deduplication بر اساس کلید منحصر به فرد و انتخاب بهترین نسخه ---
    logger.info("🧹 شروع فرآیند حذف تکراری‌ها و ادغام بازی‌ها...")
    final_unique_games_dict: Dict[str, Dict[str, Any]] = {}
    
    for game in enriched_games_with_potential_duplicates:
        dedup_key = _get_deduplication_key(game)
        if not dedup_key:
            logger.warning(f"⚠️ کلید deduplication برای بازی '{game.get('title', 'نامشخص')}' تولید نشد. این بازی نادیده گرفته می‌شود.")
            continue

        if dedup_key not in final_unique_games_dict:
            final_unique_games_dict[dedup_key] = game
            logger.debug(f"➕ بازی جدید به لیست منحصر به فرد اضافه شد: '{game.get('title', 'نامشخص')}' (کلید: {dedup_key})")
        else:
            existing_game = final_unique_games_dict[dedup_key]
            merged_game = _merge_game_data(existing_game, game)
            final_unique_games_dict[dedup_key] = merged_game
            logger.info(f"✨ بازی تکراری '{game.get('title', 'نامشخص')}' (کلید: {dedup_key}) با موفقیت ادغام شد.")

    final_unique_games = list(final_unique_games_dict.values())

    if not final_unique_games:
        logger.info("پس از deduplication، هیچ بازی منحصر به فردی برای پردازش یافت نشد. فایل خروجی با لیست خالی به‌روز می‌شود.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logger.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logger.info(f"✅ {len(final_unique_games)} بازی منحصر به فرد (پس از deduplication) آماده پردازش نهایی هستند.")

    # --- مرحله ۵: فیلتر کردن بازی‌ها برای ارسال به تلگرام (غیرفعال) ---
    logger.info("📤 بخش ارسال به تلگرام در حال حاضر غیرفعال است.")
    # games_to_post_to_telegram = []
    # for game in final_unique_games:
    #     url = game.get('url')
    #     if game.get('is_free', True) and not game.get('is_dlc_or_addon', False): 
    #         if url and not db.is_game_posted_in_last_30_days(url):
    #             games_to_post_to_telegram.append(game)
    #             logger.debug(f"بازی '{game.get('title')}' برای ارسال به تلگرام انتخاب شد.")
    #         else:
    #             logger.debug(f"ℹ️ بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) قبلاً در ۳۰ روز گذشته پست شده بود یا URL ندارد. به تلگرام ارسال نمی‌شود.")
    #     else:
    #         game_type_info = "تخفیف" if not game.get('is_free', True) else "DLC/Addon"
    #         logger.debug(f"ℹ️ بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}, نوع: {game_type_info}) به تلگرام ارسال نمی‌شود.")


    # --- مرحله ۶: ارسال پیام‌ها به تلگرام (غیرفعال) ---
    # if not games_to_post_to_telegram:
    #     logger.info("هیچ بازی جدیدی برای ارسال به تلگرام (بر اساس فیلتر ۳۰ روز گذشته) یافت نشد.")
    # else:
    #     logger.info(f"📤 {len(games_to_post_to_telegram)} بازی برای ارسال به تلگرام آماده است.")
    #     for game in games_to_post_to_telegram:
    #         store_name = infer_store_from_game_data(game)
    #         targets = db.get_targets_for_store(store_name)
            
    #         if not targets:
    #             logger.warning(f"هیچ مشترکی برای فروشگاه '{store_name}' یافت نشد. از ارسال '{game['title']}' صرف نظر شد.")
    #             continue

    #         logger.info(f"📤 در حال ارسال پیام برای '{game['title']}' به {len(targets)} مقصد...")
    #         send_tasks = [
    #             bot.send_formatted_message(game_data=game, chat_id=chat_id, thread_id=thread_id)
    #             for chat_id, thread_id in targets
    #         ]
    #         await asyncio.gather(*send_tasks, return_exceptions=True)
    #         db.add_posted_game(game['url'])
    #         logger.info(f"✅ بازی '{game['title']}' با موفقیت در دیتابیس ثبت شد.")

    # --- مرحله ۷: ذخیره داده‌های غنی‌شده برای GitHub Pages ---
    logger.info("💾 در حال ذخیره داده‌های بازی‌ها برای GitHub Pages...")
    output_dir = "web_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, "free_games.json")
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(final_unique_games, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ داده‌های بازی‌ها (رایگان و تخفیف‌دار) برای GitHub Pages در {output_file_path} ذخیره شد.")

    db.close()
    logger.info("🏁 کار ربات با موفقیت به پایان رسید.")

if __name__ == "__main__":
    asyncio.run(main())
