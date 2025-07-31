# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import json
import aiohttp
from typing import List, Dict, Any, Optional # <--- **خط اصلاح شده در اینجا قرار دارد**

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
from src.sources.epic_games import EpicGamesSource
from src.sources.itad import ITADSource
from src.sources.reddit import RedditSource

# --- Enrichment Services ---
from src.enrichment.steam_enricher import SteamEnricher
from src.enrichment.metacritic_enricher import MetacriticEnricher

# --- Translation Service ---
from src.translation.translator import SmartTranslator

# --- Utility Functions ---
from src.utils.game_utils import infer_store_from_game_data, normalize_url_for_key, clean_title

# ===== CONFIGURATION & CONSTANTS =====
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Fetch Telegram bot token from environment variable
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ===== CORE BUSINESS LOGIC / PIPELINE =====
class GamePipeline:
    """Orchestrates the entire process of fetching, enriching, and distributing game deals."""

    def __init__(self, db: Database, bot: Optional[TelegramBot], session: aiohttp.ClientSession):
        self.db = db
        self.bot = bot
        self.session = session
        
        # Initialize all components, injecting the shared session
        self.sources = [
            EpicGamesSource(), # EpicGamesSource now uses Playwright, doesn't need a session
            ITADSource(session),
            RedditSource(session)
        ]
        self.steam_enricher = SteamEnricher(session)
        self.metacritic_enricher = MetacriticEnricher(session)
        self.translator = SmartTranslator(session)

    def _get_deduplication_key(self, game: GameData) -> str:
        """Creates a unique key for a game to handle duplicates."""
        # Use a normalized URL as the primary key for stability
        return normalize_url_for_key(game.get('url', ''))

    def _classify_game_type(self, game: GameData) -> GameData:
        """Classifies a game as a full game or DLC/add-on."""
        title_lower = game.get('title', '').lower()
        is_dlc = False
        
        # Check for explicit DLC keywords
        if any(keyword in title_lower for keyword in DLC_KEYWORDS):
            is_dlc = True
        # Check for ambiguous keywords, but only if not clearly a full game
        elif any(keyword in title_lower for keyword in AMBIGUOUS_KEYWORDS):
            if not any(pk in title_lower for pk in POSITIVE_GAME_KEYWORDS):
                is_dlc = True
        
        game['is_dlc_or_addon'] = is_dlc
        return game

    def _merge_game_data(self, existing_game: GameData, new_game: GameData) -> GameData:
        """Merges data from a new source into an existing game entry, prioritizing richer data."""
        merged = existing_game.copy()
        
        # Prioritize more descriptive titles, but keep the original URL
        if len(new_game.get('title', '')) > len(merged.get('title', '')):
            merged['title'] = new_game['title']
        
        # Prioritize richer descriptions and better images
        if len(new_game.get('description', '')) > len(merged.get('description', '')):
            merged['description'] = new_game['description']
        if not merged.get('image_url') or ('placehold.co' in merged.get('image_url', '')):
            merged['image_url'] = new_game.get('image_url')
            
        # Update any missing fields in the existing entry with data from the new one
        for key, value in new_game.items():
            if merged.get(key) is None and value is not None:
                merged[key] = value
        
        return merged

    async def _fetch_raw_games(self) -> List[GameData]:
        """Fetches raw game data from all configured sources in parallel."""
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
        """Enriches, translates, classifies, and deduplicates the list of raw games."""
        logger.info("--- Step 2: Enriching, classifying, and deduplicating data ---")
        
        # Enrich all games in parallel
        enrich_tasks = []
        for game in raw_games:
            game['store'] = infer_store_from_game_data(game)
            task_steam = asyncio.create_task(self.steam_enricher.enrich(game))
            task_metacritic = asyncio.create_task(self.metacritic_enricher.enrich(game))
            enrich_tasks.extend([task_steam, task_metacritic])
        await asyncio.gather(*enrich_tasks)

        # Classify and deduplicate
        unique_games: Dict[str, GameData] = {}
        for game in raw_games:
            game = self._classify_game_type(game)
            key = self._get_deduplication_key(game)
            if key in unique_games:
                unique_games[key] = self._merge_game_data(unique_games[key], game)
            else:
                unique_games[key] = game
        
        final_list = list(unique_games.values())
        logger.info(f"Deduplication complete. Final unique game count: {len(final_list)}")
        
        # Translate the final unique list
        translate_tasks = [self.translator.translate(game.get('description', '')) for game in final_list]
        translations = await asyncio.gather(*translate_tasks)
        for game, translation in zip(final_list, translations):
            game['persian_summary'] = translation

        return final_list

    def _filter_games_for_notification(self, games: List[GameData]) -> List[GameData]:
        """Filters games to determine which ones should trigger a Telegram notification."""
        logger.info("--- Step 3: Filtering games for Telegram notification ---")
        games_to_notify = []
        for game in games:
            if not game.get('is_free') or game.get('is_dlc_or_addon'):
                continue

            dedup_key = self._get_deduplication_key(game)
            if not self.db.is_game_posted_in_last_days(dedup_key, days=30):
                games_to_notify.append(game)
                logger.info(f"✅ Game '{game['title']}' selected for notification.")
            else:
                logger.info(f"ℹ️ Skipping notification for '{game['title']}' (already sent recently).")
        
        return games_to_notify

    async def _send_notifications(self, games_to_notify: List[GameData]) -> None:
        """Sends Telegram notifications for the filtered list of games."""
        if not self.bot:
            logger.warning("Telegram bot object not initialized (TELEGRAM_BOT_TOKEN might be missing). Skipping notifications.")
            return

        logger.info(f"--- Step 4: Sending {len(games_to_notify)} notifications ---")
        for game in games_to_notify:
            targets = self.db.get_targets_for_store(game['store'])
            if not targets:
                logger.warning(f"No subscribers for store '{game['store']}'. Skipping '{game['title']}'.")
                continue
            
            logger.info(f"Sending '{game['title']}' to {len(targets)} targets.")
            notification_tasks = [
                self.bot.send_game_notification(game, chat_id, thread_id)
                for chat_id, thread_id in targets
            ]
            await asyncio.gather(*notification_tasks, return_exceptions=True)
            
            self.db.add_posted_game(self._get_deduplication_key(game))

    def _save_for_web(self, all_games: List[GameData]) -> None:
        """Saves the final list of all games (free and discounted) to a JSON file for the web."""
        logger.info("--- Step 5: Saving data for web front-end ---")
        os.makedirs(WEB_DATA_DIR, exist_ok=True)
        output_path = os.path.join(WEB_DATA_DIR, WEB_DATA_FILE)
        
        try:
            all_games.sort(key=lambda g: g.get('title', '').lower())
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(all_games, f, ensure_ascii=False, indent=4)
            logger.info(f"✅ Successfully saved {len(all_games)} games to {output_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save web data to {output_path}: {e}", exc_info=True)

    async def run(self) -> None:
        """Executes the complete data pipeline."""
        logger.info("🚀🚀🚀 Starting Game Deals Pipeline 🚀🚀🚀")
        
        raw_games = await self._fetch_raw_games()
        if not raw_games:
            logger.info("No deals found. Saving empty list to web file and exiting.")
            self._save_for_web([])
            return

        final_games = await self._enrich_and_finalize(raw_games)
        games_to_notify = self._filter_games_for_notification(final_games)
        await self._send_notifications(games_to_notify)
        self._save_for_web(final_games)
        
        logger.info("🏁🏁🏁 Pipeline finished successfully 🏁🏁🏁")

# ===== INITIALIZATION & STARTUP =====
async def main():
    """Initializes and runs the GamePipeline."""
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