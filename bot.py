import logging
import asyncio
import signal
import sys
from telegram import Bot, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from telegram.error import TelegramError
from collections import defaultdict
import time
import os

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '0')  # Default to '0' if not set
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '').split(',')  # Default to empty list if not set

# Convert LOG_CHANNEL_ID to int and validate
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    LOG_CHANNEL_ID = 0

# Initialize a Bot instance for sending logs to the group
log_bot = Bot(token=BOT_TOKEN)

# Custom logging handler to send logs to Telegram group
class TelegramGroupHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        asyncio.create_task(self.send_log_to_group(log_entry))

    async def send_log_to_group(self, log_entry):
        try:
            await log_bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"**Log Entry:**\n{log_entry}")
        except TelegramError as e:
            print(f"Error sending log to Telegram group: {e}")

# Configure logging to send to Telegram group
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.handlers = []
telegram_handler = TelegramGroupHandler()
telegram_handler.setLevel(logging.INFO)
logger.addHandler(telegram_handler)

# User state management
user_states = defaultdict(lambda: {
    'language': 'en',
    'last_action': None,
    'last_season': None,
    'season_access': {},
})

# Lockout duration (24 hours in seconds)
LOCKOUT_DURATION = 24 * 60 * 60

# Auto-delete duration (1 hour in seconds)
AUTO_DELETE_DURATION = 60 * 60

# Language support
LANGUAGES = {
    'en': {
        'welcome': 'Choose an option:',
        'invalid_season': 'Invalid season selection.',
        'season_not_found': 'Season not found.',
        'help': 'Available commands:\n/start - Start the bot and see the menu\n/clearhistory - Remove history\n/owner - Show owner info\n/mainchannel - Join our main channel\n/guide - View usage guide\n/broadcast - Send a message to all users (admin only)',
        'settings': 'Settings are not yet implemented in this version.',
        'status': 'Bot is running normally.',
        'status_error': 'Bot is experiencing issues. Please try again later.',
        'link_locked': 'You have already accessed this season’s link. Please wait 24 hours to access it again.',
        'clearhistory': 'Your history has been cleared! 🗑️',
        'owner': 'Owner: @Dhileep_S 👨‍💼',
        'mainchannel': 'Join our main channel: @bot_paiyan_official 📢',
        'guide': '✨ **Usage Guide** ✨\n1. 🌟 Click /start to see the season menu.\n2. 🎬 Select a season (e.g., Season 1).\n3. 🔗 Click "Link-Shortner" to get the season link.\n4. ✅ Resolve the link to access your file.\n5. ℹ️ Use /help for more commands!',
        'broadcast': 'Broadcast message sent (logged to group). 📢',
        'not_allowed': 'You are not allowed to use this command. 🚫 Only admins can broadcast.'
    }
}

# Season data (Naruto seasons with placeholder links)
season_data = {
    "season_1": {
        "link": "https://example.com/season1/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season1",
        "episodes": {
            "Episode 1": "https://example.com/season1/episode1",
            "Episode 2": "https://example.com/season1/episode2",
        }
    },
    "season_2": {
        "link": "https://example.com/season2/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season2",
        "episodes": {
            "Episode 1": "https://example.com/season2/episode1",
            "Episode 2": "https://example.com/season2/episode2",
        }
    },
    "season_3": {
        "link": "https://example.com/season3/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season3",
        "episodes": {
            "Episode 1": "https://example.com/season3/episode1",
            "Episode 2": "https://example.com/season3/episode2",
        }
    },
    "season_4": {
        "link": "https://example.com/season4/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season4",
        "episodes": {
            "Episode 1": "https://example.com/season4/episode1",
            "Episode 2": "https://example.com/season4/episode2",
        }
    },
    "season_5": {
        "link": "https://example.com/season5/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season5",
        "episodes": {
            "Episode 1": "https://example.com/season5/episode1",
            "Episode 2": "https://example.com/season5/episode2",
        }
    },
    "season_6": {
        "link": "https://example.com/season6/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season6",
        "episodes": {
            "Episode 1": "https://example.com/season6/episode1",
            "Episode 2": "https://example.com/season6/episode2",
        }
    },
    "season_7": {
        "link": "https://example.com/season7/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season7",
        "episodes": {
            "Episode 1": "https://example.com/season7/episode1",
            "Episode 2": "https://example.com/season7/episode2",
        }
    },
    "season_8": {
        "link": "https://example.com/season8/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season8",
        "episodes": {
            "Episode 1": "https://example.com/season8/episode1",
            "Episode 2": "https://example.com/season8/episode2",
        }
    },
    "season_9": {
        "link": "https://example.com/season9/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season9",
        "episodes": {
            "Episode 1": "https://example.com/season9/episode1",
            "Episode 2": "https://example.com/season9/episode2",
        }
    },
    "season_10": {
        "link": "https://example.com/season10/link",
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season10",
        "episodes": {
            "Episode 1": "https://example.com/season10/episode1",
            "Episode 2": "https://example.com/season10/episode2",
        }
    },
}

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(AUTO_DELETE_DURATION)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id} in chat {chat_id}")
    except TelegramError as e:
        logger.error(f"Failed to delete message {message_id}: {str(e)}")

