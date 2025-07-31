// ===== IMPORTS & DEPENDENCIES =====
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class Database:
    """Handles all database operations for the bot."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._create_tables()
        logger.info(f"[{self.__class__.__name__}] Database initialized at: {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a new database connection."""
        return sqlite3.connect(self.db_path)

    def _create_tables(self):
        """Creates required tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Table for tracking posted games
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posted_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_url TEXT UNIQUE NOT NULL,
                    posted_date TEXT NOT NULL
                )
            """)
            # Table for tracking user subscriptions
            # thread_id can be NULL for non-topic chats
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

    def add_posted_game(self, game_url: str) -> None:
        """Adds a game URL to the posted_games table."""
        posted_date = datetime.now().isoformat()
        with self._get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO posted_games (game_url, posted_date) VALUES (?, ?)",
                    (game_url, posted_date)
                )
                conn.commit()
                logger.info(f"[{self.__class__.__name__}] Added posted game URL to DB: {game_url}")
            except sqlite3.IntegrityError:
                logger.warning(f"[{self.__class__.__name__}] Game URL already exists in DB: {game_url}")
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error adding game URL to DB: {e}", exc_info=True)

    def is_game_posted_in_last_days(self, game_url: str, days: int = 30) -> bool:
        """Checks if a game has been posted in the last `days`."""
        threshold_date = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM posted_games WHERE game_url = ? AND posted_date >= ?",
                (game_url, threshold_date)
            )
            return cursor.fetchone() is not None

    def add_subscription(self, chat_id: int, store_name: str, thread_id: Optional[int] = None) -> None:
        """Adds a new subscription for a user."""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO user_subscriptions (chat_id, thread_id, store_name) VALUES (?, ?, ?)",
                    (chat_id, thread_id, store_name.lower())
                )
                conn.commit()
                logger.info(f"[{self.__class__.__name__}] Added/updated subscription for chat={chat_id}, thread={thread_id}, store='{store_name}'")
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error adding subscription: {e}", exc_info=True)

    def remove_subscription(self, chat_id: int, store_name: str, thread_id: Optional[int] = None) -> None:
        """Removes an existing subscription."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # This query correctly handles cases where thread_id is None
            cursor.execute(
                "DELETE FROM user_subscriptions WHERE chat_id = ? AND store_name = ? AND "
                "(thread_id = ? OR (? IS NULL AND thread_id IS NULL))",
                (chat_id, store_name.lower(), thread_id, thread_id)
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"[{self.__class__.__name__}] Removed subscription for chat={chat_id}, thread={thread_id}, store='{store_name}'")
            else:
                logger.warning(f"[{self.__class__.__name__}] No subscription found to remove for chat={chat_id}, thread={thread_id}, store='{store_name}'")

    def get_user_subscriptions(self, chat_id: int, thread_id: Optional[int] = None) -> List[str]:
        """Returns the list of stores a user is subscribed to."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT store_name FROM user_subscriptions WHERE chat_id = ? AND "
                "(thread_id = ? OR (? IS NULL AND thread_id IS NULL))",
                (chat_id, thread_id, thread_id)
            )
            return [row[0] for row in cursor.fetchall()]

    def get_targets_for_store(self, store_name: str) -> List[Tuple[int, Optional[int]]]:
        """
        Returns a list of (chat_id, thread_id) for a given store.
        Includes users subscribed to 'all'.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Select users subscribed to the specific store OR subscribed to 'all'
            cursor.execute(
                "SELECT DISTINCT chat_id, thread_id FROM user_subscriptions WHERE store_name = ? OR store_name = 'all'",
                (store_name.lower(),)
            )
            return cursor.fetchall()
