import os
import asyncio
import logging
import json
import re
from typing import List, Dict, Any

from core.database import Database
from core.telegram_bot import TelegramBot
from sources.itad import ITADSource
from sources.reddit import RedditSource
from sources.epic_games import EpicGamesSource
from enrichment.steam_enricher import SteamEnricher
from enrichment.metacritic_enricher import MetacriticEnricher
from translation.translator import SmartTranslator
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

def _get_deduplication_key(game: Dict[str, Any]) -> str:
    """
    یک کلید منحصر به فرد برای deduplication بازی‌ها ایجاد می‌کند.
    اولویت با Steam App ID است، سپس با یک عنوان بسیار تمیز شده.
    این کلید اکنون شامل نام فروشگاه نیز می‌شود تا پیشنهادهای رایگان از فروشگاه‌های مختلف برای یک بازی،
    به عنوان ورودی‌های جداگانه در نظر گرفته شوند.
    """
    store_name = game.get('store', 'unknown').lower().replace(' ', '')
    if 'steam_app_id' in game and game['steam_app_id']:
        # اگر Steam App ID موجود است، از آن به همراه نام فروشگاه استفاده کن
        return f"steam_{game['steam_app_id']}_{store_name}" 
    
    # اگر Steam App ID نبود، از عنوان تمیز شده به همراه نام فروشگاه استفاده می‌کنیم
    cleaned_title = clean_title_for_search(game.get('title', '')) # استفاده از تابع مشترک
    if cleaned_title:
        return f"{cleaned_title}_{store_name}" 
    
    # آخرین راه حل: استفاده از URL اصلی (که ممکن است همچنان تکرار داشته باشد)
    return game.get('url', f"unknown_{os.urandom(8).hex()}") # Fallback ایمن

def _merge_game_data(existing_game: Dict[str, Any], new_game: Dict[str, Any]) -> Dict[str, Any]:
    """
    داده‌های یک بازی جدید را در یک بازی موجود ادغام می‌کند و داده‌های کامل‌تر/معتبرتر را اولویت می‌دهد.
    URL اصلی (که بازی رایگان از آن شناسایی شده) حفظ می‌شود.
    """
    merged_game = existing_game.copy()

    # اولویت‌بندی Steam App ID به عنوان شناسه اصلی
    # این فقط ID را اضافه می‌کند، URL را تغییر نمی‌دهد زیرا URL باید به پیشنهاد خاص اشاره کند.
    if 'steam_app_id' in new_game and new_game['steam_app_id']:
        merged_game['steam_app_id'] = new_game['steam_app_id']

    # اولویت‌بندی image_url: تصویر با کیفیت بالاتر (معمولا از Steam) یا غیر placeholder
    if 'image_url' in new_game and new_game['image_url']:
        # اگر تصویر موجود نیست، یا placeholder است، یا از Reddit است، تصویر جدید را جایگزین کن
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

    # URL اصلی (url) از منبعی که بازی را به عنوان رایگان گزارش کرده، باید حفظ شود.
    # با توجه به تغییر در _get_deduplication_key، این تابع کمتر برای ادغام پیشنهادهای یک بازی از فروشگاه‌های مختلف فراخوانی می‌شود.
    # بنابراین، URL موجود (existing_game['url']) معمولاً همان URL صحیح پیشنهاد خواهد بود.
    # نیازی به منطق پیچیده برای URL در اینجا نیست، زیرا deduplication آن را مدیریت می‌کند.

    # ادغام نمرات و سایر ویژگی‌ها، با اولویت‌بندی مقادیر غیر خالی
    for key in ['metacritic_score', 'metacritic_userscore', 
                'steam_overall_score', 'steam_overall_reviews_count', 
                'steam_recent_score', 'steam_recent_reviews_count', 
                'genres', 'trailer', 'is_multiplayer', 'is_online', 'age_rating']: # age_rating هم اضافه شد
        if key in new_game and new_game[key]:
            if key in ['is_multiplayer', 'is_online']: # برای پرچم‌های بولی، OR کن
                merged_game[key] = merged_game.get(key, False) or new_game[key]
            elif key == 'genres': # برای لیست‌ها، آیتم‌های منحصر به فرد را ادغام کن
                merged_game[key] = list(set(merged_game.get(key, []) + new_game[key]))
            else: # برای سایر فیلدها، مقدار جدید را جایگزین کن
                merged_game[key] = new_game[key]
    
    # اطمینان از اینکه عنوان تمیز شده، بهترین عنوان ممکن است
    # اگر عنوان جدید پس از تمیز شدن طولانی‌تر (و احتمالا کامل‌تر) باشد، آن را جایگزین کن
    if len(clean_title_for_search(new_game.get('title', ''))) > \
       len(clean_title_for_search(merged_game.get('title', ''))):
        merged_game['title'] = new_game['title']

    return merged_game

