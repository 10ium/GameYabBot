// ===== IMPORTS & DEPENDENCIES =====
import logging
from typing import List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError

from src.core.database import Database
from src.models.game import GameData
from src.config import TELEGRAM_STORE_DISPLAY_NAMES

// ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

// ===== CORE BUSINESS LOGIC =====
class TelegramBot:
    """Handles all Telegram bot interactions, including commands and notifications."""

    def __init__(self, token: str, db: Database):
        self.application = Application.builder().token(token).build()
        self.db = db
        self._register_handlers()
        logger.info(f"[{self.__class__.__name__}] Telegram bot initialized.")

    def _register_handlers(self):
        """Registers command and callback query handlers."""
        handlers = [
            CommandHandler("start", self.start_command),
            CommandHandler("subscribe", self.subscribe_command),
            CommandHandler("unsubscribe", self.unsubscribe_command),
            CommandHandler("mysubscriptions", self.my_subscriptions_command),
            CallbackQueryHandler(self.button_callback)
        ]
        self.application.add_handlers(handlers)
        logger.info(f"[{self.__class__.__name__}] Command handlers registered.")

    def _get_chat_info(self, update: Update) -> (int, Optional[int]):
        """Extracts chat_id and thread_id from an update."""
        chat_id = update.effective_chat.id
        thread_id = None
        if update.effective_message and update.effective_message.is_topic_message:
            thread_id = update.effective_message.message_thread_id
        return chat_id, thread_id

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat_id, thread_id = self._get_chat_info(update)
        logger.info(f"[{self.__class__.__name__}] /start from user='{user.full_name}' in chat={chat_id}, thread={thread_id}")

        welcome_message = (
            f"سلام {user.full_name}! 👋\n\n"
            "به ربات 'بازی‌های رایگان' خوش آمدید. من هر روز جدیدترین بازی‌های رایگان و تخفیف‌های ویژه را از فروشگاه‌های مختلف پیدا کرده و به شما اطلاع می‌دهم.\n\n"
            "دستورات موجود:\n"
            "/subscribe - برای اشتراک در فروشگاه‌های مورد علاقه.\n"
            "/unsubscribe - برای لغو اشتراک.\n"
            "/mysubscriptions - برای مشاهده اشتراک‌های فعلی."
        )
        await update.message.reply_text(welcome_message)

    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id, thread_id = self._get_chat_info(update)
        logger.info(f"[{self.__class__.__name__}] /subscribe from chat={chat_id}, thread={thread_id}")

        keyboard = [
            [InlineKeyboardButton(display_name, callback_data=f"subscribe_{internal_name}")]
            for internal_name, display_name in sorted(TELEGRAM_STORE_DISPLAY_NAMES.items())
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("در کدام فروشگاه‌ها مایلید مشترک شوید؟", reply_markup=reply_markup)

    async def unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id, thread_id = self._get_chat_info(update)
        logger.info(f"[{self.__class__.__name__}] /unsubscribe from chat={chat_id}, thread={thread_id}")

        subscriptions = self.db.get_user_subscriptions(chat_id, thread_id)
        if not subscriptions:
            await update.message.reply_text("شما در حال حاضر در هیچ فروشگاهی مشترک نیستید.")
            return

        keyboard = [
            [InlineKeyboardButton(TELEGRAM_STORE_DISPLAY_NAMES.get(store, store), callback_data=f"unsubscribe_{store}")]
            for store in sorted(subscriptions)
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("کدام اشتراک را لغو می‌کنید؟", reply_markup=reply_markup)

    async def my_subscriptions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id, thread_id = self._get_chat_info(update)
        logger.info(f"[{self.__class__.__name__}] /mysubscriptions from chat={chat_id}, thread={thread_id}")

        subscriptions = self.db.get_user_subscriptions(chat_id, thread_id)
        if not subscriptions:
            await update.message.reply_text("شما در هیچ فروشگاهی مشترک نیستید.")
            return
            
        sub_list = "\n".join([f"• {TELEGRAM_STORE_DISPLAY_NAMES.get(s, s)}" for s in sorted(subscriptions)])
        await update.message.reply_text(f"اشتراک‌های فعلی شما:\n{sub_list}")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        
        chat_id, thread_id = self._get_chat_info(query.message)
        
        action, store_name = query.data.split('_', 1)
        display_name = TELEGRAM_STORE_DISPLAY_NAMES.get(store_name, store_name)
        
        logger.info(f"[{self.__class__.__name__}] Callback query: action='{action}', store='{store_name}' for chat={chat_id}")

        if action == "subscribe":
            self.db.add_subscription(chat_id, store_name, thread_id)
            await query.edit_message_text(text=f"✅ شما با موفقیت در «{display_name}» مشترک شدید.")
        elif action == "unsubscribe":
            self.db.remove_subscription(chat_id, store_name, thread_id)
            await query.edit_message_text(text=f"🗑️ اشتراک شما در «{display_name}» لغو شد.")

    def _format_message_text(self, game: GameData) -> str:
        """Formats the text content for a game notification."""
        title = game.get('title', 'عنوان نامشخص')
        store_key = game.get('store', 'other').lower().replace(' ', '')
        store_name = TELEGRAM_STORE_DISPLAY_NAMES.get(store_key, game.get('store', 'فروشگاه نامشخص'))
        
        header = f"🎮 *بازی رایگان جدید!* 🎮" if game.get('is_free') else f"💰 *تخفیف ویژه!* 💰"
        
        message_parts = [
            f"{header}\n",
            f"ርዕስ: *{title}*",
            f"فروشگاه: *{store_name}*",
        ]
        
        if not game.get('is_free') and game.get('discount_text'):
            message_parts.append(f"تخفیف: *{game['discount_text']}*")
        
        if game.get('is_dlc_or_addon'):
            message_parts.append("نوع: *DLC / محتوای اضافی*")

        description = game.get('persian_summary') or game.get('description', '')
        if description:
            max_desc_len = 250
            truncated_desc = description[:max_desc_len] + "..." if len(description) > max_desc_len else description
            message_parts.append(f"\nتوضیحات: {truncated_desc}")

        enriched_info = []
        if game.get('steam_overall_score'):
            enriched_info.append(f"امتیاز استیم: {game['steam_overall_score']}%")
        if game.get('metacritic_score'):
            enriched_info.append(f"متاکریتیک: {game['metacritic_score']}/100")
        if enriched_info:
            message_parts.append("\n" + " | ".join(enriched_info))

        if game.get('persian_genres'):
            message_parts.append(f"ژانرها: {', '.join(game['persian_genres'])}")
        
        message_parts.append(f"\n[🔗 دریافت از فروشگاه]({game.get('url', '#')})")
        if game.get('trailer'):
            message_parts.append(f"[🎬 مشاهده تریلر]({game['trailer']})")
            
        return "\n".join(message_parts)

    async def send_game_notification(self, game: GameData, chat_id: int, thread_id: Optional[int] = None) -> None:
        """Sends a formatted game notification to a specific chat."""
        message_text = self._format_message_text(game)
        image_url = game.get('image_url')

        try:
            if image_url:
                await self.application.bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=message_text,
                    parse_mode='Markdown',
                    message_thread_id=thread_id
                )
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode='Markdown',
                    disable_web_page_preview=False,
                    message_thread_id=thread_id
                )
            logger.info(f"[{self.__class__.__name__}] Notification for '{game['title']}' sent to chat={chat_id}")
        except TelegramError as e:
            logger.error(f"[{self.__class__.__name__}] Failed to send notification for '{game['title']}' to chat={chat_id}: {e}")
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] An unexpected error occurred sending notification to chat={chat_id}: {e}", exc_info=True)
