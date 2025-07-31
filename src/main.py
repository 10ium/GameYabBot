# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import json
import aiohttp
import re
from typing import List, Dict, Any, Optional

# --- Configuration ---
from src.config import (
    LOG_LEVEL, WEB_DATA_DIR, WEB_DATA_FILE,
    DLC_KEYWORDS, AMBIGUOUS_KEYWORDS, POSITIVE_GAME_KEYWORDS
)

# --- Core Components ---
from src.core.database import Database
from src.core.telegram_bot import TelegramBot

# --- Data Models ---
from src.models.game import GameData

# --- Data Sources ---
from src.sources.itad import ITADSource
from src.sources.reddit import RedditSource

# --- Enrichment Services ---
from src.enrichment.steam_enricher import SteamEnricher
from src.enrichment.metacritic_enricher import MetacriticEnricher
from src.enrichment.image_enricher import ImageEnricher

# --- Translation Service ---
from src.translation.translator import SmartTranslator

# --- Utility Functions ---
from src.utils.game_utils import infer_store_from_game_data, normalize_url_for_key, clean_title, sanitize_html

# ===== CONFIGURATION & CONSTANTS =====
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ===== CORE BUSINESS LOGIC / PIPELINE =====
class GamePipeline:
    """Orchestrates the entire process of fetching, enriching, and distributing game deals."""

    def __init__(self, db: Database, bot: Optional[TelegramBot], session: aiohttp.ClientSession):
        self.db = db
        self.bot = bot
        self.session = session
        
        self.sources = [
            ITADSource(session),
            RedditSource(session)
        ]
        self.steam_enricher = SteamEnricher(session)
        self.metacritic_enricher = MetacriticEnricher(session)
        self.image_enricher = ImageEnricher(session)
        self.translator = SmartTranslator(session)

    def _get_canonical_id(self, game: GameData) -> str:
        if game.get('steam_app_id'): return f"steam_{game.get('steam_app_id')}"
        url = game.get('url', '').lower()
        if 'gog.com' in url:
            match = re.search(r'/game/([a-z0-9_]+)', url)
            if match: return f"gog_{match.group(1)}"
        if 'epicgames.com' in url:
            match = re.search(r'/(p|product)/([a-z0-9-]+)', url)
            if match: return f"epic_{match.group(2)}"
        title_key = clean_title(game.get('title', ''))
        title_key = re.sub(r'\s+', '_', title_key)
        store = game.get('store', 'other')
        platform = "pc"
        if store in ['googleplay', 'android']: platform = 'android'
        if store in ['iosappstore', 'ios']: platform = 'ios'
        if store in ['playstation']: platform = 'playstation'
        if store in ['xbox']: platform = 'xbox'
        return f"title_{title_key}_{platform}"

    def _classify_game_type(self, game: GameData) -> GameData:
        title_lower = game.get('title', '').lower()
        is_dlc = False
        if any(keyword in title_lower for keyword in DLC_KEYWORDS): is_dlc = True
        elif any(keyword in title_lower for keyword in AMBIGUOUS_KEYWORDS):
            if not any(pk in title_lower for pk in POSITIVE_GAME_KEYWORDS): is_dlc = True
        game['is_dlc_or_addon'] = is_dlc
        return game

    def _merge_game_data(self, existing_game: GameData, new_game: GameData) -> GameData:
        merged = existing_game.copy()
        if len(new_game.get('title', '')) > len(merged.get('title', '')): merged['title'] = new_game['title']
        if len(new_game.get('description', '')) > len(merged.get('description', '')): merged['description'] = new_game['description']
        if not merged.get('image_url') or ('placehold.co' in merged.get('image_url', '')): merged['image_url'] = new_game.get('image_url')
        for key, value in new_game.items():
            if merged.get(key) is None and value is not None: merged[key] = value
        return merged

    async def _fetch_raw_games(self) -> List[GameData]:
        logger.info("--- Step 1: Fetching raw data from all sources ---")
        tasks = [source.fetch_free_games() for source in self.sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_games: List[GameData] = []
        for i, result in enumerate(results):
            source_name = self.sources[i].__class__.__name__
            if isinstance(result, list):
                all_games.extend(result)
                logger.info(f"✅ Found {len(result)} potential deals from {source_name}.")
            else:
                logger.error(f"❌ Failed to fetch from {source_name}: {result}", exc_info=True)
        logger.info(f"Total raw deals fetched: {len(all_games)}")
        return all_games

    async def _enrich_and_finalize(self, raw_games: List[GameData]) -> List[GameData]:
        logger.info("--- Step 2: Enriching, classifying, and deduplicating data ---")
        
        enrich_tasks = []
        for game in raw_games:
            game['store'] = infer_store_from_game_data(game)
            enrich_tasks.append(asyncio.create_task(self.steam_enricher.enrich(game)))
            enrich_tasks.append(asyncio.create_task(self.metacritic_enricher.enrich(game)))
        await asyncio.gather(*enrich_tasks, return_exceptions=True)
        
        image_tasks = [
            asyncio.create_task(self.image_enricher.enrich(game, clean_title(game['title'])))
            for game in raw_games
        ]
        await asyncio.gather(*image_tasks, return_exceptions=True)

        unique_games: Dict[str, GameData] = {}
        for game in raw_games:
            canonical_id = self._get_canonical_id(game)
            game = self._classify_game_type(game)
            if canonical_id in unique_games:
                unique_games[canonical_id] = self._merge_game_data(unique_games[canonical_id], game)
            else:
                unique_games[canonical_id] = game
        
        final_list = list(unique_games.values())
        logger.info(f"Deduplication complete. Final unique game count: {len(final_list)}")
        
        translate_tasks = []
        for game in final_list:
            clean_description = sanitize_html(game.get('description', ''))
            translate_tasks.append(self.translator.translate(clean_description))
        
        # **CRITICAL FIX**: Use `translate_tasks` here, not `translations`
        translations = await asyncio.gather(*translate_tasks, return_exceptions=True)
        
        for i, game in enumerate(final_list):
            if isinstance(translations[i], str):
                game['persian_summary'] = translations[i]
            game['description'] = sanitize_html(game.get('description', ''))

        return final_list

    def _filter_games_for_notification(self, games: List[GameData]) -> List[GameData]:
        logger.info("--- Step 3: Filtering games for Telegram notification ---")
        games_to_notify = []
        for game in games:
            if not game.get('is_free') or game.get('is_dlc_or_addon'): continue
            dedup_key = self._get_canonical_id(game)
            if not self.db.is_game_posted_in_last_days(dedup_key, days=30):
                games_to_notify.append(game)
        return games_to_notify

    async def _send_notifications(self, games_to_notify: List[GameData]) -> None:
        if not self.bot:
            logger.warning("Telegram bot not initialized. Skipping notifications.")
            return
        logger.info(f"--- Step 4: Sending {len(games_to_notify)} notifications ---")
        for game in games_to_notify:
            targets = self.db.get_targets_for_store(game['store'])
            if not targets:
                logger.warning(f"No subscribers for store '{game['store']}'. Skipping '{game['title']}'.")
                continue
            notification_tasks = [self.bot.send_game_notification(game, chat_id, thread_id) for chat_id, thread_id in targets]
            await asyncio.gather(*notification_tasks, return_exceptions=True)
            self.db.add_posted_game(self._get_canonical_id(game))

    def _save_for_web(self, all_games: List[GameData]) -> None:
        logger.info("--- Step 5: Saving data for web front-end ---")
        os.makedirs(WEB_DATA_DIR, exist_ok=True)
        output_path = os.path.join(WEB_DATA_DIR, WEB_DATA_FILE)
        sanitized_games_for_web = []
        for game in all_games:
            sanitized_game = game.copy()
            sanitized_game['description'] = sanitize_html(game.get('description', ''))
            sanitized_game['persian_summary'] = sanitize_html(game.get('persian_summary', ''))
            sanitized_games_for_web.append(sanitized_game)
        try:
            sanitized_games_for_web.sort(key=lambda g: g.get('title', '').lower())
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(sanitized_games_for_web, f, ensure_ascii=False, indent=4)
            logger.info(f"✅ Successfully saved {len(sanitized_games_for_web)} sanitized games to {output_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save web data to {output_path}: {e}", exc_info=True)

    async def run(self) -> None:
        logger.info("🚀🚀🚀 Starting Game Deals Pipeline 🚀🚀🚀")
        raw_games = await self._fetch_raw_games()
        if not raw_games:
            logger.info("No deals found. Saving empty list and exiting.")
            self._save_for_web([])
            return
        final_games = await self._enrich_and_finalize(raw_games)
        games_to_notify = self._filter_games_for_notification(final_games)
        await self._send_notifications(games_to_notify)
        self._save_for_web(final_games)
        logger.info("🏁🏁🏁 Pipeline finished successfully 🏁🏁🏁")

# ===== INITIALIZATION & STARTUP =====
async def main():
    db = Database()
    bot = TelegramBot(token=TELEGRAM_BOT_TOKEN, db=db) if TELEGRAM_BOT_TOKEN else None
    async with aiohttp.ClientSession() as session:
        pipeline = GamePipeline(db, bot, session)
        try:
            await pipeline.run()
        except Exception as e:
            logger.critical(f"🔥🔥🔥 A critical error occurred in the main pipeline: {e}", exc_info=True)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())