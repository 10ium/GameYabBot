import os
import asyncio
import logging
import json # اطمینان حاصل کنید که این خط وجود دارد
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
    for enricher in enrichers:
        game = await enricher.enrich_data(game)
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

    # --- مرحله ۱: پردازش دستورات معلق کاربران (روش صحیح و پایدار) ---
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

    # --- مرحله ۳: فیلتر کردن بازی‌های تکراری ---
    unique_new_games = []
    processed_urls = set()
    for game in all_games_raw:
        url = game.get('url')
        if url and url not in processed_urls:
            if not db.is_game_posted_in_last_30_days(url):
                unique_new_games.append(game)
            processed_urls.add(url)

    if not unique_new_games:
        logging.info("هیچ بازی جدیدی برای اطلاع‌رسانی یافت نشد.")
        # اگر بازی جدیدی یافت نشد، هنوز هم باید فایل JSON را به‌روز کنیم تا نشان دهیم هیچ بازی جدیدی نیست.
        # این کار از نمایش اطلاعات قدیمی جلوگیری می‌کند.
        # بنابراین، بخش ذخیره JSON را به خارج از این شرط منتقل می‌کنیم.
        # db.close() # این خط را از اینجا حذف می‌کنیم
        # return # این خط را از اینجا حذف می‌کنیم

    logging.info(f"✅ {len(unique_new_games)} بازی جدید برای پردازش یافت شد.")

    # --- مرحله ۴: غنی‌سازی و ترجمه ---
    enrichers = [SteamEnricher(), MetacriticEnricher()]
    enrich_tasks = [enrich_and_translate_game(game, enrichers, translator) for game in unique_new_games]
    enriched_games = await asyncio.gather(*enrich_tasks)

    # --- مرحله ۵: ارسال پیام‌ها (همانند قبل) ---
    for game in enriched_games:
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
        db.add_posted_game(game['url'])

    # --- مرحله ۶: ذخیره داده‌های غنی‌شده در یک فایل JSON برای GitHub Pages ---
    # این بخش باید همیشه اجرا شود، حتی اگر unique_new_games خالی باشد.
    # این تضمین می‌کند که فایل free_games.json همیشه به‌روز باشد.
    output_dir = "web_data"
    os.makedirs(output_dir, exist_ok=True) # اطمینان از وجود دایرکتوری
    output_file_path = os.path.join(output_dir, "free_games.json")
    
    # اگر unique_new_games خالی بود، یک لیست خالی ذخیره می‌کنیم
    # در غیر این صورت، enriched_games را ذخیره می‌کنیم
    data_to_save = enriched_games if unique_new_games else [] 

    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    logging.info(f"✅ داده‌های بازی‌های رایگان در {output_file_path} ذخیره شد.")

    db.close()
    logging.info("🏁 کار ربات با موفقیت به پایان رسید.")

if __name__ == "__main__":
    asyncio.run(main())
