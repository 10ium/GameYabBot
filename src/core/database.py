// ===== IMPORTS & DEPENDENCIES =====
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from src.config import DATABASE_PATH

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class Database:
    """Handles all database operations for the bot, including subscriptions and posted games."""
    
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._create_tables()
        logger.info(f"[{self.__class__.__name__}] Database initialized at: {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a new database connection."""
        return sqlite3.connect(self.db_path)

    def _create_tables(self) -> None:
        """Creates required tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Table for tracking posted games
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posted_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deduplication_key TEXT UNIQUE NOT NULL,
                    posted_date TEXT NOT NULL
                )
            """)
            # Table for tracking user subscriptions. Thread_id can be NULL for non-topic chats.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    store_name TEXT NOT NULL,
                    UNIQUE(chat_id, store_name, thread_id)
                )
            """)
            conn.commit()
            logger.info(f"[{self.__class__.__name__}] Database tables verified/created.")

    def add_posted_game(self, deduplication_key: str) -> None:
        """Adds a game's deduplication key to the posted_games table."""
        posted_date = datetime.now().isoformat()
        with self._get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO posted_games (deduplication_key, posted_date) VALUES (?, ?)",
                    (deduplication_key, posted_date)
                )
                conn.commit()
                logger.info(f"[{self.__class__.__name__}] Added posted game to DB with key: {deduplication_key}")
            except sqlite3.IntegrityError:
                logger.warning(f"[{self.__class__.__name__}] Game with key '{deduplication_key}' already exists in DB.")
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error adding game to DB: {e}", exc_info=True)

    def is_game_posted_in_last_days(self, deduplication_key: str, days: int = 30) -> bool:
        """Checks if a game with the given key has been posted in the last `days`."""
        threshold_date = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM posted_games WHERE deduplication_key = ? AND posted_date >= ?",
                (deduplication_key, threshold_date)
            )
            return cursor.fetchone() is not None

    def add_subscription(self, chat_id: int, store_name: str, thread_id: Optional[int] = None) -> None:
        """Adds a new subscription for a user."""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO user_subscriptions (chat_id, thread_id, store_name) VALUES (?, ?, ?)",
                    (chat_id, thread_id, store_name.lower())
                )
                conn.commit()
                logger.info(f"[{self.__class__.__name__}] Subscription processed for chat={chat_id}, thread={thread_id}, store='{store_name}'")
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error adding subscription: {e}", exc_info=True)

    def remove_subscription(self, chat_id: int, store_name: str, thread_id: Optional[int] = None) -> None:
        """Removes an existing subscription."""
        query = "DELETE FROM user_subscriptions WHERE chat_id = ? AND store_name = ? AND "
        params = [chat_id, store_name.lower()]
        
        if thread_id is None:
            query += "thread_id IS NULL"
        else:
            query += "thread_id = ?"
            params.append(thread_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"[{self.__class__.__name__}] Removed subscription for chat={chat_id}, thread={thread_id}, store='{store_name}'")
            else:
                logger.warning(f"[{self.__class__.__name__}] No subscription found to remove for chat={chat_id}, thread={thread_id}, store='{store_name}'")

    def get_user_subscriptions(self, chat_id: int, thread_id: Optional[int] = None) -> List[str]:
        """Returns the list of stores a user is subscribed to."""
        query = "SELECT store_name FROM user_subscriptions WHERE chat_id = ? AND "
        params = [chat_id]
        
        if thread_id is None:
            query += "thread_id IS NULL"
        else:
            query += "thread_id = ?"
            params.append(thread_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [row[0] for row in cursor.fetchall()]

    def get_targets_for_store(self, store_name: str) -> List[Tuple[int, Optional[int]]]:
        """Returns a list of (chat_id, thread_id) for subscribers of a given store."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Select users subscribed to the specific store OR to 'all' stores
            cursor.execute(
                "SELECT DISTINCT chat_id, thread_id FROM user_subscriptions WHERE store_name = ? OR store_name = 'all'",
                (store_name.lower(),)
            )
            return cursor.fetchall()```