async def send_season_info(update: Update, context: ContextTypes.DEFAULT_TYPE, season_key: str):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']

    season_info = season_data.get(season_key)
    if not season_info:
        message = await context.bot.send_message(chat_id=update.effective_chat.id, text=LANGUAGES[lang]['season_not_found'])
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
        return

    try:
        link = season_info["link"]
        start_id_reference = season_info["start_id_ref"]
        season_name = f"Season {season_key.split('_')[1]}"
        logger.info(f"User {user_id} accessed {season_key}: {start_id_reference}")

        season_access = user_states[user_id]['season_access'].get(season_key, {})
        current_time = time.time()

        if not season_access.get('first_access_time'):
            user_states[user_id]['season_access'][season_key] = {
                'first_access_time': current_time,
                'resolved_time': None
            }
            keyboard = [
                [InlineKeyboardButton(season_name, callback_data=f"info_{season_key}")],
                [InlineKeyboardButton("Link-Shortner", callback_data=f"resolve_{season_key}")],
                [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")]
            ]
        else:
            resolved_time = season_access.get('resolved_time')
            if resolved_time and (current_time - resolved_time) < LOCKOUT_DURATION:
                message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=LANGUAGES[lang]['link_locked']
                )
                asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
                return
            else:
                keyboard = [
                    [InlineKeyboardButton(season_name, callback_data=f"info_{season_key}")],
                    [InlineKeyboardButton("Link-Shortner", callback_data=f"resolve_{season_key}")],
                    [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")]
                ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Accessing {season_name}:",
            reply_markup=reply_markup
        )
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
        user_states[user_id]['last_season'] = season_key

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"📥 {update.effective_user.first_name} accessed {season_name}")
    except TelegramError as e:
        logger.error(f"Error sending season info: {str(e)}")
        message = await context.bot.send_message(chat_id=update.effective_chat.id, text="An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    try:
        keyboard = [
            ['Season 1'],
            ['Season 2'],
            ['Season 3'],
            ['Season 4'],
            ['Season 5'],
            ['Season 6'],
            ['Season 7'],
            ['Season 8'],
            ['Season 9'],
            ['Season 10'],
            ['Help'],
            ['Settings']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        message = await update.message.reply_text(LANGUAGES[lang]['welcome'], reply_markup=reply_markup)
        logger.info(f"User {user_id} used /start command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"🚀 {update.effective_user.first_name} started the bot")
    except TelegramError as e:
        logger.error(f"Error in start command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    try:
        user_states[user_id] = {
            'language': 'en',
            'last_action': None,
            'last_season': None,
            'season_access': {},
        }
        message = await update.message.reply_text(LANGUAGES[lang]['clearhistory'])
        logger.info(f"User {user_id} used /clearhistory command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"🗑️ {update.effective_user.first_name} cleared their history")
    except TelegramError as e:
        logger.error(f"Error in clearhistory command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    try:
        message = await update.message.reply_text(LANGUAGES[lang]['owner'])
        logger.info(f"User {user_id} used /owner command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"👨‍💼 {update.effective_user.first_name} requested owner info")
    except TelegramError as e:
        logger.error(f"Error in owner command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def mainchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    try:
        message = await update.message.reply_text(LANGUAGES[lang]['mainchannel'])
        logger.info(f"User {user_id} used /mainchannel command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"📢 {update.effective_user.first_name} requested the main channel")
    except TelegramError as e:
        logger.error(f"Error in mainchannel command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    try:
        message = await update.message.reply_text(LANGUAGES[lang]['guide'])
        logger.info(f"User {user_id} used /guide command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group
        await context.bot.send_message(LOG_CHANNEL_ID, f"📚 {update.effective_user.first_name} viewed the usage guide")
    except TelegramError as e:
        logger.error(f"Error in guide command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    try:
        if user_id not in ADMIN_USER_IDS:
            message = await update.message.reply_text(LANGUAGES[lang]['not_allowed'])
            logger.info(f"User {user_id} attempted /broadcast but is not an admin")
            asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

            # Log to group
            await context.bot.send_message(LOG_CHANNEL_ID, f"🚫 {update.effective_user.first_name} attempted to broadcast but is not an admin")
            return

        message = await update.message.reply_text(LANGUAGES[lang]['broadcast'])
        logger.info(f"User {user_id} used /broadcast command")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

        # Log to group (simulating broadcast)
        await context.bot.send_message(LOG_CHANNEL_ID, f"📢 {update.effective_user.first_name} initiated a broadcast: New season links available!")
    except TelegramError as e:
        logger.error(f"Error in broadcast command: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    text = update.message.text.lower()

    try:
        if text.startswith('season '):
            try:
                season_number = int(text.split(' ')[1])
                if 1 <= season_number <= 10:
                    await send_season_info(update, context, f"season_{season_number}")
                    user_states[user_id]['last_action'] = f"season_{season_number}"
                else:
                    message = await update.message.reply_text(LANGUAGES[lang]['invalid_season'])
                    asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
            except ValueError:
                message = await update.message.reply_text(LANGUAGES[lang]['invalid_season'])
                asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
        elif text == 'help':
            message = await update.message.reply_text(LANGUAGES[lang]['help'])
            logger.info(f"User {user_id} used /help command")
            asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

            # Log to group
            await context.bot.send_message(LOG_CHANNEL_ID, f"ℹ️ {update.effective_user.first_name} used /help")
        elif text == 'settings':
            message = await update.message.reply_text(LANGUAGES[lang]['settings'])
            asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
        else:
            message = await update.message.reply_text("Invalid selection.")
            asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))
    except TelegramError as e:
        logger.error(f"Error handling selection: {str(e)}")
        message = await update.message.reply_text("An error occurred. Please try again later.")
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = user_states[user_id]['language']

    try:
        if query.data.startswith('info_'):
            season_key = query.data.split('_', 1)[1]
            await send_season_info(update, context, season_key)
        elif query.data.startswith('resolve_'):
            season_key = query.data.split('_', 1)[1]
            season_info = season_data.get(season_key)
            if season_info:
                current_time = time.time()
                user_states[user_id]['season_access'][season_key]['resolved_time'] = current_time
                link = season_info["link"]
                season_name = f"Season {season_key.split('_')[1]}"
                message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"{season_name} Link: {link}"
                )
                asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

                # Log to group
                await context.bot.send_message(LOG_CHANNEL_ID, f"🔗 {update.effective_user.first_name} resolved link for {season_name}")
    except TelegramError as e:
        logger.error(f"Error handling button callback: {str(e)}")
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="An error occurred. Please try again later."
        )
        asyncio.create_task(schedule_message_deletion(context, update.effective_chat.id, message.message_id))

def handle_shutdown(application):
    logger.info("Shutting down bot...")
    application.stop_running()
    logger.info("Bot stopped.")

async def set_command_menu(application):
    commands = [
        BotCommand(command="start", description="Start the bot and see the menu"),
        BotCommand(command="clearhistory", description="Remove history"),
        BotCommand(command="owner", description="Show owner info"),
        BotCommand(command="mainchannel", description="Join our main channel"),
        BotCommand(command="guide", description="View usage guide"),
        BotCommand(command="broadcast", description="Send a message to all users (admin only)"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Custom command menu set successfully")
    except TelegramError as e:
        logger.error(f"Failed to set command menu: {str(e)}")

def main():
    try:
        if 'YOUR_BOT_TOKEN' in BOT_TOKEN:
            logger.critical("Invalid bot token. Please set a valid token.")
            sys.exit(1)

        if LOG_CHANNEL_ID == 0:
            logger.critical("Invalid log group ID. Please set a valid group ID.")
            sys.exit(1)

        if not ADMIN_USER_IDS or ADMIN_USER_IDS == ['']:
            logger.critical("Invalid admin user ID. Please set a valid admin ID.")
            sys.exit(1)

        app = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        app.add_handler(CommandHandler('start', start))
        app.add_handler(CommandHandler('clearhistory', clearhistory))
        app.add_handler(CommandHandler('owner', owner))
        app.add_handler(CommandHandler('mainchannel', mainchannel))
        app.add_handler(CommandHandler('guide', guide))
        app.add_handler(CommandHandler('broadcast', broadcast))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_selection))
        app.add_handler(CallbackQueryHandler(button))

        # Set up signal handlers for graceful shutdown
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda s, f: handle_shutdown(app))

        # Set command menu
        loop = asyncio.get_event_loop()
        loop.create_task(set_command_menu(app))

        logger.info("Starting bot...")
        loop.run_until_complete(app.run_polling())
    except Exception as e:
        logger.critical(f"Failed to start bot: {str(e)}")
        raise

if __name__ == '__main__':
    main()