async def enrich_and_translate_game(game: Dict[str, Any], steam_enricher: SteamEnricher, metacritic_enricher: MetacriticEnricher, translator: SmartTranslator) -> Dict[str, Any]:
    """
    بازی را با اطلاعات اضافی غنی‌سازی و توضیحات آن را ترجمه می‌کند،
    با اعمال enricherها بر اساس پلتفرم.
    """
    store = game.get('store', '').lower().replace(' ', '')

    # تعیین پلتفرم بر اساس فروشگاه
    is_desktop_store = store in ['steam', 'epic games', 'gog', 'itch.io', 'indiegala', 'stove', 'other'] # 'other' می تواند دسکتاپ باشد
    is_console_store = store in ['xbox', 'playstation', 'nintendo']
    is_mobile_store = store in ['google play', 'ios app store']

    # اعمال SteamEnricher فقط برای بازی‌های دسکتاپ
    if is_desktop_store:
        game = await steam_enricher.enrich_data(game)
    else:
        logging.info(f"ℹ️ SteamEnricher برای بازی موبایل/کنسول '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # اعمال MetacriticEnricher برای بازی‌های دسکتاپ و کنسول
    if is_desktop_store or is_console_store:
        game = await metacritic_enricher.enrich_data(game)
    else:
        logging.info(f"ℹ️ MetacriticEnricher برای بازی موبایل '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # ترجمه توضیحات در صورت وجود
    description = game.get('description')
    if description and translator:
        game['persian_summary'] = await translator.translate(description)
    return game

async def main():
    logging.info("🚀 ربات گیم رایگان شروع به کار کرد...")

    if not TELEGRAM_BOT_TOKEN:
        logging.error("متغیر محیطی TELEGRAM_BOT_TOKEN تنظیم نشده است. برنامه متوقف می‌شود.")
        return

    db = Database(db_path="data/games.db")
    bot = TelegramBot(token=TELEGRAM_BOT_TOKEN, db=db)
    translator = SmartTranslator()

    # --- مرحله ۱: پردازش دستورات معلق کاربران ---
    await bot.process_pending_updates()

    # --- مرحله ۲: نمونه‌سازی و جمع‌آآوری داده از تمام منابع ---
    logging.info("🎮 شروع فرآیند یافتن بازی‌های رایگان...")
    sources = [
        ITADSource(),
        RedditSource(),
        EpicGamesSource()
    ]
    
    fetch_tasks = [source.fetch_free_games() for source in sources]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    all_games_raw = []
    for result in results:
        if isinstance(result, list):
            all_games_raw.extend(result)
        elif isinstance(result, Exception):
            logging.error(f"خطا در یکی از منابع داده: {result}")

    if not all_games_raw:
        logging.info("هیچ بازی از منابع یافت نشد.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logging.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logging.info(f"✅ {len(all_games_raw)} بازی خام از منابع مختلف یافت شد.")

    # --- مرحله ۳: غنی‌سازی و ترجمه تمام بازی‌های یافت شده (قبل از deduplication) ---
    steam_enricher = SteamEnricher()
    metacritic_enricher = MetacriticEnricher()
    
    enrich_tasks = [
        enrich_and_translate_game(game, steam_enricher, metacritic_enricher, translator)
        for game in all_games_raw
    ]
    
    # این لیست شامل تمام بازی‌های غنی‌شده است که ممکن است شامل تکراری‌ها باشد
    enriched_games_with_potential_duplicates = await asyncio.gather(*enrich_tasks)
    
    # --- مرحله ۴: deduplication بر اساس کلید منحصر به فرد و انتخاب بهترین نسخه ---
    final_unique_games_dict: Dict[str, Dict[str, Any]] = {} # Dictionary to store the best version of each game by key
    
    for game in enriched_games_with_potential_duplicates:
        dedup_key = _get_deduplication_key(game)
        if not dedup_key: # Skip if key generation failed
            logging.warning(f"⚠️ کلید deduplication برای بازی '{game.get('title', 'نامشخص')}' تولید نشد. این بازی ممکن است تکراری باشد.")
            continue

        if dedup_key not in final_unique_games_dict:
            final_unique_games_dict[dedup_key] = game
        else:
            # اگر کلید تکراری بود، داده‌ها را ادغام کن
            existing_game = final_unique_games_dict[dedup_key]
            merged_game = _merge_game_data(existing_game, game)
            final_unique_games_dict[dedup_key] = merged_game
            logging.info(f"✨ بازی تکراری '{game.get('title', 'نامشخص')}' (کلید: {dedup_key}) ادغام شد.")

    final_unique_games = list(final_unique_games_dict.values())

    if not final_unique_games:
        logging.info("پس از deduplication، هیچ بازی منحصر به فردی برای پردازش یافت نشد.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logging.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logging.info(f"✅ {len(final_unique_games)} بازی منحصر به فرد (پس از deduplication) برای پردازش یافت شد.")

    # --- مرحله ۵: فیلتر کردن بازی‌ها برای ارسال به تلگرام (فقط بازی‌های جدید در ۳۰ روز گذشته) ---
    games_to_post_to_telegram = []
    for game in final_unique_games: # از لیست deduplicate شده استفاده می‌کنیم
        url = game.get('url')
        # از id_in_db یا URL برای چک کردن در دیتابیس استفاده می‌کنیم
        if url and not db.is_game_posted_in_last_30_days(url): 
            games_to_post_to_telegram.append(game)
        else:
            logging.info(f"ℹ️ بازی '{game.get('title', 'نامشخص')}' قبلاً در ۳۰ روز گذشته پست شده بود یا URL ندارد. به تلگرام ارسال نمی‌شود.")

    # --- مرحله ۶: ارسال پیام‌ها به تلگرام ---
    if not games_to_post_to_telegram:
        logging.info("هیچ بازی جدیدی برای ارسال به تلگرام (بر اساس فیلتر ۳۰ روز گذشته) یافت نشد.")
    else:
        logging.info(f"📤 {len(games_to_post_to_telegram)} بازی برای ارسال به تلگرام آماده است.")
        for game in games_to_post_to_telegram:
            store_name = game.get('store', '').replace(' ', '').lower()
            targets = db.get_targets_for_store(store_name)
            
            if not targets:
                logging.warning(f"هیچ مشترکی برای فروشگاه '{store_name}' یافت نشد. از ارسال '{game['title']}' صرف نظر شد.")
                continue

            logging.info(f"📤 در حال ارسال پیام برای '{game['title']}' به {len(targets)} مقصد...")
            send_tasks = [
                bot.send_formatted_message(game_data=game, chat_id=chat_id, thread_id=thread_id)
                for chat_id, thread_id in targets
            ]
            await asyncio.gather(*send_tasks, return_exceptions=True)
            db.add_posted_game(game['url']) # ثبت بازی پس از ارسال موفقیت‌آمیز

    # --- مرحله ۷: ذخیره داده‌های غنی‌شده برای GitHub Pages ---
    # این مرحله همیشه اجرا می‌شود تا فایل JSON برای وب‌سایت با بازی‌های منحصر به فرد به‌روز باشد.
    output_dir = "web_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, "free_games.json")
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(final_unique_games, f, ensure_ascii=False, indent=4)
    logging.info(f"✅ داده‌های بازی‌های رایگان برای GitHub Pages در {output_file_path} ذخیره شد.")

    db.close()
    logging.info("🏁 کار ربات با موفقیت به پایان رسید.")

if __name__ == "__main__":
    asyncio.run(main())
