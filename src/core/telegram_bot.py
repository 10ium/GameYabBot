import logging
from typing import Dict, Any, Optional

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

from .database import Database 

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

VALID_STORES = [
    "epic games", "gog", "steam", "all",
    "xbox", "playstation", "nintendo", "stove",
    "indiegala", "itch.io", "ios app store", "google play",
    "other"
]

class TelegramBot:
    def __init__(self, token: str, db: Database):
        if not token:
            logger.error("توکن تلگرام ارائه نشده است. ربات نمی‌تواند شروع به کار کند.")
            raise ValueError("توکن تلگرام ارائه نشده است.")
            
        self.bot = Bot(token)
        self.application = Application.builder().bot(self.bot).build()
        self.db = db
        self._register_handlers()
        logger.info("نمونه ربات تلگرام و کنترل‌کننده‌های دستورات با موفقیت ایجاد شدند.")

    @staticmethod
    def _escape_markdown_v2(text: str) -> str:
        """
        کاراکترهای خاص را برای فرمت MarkdownV2 در تلگرام Escape می‌کند.
        """
        if not isinstance(text, str):
            return ""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return "".join(f'\\{char}' if char in escape_chars else char for char in text)

    def _format_message(self, game_data: Dict[str, Any]) -> str:
        """
        داده‌های بازی را به فرمت پیام تلگرام (MarkdownV2) تبدیل می‌کند.
        """
        title = self._escape_markdown_v2(game_data.get('title', 'بدون عنوان'))
        store = self._escape_markdown_v2(game_data.get('store', 'نامشخص'))
        url = game_data.get('url', '')
        
        summary_to_use = game_data.get('persian_summary') or game_data.get('description')
        summary_text = ""
        if summary_to_use:
            if len(summary_to_use) > 250: # کوتاه کردن برای تلگرام
                summary_to_use = summary_to_use[:250] + "..."
            summary_text = f"\n📝 *خلاصه داستان:*\n_{self._escape_markdown_v2(summary_to_use)}_\n"
        
        scores_parts = []
        if game_data.get('metacritic_score'):
            scores_parts.append(f"⭐ *Metacritic (منتقدان):* {game_data['metacritic_score']}/100") # تغییر متن
        if game_data.get('metacritic_userscore'):
            scores_parts.append(f"👥 *Metacritic (کاربران):* {game_data['metacritic_userscore']}/10")
        if game_data.get('steam_overall_score'): # نمره کلی استیم
            scores_parts.append(f"👍 *Steam (کلی):* {game_data['steam_overall_score']}% \\({game_data.get('steam_overall_reviews_count', 0)} رای\\)")
        if game_data.get('steam_recent_score'): # نمره اخیر استیم
            scores_parts.append(f"🔥 *Steam (اخیر):* {game_data['steam_recent_score']}% \\({game_data.get('steam_recent_reviews_count', 0)} رای\\)")
        
        scores_text = "\n".join(scores_parts)
        if scores_text:
            scores_text = f"\n📊 *امتیازات:*\n{scores_text}\n"
        
        details_parts = []
        if game_data.get('genres'):
            details_parts.append(f"🔸 *ژانر:* {self._escape_markdown_v2(', '.join(game_data['genres']))}")
        
        player_info = ""
        if game_data.get('is_multiplayer') and game_data.get('is_online'):
            player_info = "چندنفره (آنلاین)"
        elif game_data.get('is_multiplayer'):
            player_info = "چندنفره (آفلاین)"
        elif game_data.get('is_online'):
            player_info = "تک‌نفره (آنلاین)"
        else:
            player_info = "تک‌نفره"
        details_parts.append(f"🎮 *تعداد بازیکن:* {player_info}")

        if game_data.get('trailer'):
            details_parts.append(f"🎬 [لینک تریلر]({self._escape_markdown_v2(game_data['trailer'])})")
        
        details_text = "\n".join(details_parts)
        if details_text:
            details_text = f"\n{details_text}\n"
        
        escaped_url = self._escape_markdown_v2(url)

        return (
            f"🎮 *{title}* رایگان شد\\!\n\n"
            f"🏪 *فروشگاه:* `{store.upper()}`\n"
            f"{summary_text}"
            f"{scores_text}"
            f"{details_text}"
            f"🔗 [دریافت بازی از فروشگاه]({escaped_url})"
        )

    async def send_formatted_message(self, game_data: Dict[str, Any], chat_id: int, thread_id: Optional[int] = None):
        """
        پیام فرمت‌بندی شده بازی را به یک چت تلگرام ارسال می‌کند.
        """
        message_text = self._format_message(game_data)
        image_url = game_data.get('image_url')
        game_title = game_data.get('title', 'بدون عنوان')

        logger.info(f"📤 در حال ارسال پیام برای '{game_title}' به chat_id={chat_id}, thread_id={thread_id}...")
        try:
            if image_url:
                logger.debug(f"ارسال عکس برای '{game_title}' (URL: {image_url})")
                await self.bot.send_photo(chat_id=chat_id, photo=image_url, caption=message_text, parse_mode=ParseMode.MARKDOWN_V2, message_thread_id=thread_id)
            else:
                logger.debug(f"ارسال پیام متنی برای '{game_title}' (بدون تصویر).")
                await self.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True, message_thread_id=thread_id)
            logger.info(f"✅ پیام برای '{game_title}' با موفقیت به chat_id={chat_id} ارسال شد.")
        except TelegramError as e:
            logger.error(f"❌ خطای تلگرام هنگام ارسال پیام برای '{game_title}' به chat_id={chat_id}: {e.message} (کد خطا: {e.api_kwargs.get('error_code', 'نامشخص')}, توضیحات: {e.api_kwargs.get('description', 'نامشخص')})")
        except Exception as e:
            logger.critical(f"🔥 خطای بحرانی و ناشناخته هنگام ارسال پیام برای '{game_title}' به chat_id={chat_id}: {e}", exc_info=True)


    async def _user_is_admin(self, chat_id: int, user_id: int) -> bool:
        """
        بررسی می‌کند که آیا کاربر ادمین چت است یا خیر.
        برای چت‌های خصوصی، همیشه True برمی‌گرداند.
        """
        if chat_id > 0:  # چت خصوصی
            logger.debug(f"بررسی ادمین برای چت خصوصی (chat_id={chat_id}): همیشه True")
            return True
        try:
            logger.debug(f"در حال دریافت ادمین‌های چت برای chat_id={chat_id}...")
            chat_admins = await self.bot.get_chat_administrators(chat_id)
            is_admin = user_id in [admin.user.id for admin in chat_admins]
            logger.info(f"کاربر {user_id} در چت {chat_id} ادمین است: {is_admin}")
            return is_admin
        except TelegramError as e:
            logger.warning(f"⚠️ خطا در بررسی ادمین برای چت {chat_id} و کاربر {user_id}: {e.message}. فرض می‌کنیم کاربر ادمین نیست.")
            return False

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        هندلر دستور /start.
        """
        chat_id = update.message.chat_id
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        user_id = update.message.from_user.id
        
        logger.info(f"دستور /start از کاربر {user_id} در chat_id={chat_id}, thread_id={thread_id} دریافت شد.")

        if self.db.add_subscription(chat_id, thread_id=thread_id, store='all'):
            logger.info(f"✅ اشتراک جدید برای chat_id={chat_id}, thread_id={thread_id}, store='all' ثبت شد.")
            await update.message.reply_text("سلام! من ربات گیم رایگان هستم. شما به طور خودکار برای دریافت تمام اعلان‌ها مشترک شدید.\nبرای مشاهده لیست کامل دستورات /help را ارسال کنید.")
        else:
            logger.info(f"ℹ️ اشتراک برای chat_id={chat_id}, thread_id={thread_id} از قبل وجود داشت.")
            await update.message.reply_text("سلام! شما از قبل مشترک هستید. برای مشاهده لیست کامل دستورات /help را ارسال کنید.")


    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        هندلر دستور /help.
        """
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        logger.info(f"دستور /help از کاربر {user_id} در chat_id={chat_id} دریافت شد.")
        help_text = (
            "راهنمای دستورات ربات گیم رایگان:\n\n"
            "🔹 `/subscribe \\[store_name\\]` برای ثبت‌نام این چت (یا تاپیک) جهت دریافت اعلان‌های یک فروشگاه خاص\\. مثال:\n"
            "`/subscribe epic games`\n"
            "`/subscribe all` \\(برای دریافت همه اعلان‌ها\\)\n\n"
            "🔸 `/unsubscribe \\[store_name\\]` برای لغو اشتراک\\. مثال:\n"
            "`/unsubscribe steam`\n\n"
            f"فروشگاه‌های معتبر: `{', '.join(VALID_STORES)}`\n\n"
            "توجه: فقط ادمین‌های گروه یا کانال می‌توانند از این دستورات استفاده کنند."
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2) 

    async def _subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        هندلر دستور /subscribe.
        """
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        store = " ".join(context.args).lower() if context.args else "all"

        logger.info(f"دستور /subscribe {store} از کاربر {user_id} در chat_id={chat_id}, thread_id={thread_id} دریافت شد.")
        
        if not await self._user_is_admin(chat_id, user_id):
            logger.warning(f"⛔️ کاربر {user_id} (غیر ادمین) تلاش کرد در chat_id={chat_id} اشتراک را مدیریت کند.")
            await update.message.reply_text("متاسفم، فقط ادمین‌ها می‌توانند اشتراک را مدیریت کنند.")
            return
        
        if store not in VALID_STORES:
            logger.warning(f"❌ نام فروشگاه نامعتبر '{store}' برای اشتراک در chat_id={chat_id}.")
            await update.message.reply_text(f"نام فروشگاه نامعتبر است. لطفاً یکی از این موارد را استفاده کنید: {', '.join(VALID_STORES)}")
            return
        
        if self.db.add_subscription(chat_id, thread_id, store):
            logger.info(f"✅ اشتراک برای اعلان‌های '{store}' با موفقیت در chat_id={chat_id}, thread_id={thread_id} ثبت شد.")
            await update.message.reply_text(f"✅ اشتراک برای اعلان‌های '{store}' با موفقیت ثبت شد.")
        else:
            logger.info(f"ℹ️ اشتراک برای '{store}' در chat_id={chat_id}, thread_id={thread_id} از قبل وجود داشت.")
            await update.message.reply_text("این اشتراک از قبل وجود دارد.")

    async def _unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        هندلر دستور /unsubscribe.
        """
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        logger.info(f"دستور /unsubscribe از کاربر {user_id} در chat_id={chat_id}, thread_id={thread_id} دریافت شد.")

        if not await self._user_is_admin(chat_id, user_id):
            logger.warning(f"⛔️ کاربر {user_id} (غیر ادمین) تلاش کرد در chat_id={chat_id} اشتراک را لغو کند.")
            await update.message.reply_text("متاسفم، فقط ادمین‌ها می‌توانند اشتراک را مدیریت کنند.")
            return
        
        if not context.args:
            logger.warning(f"❌ نام فروشگاه برای لغو اشتراک در chat_id={chat_id} مشخص نشده بود.")
            await update.message.reply_text("لطفاً نام فروشگاه را برای لغو اشتراک مشخص کنید. مثال: `/unsubscribe all`")
            return
        
        store = " ".join(context.args).lower()
        if self.db.remove_subscription(chat_id, thread_id, store):
            logger.info(f"✅ اشتراک برای اعلان‌های '{store}' با موفقیت از chat_id={chat_id}, thread_id={thread_id} لغو شد.")
            await update.message.reply_text(f"❌ اشتراک برای اعلان‌های '{store}' با موفقیت لغو شد.")
        else:
            logger.info(f"ℹ️ اشتراکی برای '{store}' در chat_id={chat_id}, thread_id={thread_id} برای لغو یافت نشد.")
            await update.message.reply_text("این اشتراک از قبل وجود دارد.")

    async def _on_new_chat_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        هندلر برای زمانی که ربات به یک گروه جدید اضافه می‌شود.
        """
        bot_id = self.bot.id
        for member in update.message.new_chat_members:
            if member.id == bot_id:
                chat_id = update.message.chat_id
                thread_id = update.message.message_thread_id if update.message.is_topic_message else None
                logger.info(f"🤖 ربات به گروه جدیدی با شناسه {chat_id} و تاپیک {thread_id} اضافه شد.")
                
                if self.db.add_subscription(chat_id, thread_id=thread_id, store='all'):
                    logger.info(f"✅ اشتراک پیش‌فرض برای 'all' در chat_id={chat_id}, thread_id={thread_id} ثبت شد.")
                    await self.bot.send_message(
                        chat_id,
                        "سلام! ممنون که من را به این گروه اضافه کردید.\n"
                        "این گروه به طور خودکار برای دریافت اعلان تمام بازی‌های رایگان مشترک شد.\n"
                        "ادمین‌ها می‌توانند با دستور /help اشتراک‌ها را مدیریت کنند.",
                        message_thread_id=thread_id
                    )
                else:
                    logger.info(f"ℹ️ ربات قبلاً در chat_id={chat_id}, thread_id={thread_id} فعال بود (اشتراک از قبل موجود).")
                    await self.bot.send_message(
                        chat_id,
                        "سلام! من قبلاً در این گروه فعال بودم. خوش برگشتید!\n"
                        "ادمین‌ها می‌توانند با دستور /help اشتراک‌ها را مدیریت کنند.",
                        message_id=thread_id
                    )
                break

    def _register_handlers(self):
        """
        هندلرهای دستورات را به اپلیکیشن ربات اضافه می‌کند.
        """
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(CommandHandler("help", self._help_command))
        self.application.add_handler(CommandHandler("subscribe", self._subscribe_command))
        self.application.add_handler(CommandHandler("unsubscribe", self._unsubscribe_command))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._on_new_chat_member))

    async def process_pending_updates(self):
        """
        به‌روزرسانی‌های در حال انتظار را از تلگرام دریافت و پردازش می‌کند.
        """
        logger.info("🚀 شروع فرآیند دریافت و پردازش به‌روزرسانی‌های تلگرام...")
        await self.application.initialize()
        
        updates = await self.application.bot.get_updates(timeout=10)
        
        if not updates:
            logger.info("هیچ دستور جدیدی برای پردازش یافت نشد.")
            await self.application.shutdown()
            logger.info("🏁 فرآیند پردازش به‌روزرسانی‌ها به پایان رسید.")
            return

        logger.info(f"📦 {len(updates)} دستور جدید برای پردازش یافت شد.")
        
        for update in updates:
            logger.debug(f"در حال پردازش به‌روزرسانی: {update.update_id}")
            await self.application.process_update(update)
        
        if updates:
            last_update_id = updates[-1].update_id
            logger.info(f"تنظیم offset به‌روزرسانی به {last_update_id + 1} برای جلوگیری از پردازش مجدد.")
            await self.application.bot.get_updates(offset=last_update_id + 1)
            
        await self.application.shutdown()
        logger.info("🏁 فرآیند پردازش به‌روزرسانی‌ها به پایان رسید.")
