import os
import asyncio
import logging
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
    translator = SmartTranslator() # دیگر نیازی به کلید نیست

    # --- *** بخش جدید: حالت تعاملی ۵ دقیقه‌ای *** ---
    try:
        logging.info("🤖 ربات به مدت ۵ دقیقه در حالت تعاملی برای دریافت دستورات قرار گرفت...")
        await bot.application.initialize()
        await bot.application.start()
        # updater.start_polling() را مستقیما صدا نمی‌زنیم، start() این کار را مدیریت می‌کند
        
        # به مدت ۳۰۰ ثانیه (۵ دقیقه) به ربات اجازه می‌دهیم تا دستورات را پردازش کند
        await asyncio.sleep(300)
        
        await bot.application.stop()
        await bot.application.shutdown()
        logging.info("⏳ زمان حالت تعاملی به پایان رسید. ادامه فرآیند...")
    except Exception as e:
        logging.error(f"خطا در حالت تعاملی ربات: {e}", exc_info=True)


    # --- بخش اصلی: یافتن و اطلاع‌رسانی بازی‌ها ---
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

    unique_new_games = []
    processed_urls = set()
    for game in all_games_raw:
        url = game.get('url')
        if url and url not in processed_urls:
            if not db.is_game_posted_in_last_30_days(url):
                unique_new_games.append(game)
            else:
                logging.info(f"بازی تکراری (در ۳۰ روز اخیر) یافت شد و نادیده گرفته شد: {game.get('title')}")
            processed_urls.add(url)

    if not unique_new_games:
        logging.info("هیچ بازی جدیدی برای اطلاع‌رسانی یافت نشد.")
        db.close()
        return

    logging.info(f"✅ {len(unique_new_games)} بازی جدید برای پردازش یافت شد.")

    enrichers = [SteamEnricher(), MetacriticEnricher()]
    enrich_tasks = [enrich_and_translate_game(game, enrichers, translator) for game in unique_new_games]
    enriched_games = await asyncio.gather(*enrich_tasks)

    for game in enriched_games:
        store_name = game.get('store', '').replace(' ', '').lower()
        targets = db.get_targets_for_store(store_name)
        
        if not targets:
            logging.warning(f"هیچ مشترکی برای فروشگاه '{store_name}' یافت نشد.")
            continue

        logging.info(f"📤 در حال ارسال پیام برای '{game['title']}' به {len(targets)} مقصد...")
        send_tasks = [
            bot.send_formatted_message(game_data=game, chat_id=chat_id, thread_id=thread_id)
            for chat_id, thread_id in targets
        ]
        await asyncio.gather(*send_tasks, return_exceptions=True)
        db.add_posted_game(game['url'])

    db.close()
    logging.info("🏁 کار ربات با موفقیت به پایان رسید.")


if __name__ == "__main__":
    asyncio.run(main())
