import sqlite3
import logging
from datetime import datetime, timedelta
import os
from typing import List, Tuple, Optional

# تنظیمات اولیه برای لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class Database:
    """
    کلاسی برای مدیریت کامل پایگاه داده SQLite ربات.
    این کلاس هم بازی‌های ارسال شده و هم اشتراک‌های کاربران را مدیریت می‌کند.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            self._create_tables()
        except sqlite3.Error as e:
            logging.error(f"خطا در اتصال به پایگاه داده: {e}")
            raise

    def _create_tables(self):
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS posted_games (
                    url TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL
                )
            """)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    store TEXT NOT NULL,
                    PRIMARY KEY (chat_id, thread_id, store)
                )
            """)
            self.conn.commit()
            logging.info("جداول پایگاه داده با موفقیت بررسی/ایجاد شدند.")
        except sqlite3.Error as e:
            logging.error(f"خطا در ایجاد جداول: {e}")

    def is_game_posted_in_last_30_days(self, url: str) -> bool:
        try:
            thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
            self.cursor.execute(
                "SELECT 1 FROM posted_games WHERE url = ? AND timestamp >= ?",
                (url, thirty_days_ago)
            )
            return self.cursor.fetchone() is not None
        except sqlite3.Error as e:
            logging.error(f"خطا در بررسی URL بازی ({url}): {e}")
            return True

    def add_posted_game(self, url: str):
        try:
            timestamp = datetime.utcnow().isoformat()
            self.cursor.execute(
                "INSERT OR REPLACE INTO posted_games (url, timestamp) VALUES (?, ?)",
                (url, timestamp)
            )
            self.conn.commit()
            logging.info(f"بازی با آدرس {url} در پایگاه داده ثبت/به‌روز شد.")
        except sqlite3.Error as e:
            logging.error(f"خطا در ثبت بازی ({url}) در پایگاه داده: {e}")

    def add_subscription(self, chat_id: int, thread_id: Optional[int], store: str) -> bool:
        try:
            self.cursor.execute(
                "INSERT INTO subscriptions (chat_id, thread_id, store) VALUES (?, ?, ?)",
                (chat_id, thread_id, store.lower())
            )
            self.conn.commit()
            logging.info(f"اشتراک جدید ثبت شد: chat_id={chat_id}, thread_id={thread_id}, store='{store}'")
            return True
        except sqlite3.IntegrityError:
            logging.warning(f"این اشتراک از قبل وجود داشت: chat_id={chat_id}, thread_id={thread_id}, store='{store}'")
            return False

    def remove_subscription(self, chat_id: int, thread_id: Optional[int], store: str) -> bool:
        try:
            self.cursor.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND (? IS NULL OR thread_id = ?) AND store = ?",
                (chat_id, thread_id, thread_id, store.lower())
            )
            self.conn.commit()
            if self.cursor.rowcount > 0:
                logging.info(f"اشتراک حذف شد: chat_id={chat_id}, thread_id={thread_id}, store='{store}'")
                return True
            return False
        except sqlite3.Error as e:
            logging.error(f"خطا در حذف اشتراک: {e}")
            return False

    def get_targets_for_store(self, store: str) -> List[Tuple[int, Optional[int]]]:
        try:
            self.cursor.execute(
                "SELECT chat_id, thread_id FROM subscriptions WHERE store = ? OR store = 'all'",
                (store.lower(),)
            )
            return list(set(self.cursor.fetchall()))
        except sqlite3.Error as e:
            logging.error(f"خطا در دریافت اهداف برای فروشگاه '{store}': {e}")
            return []

    def close(self):
        if self.conn:
            self.conn.close()
            logging.info("اتصال به پایگاه داده بسته شد.")
