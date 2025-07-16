import sqlite3
import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class Database:
    def __init__(self, db_path='data/games.db'):
        """
        پایگاه داده SQLite را مقداردهی اولیه می‌کند.
        فایل پایگاه داده در مسیر مشخص شده ایجاد می‌شود اگر وجود نداشته باشد.
        """
        self.db_path = db_path
        self._create_tables()
        logging.info(f"پایگاه داده در مسیر: {self.db_path} مقداردهی اولیه شد.")

    def _create_tables(self):
        """
        جداول مورد نیاز (posted_games و user_subscriptions) را در پایگاه داده ایجاد می‌کند
        اگر از قبل وجود نداشته باشند.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # جدول برای ردیابی بازی‌های پست شده
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posted_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_url TEXT UNIQUE NOT NULL,
                    posted_date TEXT NOT NULL
                )
            """)
            # جدول برای ردیابی اشتراک‌های کاربران
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    store_name TEXT NOT NULL,
                    PRIMARY KEY (chat_id, thread_id, store_name)
                )
            """)
            conn.commit()
            logging.info("جداول پایگاه داده (posted_games, user_subscriptions) بررسی/ایجاد شدند.")

    def add_posted_game(self, game_url: str):
        """
        یک URL بازی را به جدول posted_games اضافه می‌کند.
        تاریخ فعلی به عنوان posted_date ثبت می‌شود.
        """
        posted_date = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO posted_games (game_url, posted_date) VALUES (?, ?)",
                    (game_url, posted_date)
                )
                conn.commit()
                logging.info(f"URL بازی '{game_url}' به پایگاه داده اضافه شد.")
            except sqlite3.IntegrityError:
                logging.warning(f"URL بازی '{game_url}' قبلاً در پایگاه داده وجود داشت. نادیده گرفته شد.")
            except Exception as e:
                logging.error(f"خطا در افزودن URL بازی به پایگاه داده: {e}")

    def is_game_posted_in_last_30_days(self, game_url: str) -> bool:
        """
        بررسی می‌کند که آیا یک بازی با URL مشخص شده در ۳۰ روز گذشته پست شده است یا خیر.
        """
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM posted_games WHERE game_url = ? AND posted_date >= ?",
                (game_url, thirty_days_ago)
            )
            count = cursor.fetchone()[0]
            if count > 0:
                logging.debug(f"بازی '{game_url}' در ۳۰ روز گذشته پست شده است.")
                return True
            logging.debug(f"بازی '{game_url}' در ۳۰ روز گذشته پست نشده است.")
            return False

    def add_subscription(self, chat_id: int, thread_id: int, store_name: str):
        """
        یک اشتراک جدید برای کاربر و فروشگاه مشخص شده اضافه می‌کند.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO user_subscriptions (chat_id, thread_id, store_name) VALUES (?, ?, ?)",
                    (chat_id, thread_id, store_name.lower())
                )
                conn.commit()
                logging.info(f"اشتراک جدید برای chat_id={chat_id}, thread_id={thread_id}, store='{store_name}' اضافه شد.")
            except sqlite3.IntegrityError:
                logging.warning(f"اشتراک برای chat_id={chat_id}, thread_id={thread_id}, store='{store_name}' قبلاً وجود داشت.")
            except Exception as e:
                logging.error(f"خطا در افزودن اشتراک: {e}")

    def remove_subscription(self, chat_id: int, thread_id: int, store_name: str):
        """
        یک اشتراک موجود را حذف می‌کند.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_subscriptions WHERE chat_id = ? AND thread_id = ? AND store_name = ?",
                (chat_id, thread_id, store_name.lower())
            )
            conn.commit()
            if cursor.rowcount > 0:
                logging.info(f"اشتراک برای chat_id={chat_id}, thread_id={thread_id}, store='{store_name}' حذف شد.")
            else:
                logging.warning(f"اشتراکی برای chat_id={chat_id}, thread_id={thread_id}, store='{store_name}' یافت نشد.")

    def get_user_subscriptions(self, chat_id: int, thread_id: int) -> List[str]:
        """
        لیست فروشگاه‌هایی که کاربر در آن مشترک است را برمی‌گرداند.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT store_name FROM user_subscriptions WHERE chat_id = ? AND thread_id = ?",
                (chat_id, thread_id)
            )
            subscriptions = [row[0] for row in cursor.fetchall()]
            logging.debug(f"اشتراک‌های کاربر chat_id={chat_id}, thread_id={thread_id}: {subscriptions}")
            return subscriptions

    def get_targets_for_store(self, store_name: str) -> List[tuple]:
        """
        لیست (chat_id, thread_id) کاربرانی که در فروشگاه مشخص شده مشترک هستند را برمی‌گرداند.
        اگر store_name 'all' باشد، تمام کاربران را برمی‌گرداند.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if store_name.lower() == 'all':
                cursor.execute("SELECT chat_id, thread_id FROM user_subscriptions")
            else:
                cursor.execute(
                    "SELECT chat_id, thread_id FROM user_subscriptions WHERE store_name = ?",
                    (store_name.lower(),)
                )
            targets = cursor.fetchall()
            logging.debug(f"مقاصد برای فروشگاه '{store_name}': {targets}")
            return targets
    
    def close(self):
        """
        اتصال به پایگاه داده را می‌بندد.
        """
        # در اینجا نیازی به بستن صریح نیست زیرا از 'with' statement استفاده می‌شود.
        # اما این متد برای سازگاری با الگوهای ممکن دیگر اضافه شده است.
        logging.info("اتصال به پایگاه داده بسته شد (اگر باز بود).")

