import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from typing import List, Dict, Any

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self, token: str, db):
        """
        ربات تلگرام را مقداردهی اولیه می‌کند.
        :param token: توکن API ربات تلگرام.
        :param db: نمونه‌ای از کلاس Database برای تعامل با پایگاه داده.
        """
        self.application = Application.builder().token(token).build()
        self.db = db
        self._register_handlers()
        logger.info("ربات تلگرام با موفقیت مقداردهی اولیه شد.")

    def _register_handlers(self):
        """
        هندلرهای دستورات و Callback Queryها را ثبت می‌کند.
        """
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("subscribe", self.subscribe_command))
        self.application.add_handler(CommandHandler("unsubscribe", self.unsubscribe_command))
        self.application.add_handler(CommandHandler("mysubscriptions", self.my_subscriptions_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        logger.info("هندلرهای ربات تلگرام ثبت شدند.")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        پاسخ به دستور /start.
        """
        user = update.effective_user
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if update.effective_message.is_topic_message else None
        
        logger.info(f"دستور /start از کاربر {user.full_name} (ID: {user.id}) در چت {chat_id} (تاپیک: {thread_id}) دریافت شد.")
        
        welcome_message = (
            f"سلام {user.full_name}! 👋\n\n"
            "به ربات 'بازی‌های رایگان' خوش آمدید. من هر روز جدیدترین بازی‌های رایگان را از فروشگاه‌های مختلف پیدا می‌کنم و به شما اطلاع می‌دهم.\n\n"
            "برای شروع، می‌توانید با دستور /subscribe در فروشگاه‌های مورد علاقه خود مشترک شوید.\n"
            "برای دیدن اشتراک‌های فعلی خود از دستور /mysubscriptions استفاده کنید.\n"
            "برای لغو اشتراک از دستور /unsubscribe استفاده کنید."
        )
        await update.message.reply_text(welcome_message)

    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        پاسخ به دستور /subscribe. لیستی از فروشگاه‌ها را برای اشتراک نمایش می‌دهد.
        """
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if update.effective_message.is_topic_message else None
        
        logger.info(f"دستور /subscribe از چت {chat_id} (تاپیک: {thread_id}) دریافت شد.")

        # لیست فروشگاه‌های موجود (باید با نام‌های داخلی در main.py و db.py همخوانی داشته باشد)
        # اینها نام‌های انگلیسی هستند که در دیتابیس ذخیره می‌شوند.
        available_stores = {
            "epic games": "اپیک گیمز (ویندوز)",
            "steam": "استیم",
            "google play": "گوگل پلی",
            "epic games (android)": "اپیک گیمز (اندروید)", 
            "epic games (ios)": "اپیک گیمز (iOS)", 
            "ios app store": "اپ استور iOS",
            "playstation": "پلی‌استیشن",
            "xbox": "ایکس‌باکس",
            "gog": "GOG",
            "itch.io": "Itch.io",
            "indiegala": "ایندی‌گالا",
            "stove": "STOVE",
            "all": "همه فروشگاه‌ها" # گزینه برای اشتراک در همه
        }

        keyboard = []
        for internal_name, display_name in available_stores.items():
            keyboard.append([InlineKeyboardButton(display_name, callback_data=f"subscribe_{internal_name}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("در کدام فروشگاه‌ها مایلید مشترک شوید؟", reply_markup=reply_markup)

    async def unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        پاسخ به دستور /unsubscribe. لیستی از فروشگاه‌های مشترک شده را برای لغو اشتراک نمایش می‌دهد.
        """
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if update.effective_message.is_topic_message else None
        
        logger.info(f"دستور /unsubscribe از چت {chat_id} (تاپیک: {thread_id}) دریافت شد.")

        current_subscriptions = self.db.get_user_subscriptions(chat_id, thread_id)
        
        if not current_subscriptions:
            await update.message.reply_text("شما در هیچ فروشگاهی مشترک نیستید.")
            return

        keyboard = []
        # نگاشت برای نمایش نام‌های فارسی در دکمه‌های لغو اشتراک
        display_map = {
            "epic games": "اپیک گیمز (ویندوز)", "steam": "استیم", "google play": "گوگل پلی",
            "epic games (android)": "اپیک گیمز (اندروید)", "epic games (ios)": "اپیک گیمز (iOS)",
            "ios app store": "اپ استور iOS", "playstation": "پلی‌استیشن", "xbox": "ایکس‌باکس",
            "gog": "GOG", "itch.io": "Itch.io", "indiegala": "ایندی‌گالا", "stove": "STOVE",
            "all": "همه فروشگاه‌ها"
        }

        for store_name in current_subscriptions:
            display_name = display_map.get(store_name, store_name.capitalize())
            keyboard.append([InlineKeyboardButton(display_name, callback_data=f"unsubscribe_{store_name}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("کدام اشتراک را مایلید لغو کنید؟", reply_markup=reply_markup)

    async def my_subscriptions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        پاسخ به دستور /mysubscriptions. اشتراک‌های فعلی کاربر را نمایش می‌دهد.
        """
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if update.effective_message.is_topic_message else None
        
        logger.info(f"دستور /mysubscriptions از چت {chat_id} (تاپیک: {thread_id}) دریافت شد.")

        subscriptions = self.db.get_user_subscriptions(chat_id, thread_id)
        
        if not subscriptions:
            await update.message.reply_text("شما در هیچ فروشگاهی مشترک نیستید.")
            return

        # نگاشت برای نمایش نام‌های فارسی
        display_map = {
            "epic games": "اپیک گیمز (ویندوز)", "steam": "استیم", "google play": "گوگل پلی",
            "epic games (android)": "اپیک گیمز (اندروید)", "epic games (ios)": "اپیک گیمز (iOS)",
            "ios app store": "اپ استور iOS", "playstation": "پلی‌استیشن", "xbox": "ایکس‌باکس",
            "gog": "GOG", "itch.io": "Itch.io", "indiegala": "ایندی‌گالا", "stove": "STOVE",
            "all": "همه فروشگاه‌ها"
        }
        
        subscription_list = "\n".join([f"- {display_map.get(s, s.capitalize())}" for s in subscriptions])
        await update.message.reply_text(f"اشتراک‌های فعلی شما:\n{subscription_list}")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        هندلر برای دکمه‌های Inline Keyboard.
        """
        query = update.callback_query
        chat_id = query.message.chat_id
        thread_id = query.message.message_thread_id if query.message.is_topic_message else None
        
        logger.info(f"Callback query '{query.data}' از چت {chat_id} (تاپیک: {thread_id}) دریافت شد.")

        await query.answer() # برای حذف حالت لودینگ از دکمه

        action, store_name = query.data.split('_', 1)

        if action == "subscribe":
            self.db.add_subscription(chat_id, thread_id, store_name)
            display_name = {
                "epic games": "اپیک گیمز (ویندوز)", "steam": "استیم", "google play": "گوگل پلی",
                "epic games (android)": "اپیک گیمز (اندروید)", "epic games (ios)": "اپیک گیمز (iOS)",
                "ios app store": "اپ استور iOS", "playstation": "پلی‌استیشن", "xbox": "ایکس‌باکس",
                "gog": "GOG", "itch.io": "Itch.io", "indiegala": "ایندی‌گالا", "stove": "STOVE",
                "all": "همه فروشگاه‌ها"
            }.get(store_name, store_name.capitalize())
            await query.edit_message_text(f"شما با موفقیت در فروشگاه '{display_name}' مشترک شدید.")
        elif action == "unsubscribe":
            self.db.remove_subscription(chat_id, thread_id, store_name)
            display_name = {
                "epic games": "اپیک گیمز (ویندوز)", "steam": "استیم", "google play": "گوگل پلی",
                "epic games (android)": "اپیک گیمز (اندروید)", "epic games (ios)": "اپیک گیمز (iOS)",
                "ios app store": "اپ استور iOS", "playstation": "پلی‌استیشن", "xbox": "ایکس‌باکس",
                "gog": "GOG", "itch.io": "Itch.io", "indiegala": "ایندی‌گالا", "stove": "STOVE",
                "all": "همه فروشگاه‌ها"
            }.get(store_name, store_name.capitalize())
            await query.edit_message_text(f"اشتراک شما در فروشگاه '{display_name}' لغو شد.")

    async def send_formatted_message(self, game_data: Dict[str, Any], chat_id: int, thread_id: int = None) -> None:
        """
        یک پیام فرمت‌بندی شده حاوی اطلاعات بازی را به چت مشخص شده ارسال می‌کند.
        :param game_data: دیکشنری حاوی اطلاعات بازی.
        :param chat_id: ID چت مقصد.
        :param thread_id: ID تاپیک پیام (اختیاری، برای فوروم‌های تلگرام).
        """
        title = game_data.get('title', 'عنوان نامشخص')
        store = game_data.get('store', 'فروشگاه نامشخص')
        url = game_data.get('url', '#')
        image_url = game_data.get('image_url')
        description = game_data.get('persian_summary') or game_data.get('description', 'توضیحات موجود نیست.')
        
        # نگاشت نام‌های فروشگاه به فارسی برای نمایش در پیام
        store_display_map = {
            "epic games": "اپیک گیمز (ویندوز)",
            "steam": "استیم",
            "google play": "گوگل پلی",
            "epic games (android)": "اپیک گیمز (اندروید)", 
            "epic games (ios)": "اپیک گیمز (iOS)", 
            "ios app store": "اپ استور iOS",
            "playstation": "پلی‌استیشن",
            "xbox": "ایکس‌باکس",
            "gog": "GOG",
            "itch.io": "Itch.io",
            "indiegala": "ایندی‌گالا",
            "stove": "STOVE",
            "other": "سایر فروشگاه‌ها",
            "reddit": "ردیت"
        }
        display_store_name = store_display_map.get(store.lower(), store)

        # ساخت متن پیام
        message_text = (
            f"🎮 *بازی رایگان جدید!* 🎮\n\n"
            f"عنوان: *{title}*\n"
            f"فروشگاه: *{display_store_name}*\n"
            f"توضیحات: {description}\n\n"
            f"[دریافت بازی]({url})"
        )

        # اضافه کردن اطلاعات غنی‌سازی شده (در صورت وجود)
        if game_data.get('metacritic_score'):
            message_text += f"\nمتاکریتیک (منتقدان): {game_data['metacritic_score']}/100"
        if game_data.get('metacritic_userscore'):
            message_text += f"\nمتاکریتیک (کاربران): {game_data['metacritic_userscore']}/10"
        if game_data.get('steam_overall_score'):
            message_text += f"\nاستیم (کلی): {game_data['steam_overall_score']}% ({game_data['steam_overall_reviews_count']} رای)"
        if game_data.get('steam_recent_score'):
            message_text += f"\nاستیم (اخیر): {game_data['steam_recent_score']}% ({game_data['steam_recent_reviews_count']} رای)"
        
        # استفاده از ژانرهای ترجمه شده
        if game_data.get('persian_genres'):
            message_text += f"\nژانرها: {', '.join(game_data['persian_genres'])}"
        elif game_data.get('genres'):
            message_text += f"\nژانرها: {', '.join(game_data['genres'])}"

        # استفاده از رده‌بندی سنی ترجمه شده
        if game_data.get('persian_age_rating'):
            message_text += f"\nرده‌بندی سنی: {game_data['persian_age_rating']}"
        elif game_data.get('age_rating'):
            message_text += f"\nرده‌بندی سنی: {game_data['age_rating']}"

        if game_data.get('is_multiplayer') and game_data.get('is_online'):
            message_text += "\nتعداد بازیکن: چندنفره (آنلاین)"
        elif game_data.get('is_multiplayer'):
            message_text += "\nتعداد بازیکن: چندنفره (آفلاین)"
        elif game_data.get('is_online'):
            message_text += "\nتعداد بازیکن: تک‌نفره (آنلاین)"
        else:
            message_text += "\nتعداد بازیکن: تک‌نفره"

        if game_data.get('trailer'):
            message_text += f"\n[مشاهده تریلر]({game_data['trailer']})"

        try:
            if image_url:
                await self.application.bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=message_text,
                    parse_mode='Markdown',
                    message_thread_id=thread_id
                )
                logger.info(f"پیام حاوی عکس برای '{title}' به chat_id={chat_id} (تاپیک: {thread_id}) ارسال شد.")
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode='Markdown',
                    disable_web_page_preview=True, # برای جلوگیری از پیش‌نمایش لینک اگر تصویر نباشد
                    message_thread_id=thread_id
                )
                logger.info(f"پیام متنی برای '{title}' به chat_id={chat_id} (تاپیک: {thread_id}) ارسال شد.")
        except Exception as e:
            logger.error(f"خطا در ارسال پیام تلگرام برای '{title}' به chat_id={chat_id} (تاپیک: {thread_id}): {e}", exc_info=True)

    async def process_pending_updates(self):
        """
        آپدیت‌های معلق را پردازش می‌کند. این برای اجرای دستورات کاربران در زمان‌های غیر از زمان‌بندی اصلی است.
        """
        logger.info("شروع پردازش آپدیت‌های معلق تلگرام...")
        # Get updates (e.g., commands from users)
        updates = await self.application.bot.get_updates()
        if updates:
            logger.info(f"تعداد {len(updates)} آپدیت معلق یافت شد. در حال پردازش...")
            for update in updates:
                # Process each update using the registered handlers
                try:
                    await self.application.process_update(update)
                except Exception as e:
                    logger.error(f"خطا در پردازش آپدیت: {e}", exc_info=True)
        else:
            logger.info("هیچ آپدیت معلقی یافت نشد.")

    def run(self):
        """
        ربات را در حالت polling اجرا می‌کند. (برای توسعه/تست محلی)
        """
        logger.info("ربات تلگرام در حال اجرا در حالت polling...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

