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

VALID_STORES = ["epic games", "gog", "steam", "all"]

class TelegramBot:
    def __init__(self, token: str, db: Database):
        if not token:
            raise ValueError("توکن تلگرام ارائه نشده است.")
        # ساخت Application به جای Bot به تنهایی
        self.application = Application.builder().token(token).build()
        self.bot = self.application.bot # دسترسی به شیء Bot از طریق اپلیکیشن
        self.db = db
        self._register_handlers()
        logging.info("نمونه ربات تلگرام و کنترل‌کننده‌های دستورات با موفقیت ایجاد شدند.")

    @staticmethod
    def _escape_markdown_v2(text: str) -> str:
        if not isinstance(text, str): return ""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return "".join(f'\\{char}' if char in escape_chars else char for char in text)

    def _format_message(self, game_data: Dict[str, Any]) -> str:
        title = self._escape_markdown_v2(game_data.get('title', 'بدون عنوان'))
        store = self._escape_markdown_v2(game_data.get('store', 'نامشخص'))
        url = game_data.get('url', '')
        persian_summary = game_data.get('persian_summary')
        summary_text = ""
        if persian_summary:
            if len(persian_summary) > 400:
                persian_summary = persian_summary[:400] + "..."
            summary_text = f"\n📝 *خلاصه داستان:*\n_{self._escape_markdown_v2(persian_summary)}_\n"
        scores_parts = []
        if game_data.get('metacritic_score'):
            scores_parts.append(f"⭐ *Metacritic:* {game_data['metacritic_score']}/100")
        if game_data.get('steam_score'):
            scores_parts.append(f"👍 *Steam:* {game_data['steam_score']}% ({game_data.get('steam_reviews_count', 0)} رای)")
        scores_text = "\n".join(scores_parts)
        if scores_text:
            scores_text = f"\n📊 *امتیازات:*\n{scores_text}\n"
        details_parts = []
        if game_data.get('genres'):
            details_parts.append(f"🔸 *ژانر:* {self._escape_markdown_v2(', '.join(game_data['genres']))}")
        if game_data.get('trailer'):
            details_parts.append(f"🎬 [لینک تریلر]({game_data['trailer']})")
        details_text = "\n".join(details_parts)
        if details_text:
            details_text = f"\n{details_text}\n"
        return (
            f"🎮 *{title}* رایگان شد\\!\n\n"
            f"🏪 *فروشگاه:* `{store.upper()}`\n"
            f"{summary_text}"
            f"{scores_text}"
            f"{details_text}"
            f"🔗 [دریافت بازی از فروشگاه]({url})"
        )

    async def send_formatted_message(self, game_data: Dict[str, Any], chat_id: int, thread_id: Optional[int] = None):
        message_text = self._format_message(game_data)
        image_url = game_data.get('image_url')
        try:
            if image_url:
                await self.bot.send_photo(chat_id=chat_id, photo=image_url, caption=message_text, parse_mode=ParseMode.MARKDOWN_V2, message_thread_id=thread_id)
            else:
                await self.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True, message_thread_id=thread_id)
        except TelegramError as e:
            logging.error(f"خطای تلگرام هنگام ارسال به {chat_id}: {e.message}")
            raise

    async def _user_is_admin(self, chat_id: int, user_id: int) -> bool:
        if chat_id > 0: return True
        try:
            chat_admins = await self.bot.get_chat_administrators(chat_id)
            return user_id in [admin.user.id for admin in chat_admins]
        except TelegramError: return False

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.db.add_subscription(chat_id, thread_id=None, store='all')
        await update.message.reply_text("سلام! من ربات گیم رایگان هستم. شما به طور خودکار برای دریافت تمام اعلان‌ها مشترک شدید.\nبرای مشاهده لیست کامل دستورات /help را ارسال کنید.")

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "راهنمای دستورات ربات گیم رایگان:\n\n"
            "🔹 `/subscribe [store_name]`\n"
            "برای ثبت‌نام این چت (یا تاپیک) جهت دریافت اعلان‌های یک فروشگاه خاص. مثال:\n"
            "`/subscribe epic games`\n"
            "`/subscribe all` (برای دریافت همه اعلان‌ها)\n\n"
            "🔸 `/unsubscribe [store_name]`\n"
            "برای لغو اشتراک. مثال:\n"
            "`/unsubscribe steam`\n\n"
            f"فروشگاه‌های معتبر: `{', '.join(VALID_STORES)}`\n\n"
            "توجه: فقط ادمین‌های گروه یا کانال می‌توانند از این دستورات استفاده کنند."
        )
        await update.message.reply_text(help_text)

    async def _subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        if not await self._user_is_admin(chat_id, user_id):
            await update.message.reply_text("متاسفم، فقط ادمین‌ها می‌توانند اشتراک را مدیریت کنند.")
            return
        store = " ".join(context.args).lower() if context.args else "all"
        if store not in VALID_STORES:
            await update.message.reply_text(f"نام فروشگاه نامعتبر است. لطفاً یکی از این موارد را استفاده کنید: {', '.join(VALID_STORES)}")
            return
        if self.db.add_subscription(chat_id, thread_id, store):
            await update.message.reply_text(f"✅ اشتراک برای اعلان‌های '{store}' با موفقیت ثبت شد.")
        else:
            await update.message.reply_text("این اشتراک از قبل وجود دارد.")

    async def _unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        if not await self._user_is_admin(chat_id, user_id):
            await update.message.reply_text("متاسفم، فقط ادمین‌ها می‌توانند اشتراک را مدیریت کنند.")
            return
        if not context.args:
            await update.message.reply_text("لطفاً نام فروشگاه را برای لغو اشتراک مشخص کنید. مثال: `/unsubscribe all`")
            return
        store = " ".join(context.args).lower()
        if self.db.remove_subscription(chat_id, thread_id, store):
            await update.message.reply_text(f"❌ اشتراک برای اعلان‌های '{store}' با موفقیت لغو شد.")
        else:
            await update.message.reply_text("اشتراکی برای لغو یافت نشد.")

    async def _on_new_chat_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot_id = self.bot.id
        for member in update.message.new_chat_members:
            if member.id == bot_id:
                chat_id = update.message.chat_id
                logging.info(f"ربات به گروه جدیدی با شناسه {chat_id} اضافه شد.")
                self.db.add_subscription(chat_id, thread_id=None, store='all')
                await self.bot.send_message(
                    chat_id,
                    "سلام! ممنون که من را به این گروه اضافه کردید.\n"
                    "این گروه به طور خودکار برای دریافت اعلان تمام بازی‌های رایگان مشترک شد.\n"
                    "ادمین‌ها می‌توانند با دستور /help اشتراک‌ها را مدیریت کنند."
                )
                break

    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(CommandHandler("help", self._help_command))
        self.application.add_handler(CommandHandler("subscribe", self._subscribe_command))
        self.application.add_handler(CommandHandler("unsubscribe", self._unsubscribe_command))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._on_new_chat_member))

    async def process_pending_updates(self):
        await self.application.initialize()
        updates = await self.application.bot.get_updates(timeout=10)
        if not updates:
            logging.info("هیچ دستور جدیدی برای پردازش یافت نشد.")
            await self.application.shutdown()
            return
        logging.info(f"{len(updates)} دستور جدید برای پردازش یافت شد.")
        for update in updates:
            await self.application.process_update(update)
        if updates:
            last_update_id = updates[-1].update_id
            await self.application.bot.get_updates(offset=last_update_id + 1)
        await self.application.shutdown()
