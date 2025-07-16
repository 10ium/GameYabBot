import os
import asyncio
import logging
import json
from typing import List, Dict, Any

from core.database import Database
from core.telegram_bot import TelegramBot
from sources.itad import ITADSource
from sources.reddit import RedditSource
from sources.epic_games import EpicGamesSource
from enrichment.steam_enricher import SteamEnricher
from enrichment.metacritic_enricher import MetacriticEnricher
from translation.translator import SmartTranslator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

async def enrich_and_translate_game(game: Dict[str, Any], enrichers: list, translator: SmartTranslator) -> Dict[str, Any]:
    """
    بازی را با اطلاعات اضافی غنی‌سازی و توضیحات آن را ترجمه می‌کند.
    """
    # ابتدا اطلاعات را از Enricherها دریافت می‌کنیم
    for enricher in enrichers:
        game = await enricher.enrich_data(game)
    
    # سپس توضیحات (که ممکن است توسط Enricherها اضافه شده باشد) را ترجمه می‌کنیم
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

    # --- مرحله ۲: نمونه‌سازی و جمع‌آوری داده از تمام منابع ---
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

    # --- مرحله ۳: فیلتر کردن بازی‌های تکراری در بین تمام منابع یافت شده در این اجرا ---
    # این لیست شامل تمام بازی‌های منحصر به فرد یافت شده در این اجرای فعلی است.
    # این لیست مبنای غنی‌سازی و ترجمه قرار می‌گیرد.
    current_run_unique_games = []
    processed_urls_current_run = set()
    for game in all_games_raw:
        url = game.get('url')
        if url and url not in processed_urls_current_run:
            current_run_unique_games.append(game)
            processed_urls_current_run.add(url)

    if not current_run_unique_games:
        logging.info("هیچ بازی جدیدی در این اجرا یافت نشد.")
        # اگر هیچ بازی جدیدی در این اجرا یافت نشد، فایل JSON را با لیست خالی به‌روز می‌کنیم.
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logging.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logging.info(f"✅ {len(current_run_unique_games)} بازی منحصر به فرد در این اجرا یافت شد.")

    # --- مرحله ۴: غنی‌سازی و ترجمه تمام بازی‌های منحصر به فرد یافت شده ---
    enrichers = [SteamEnricher(), MetacriticEnricher()]
    enrich_tasks = [enrich_and_translate_game(game, enrichers, translator) for game in current_run_unique_games]
    
    # این لیست شامل تمام بازی‌های منحصر به فرد و غنی‌شده در این اجرا است.
    # این لیست برای GitHub Pages استفاده خواهد شد.
    enriched_games_for_pages = await asyncio.gather(*enrich_tasks)
    
    # --- مرحله ۵: فیلتر کردن بازی‌ها برای ارسال به تلگرام (فقط بازی‌های جدید در ۳۰ روز گذشته) ---
    games_to_post_to_telegram = []
    for game in enriched_games_for_pages:
        url = game.get('url')
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
    # این مرحله همیشه اجرا می‌شود تا فایل JSON برای وب‌سایت به‌روز باشد.
    output_dir = "web_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, "free_games.json")
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(enriched_games_for_pages, f, ensure_ascii=False, indent=4)
    logging.info(f"✅ داده‌های بازی‌های رایگان برای GitHub Pages در {output_file_path} ذخیره شد.")

    db.close()
    logging.info("🏁 کار ربات با موفقیت به پایان رسید.")

if __name__ == "__main__":
    asyncio.run(main())
