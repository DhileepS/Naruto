import logging
import asyncio
import sys
import os
from urllib.parse import urlencode
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
from aiohttp import web, ClientSession

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '0')  # Default to '0' if not set
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '').split(',')  # Default to empty list if not set
GPLINK_API = os.getenv('GPLINK_API', 'YOUR_GPLINK_API')  # gplinks.co API token
PORT = int(os.getenv('PORT', 10000))  # Render assigns PORT, default to 10000

# Convert LOG_CHANNEL_ID to int and validate
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    LOG_CHANNEL_ID = 0

# Validate LOG_CHANNEL_ID (should be negative for groups)
IS_LOGGING_ENABLED = LOG_CHANNEL_ID != 0 and LOG_CHANNEL_ID < 0

# Initialize a Bot instance for sending logs to the group
log_bot = Bot(token=BOT_TOKEN)

# Custom logging handler to send logs to Telegram group
class TelegramGroupHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_queue = []  # Queue to store logs until the event loop is running
        self.loop = None
        self.bot_initialized = False

    def set_loop(self, loop):
        self.loop = loop
        self.bot_initialized = True
        # Process any queued logs now that the loop is set
        if self.log_queue:
            for log_entry in self.log_queue:
                asyncio.run_coroutine_threadsafe(self.send_log_to_group(log_entry), self.loop)
            self.log_queue.clear()

    def emit(self, record):
        log_entry = self.format(record)
        if not IS_LOGGING_ENABLED:
            print(f"Logging to Telegram group disabled (LOG_CHANNEL_ID: {LOG_CHANNEL_ID})")
            print(f"Log: {log_entry}")
            return
        if not self.bot_initialized or self.loop is None:
            # If the loop isn't set, queue the log message
            self.log_queue.append(log_entry)
            print(f"Queued log: {log_entry}")  # Fallback to console
        else:
            # If the loop is set, send the log to Telegram
            asyncio.run_coroutine_threadsafe(self.send_log_to_group(log_entry), self.loop)

    async def send_log_to_group(self, log_entry):
        try:
            await log_bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"**Log Entry:**\n{log_entry}")
        except TelegramError as e:
            print(f"Error sending log to Telegram group: {e}")
            print(f"Failed log entry: {log_entry}")

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

# Auto-delete duration (1 hour in seconds)
AUTO_DELETE_DURATION = 60 * 60

# Language support
LANGUAGES = {
    'en': {
        'welcome': 'Choose an option:',
        'invalid_season': 'Invalid season selection.',
        'season_not_found': 'Season not found.',
        'episode_not_found': 'Episode not found.',
        'invalid_episode': 'Invalid episode number. Please use /episode <number> (e.g., /episode 100).',
        'help': 'Available commands:\n/start - Start the bot and see the menu\n/episode <number> - Get a specific episode link\n/clearhistory - Remove history\n/owner - Show owner info\n/mainchannel - Join our main channel\n/guide - View usage guide\n/broadcast - Send a message to all users (admin only)\n/getchatid - Get the chat ID of this group (for debugging)',
        'settings': 'Settings are not yet implemented in this version.',
        'status': 'Bot is running normally.',
        'status_error': 'Bot is experiencing issues. Please try again later.',
        'clearhistory': 'Your history has been cleared! 🗑️',
        'owner': 'Owner: @Dhileep_S 👨‍💼',
        'mainchannel': 'Join our main channel: @bot_paiyan_official 📢',
        'guide': '✨ **Usage Guide** ✨\n1. 🌟 Click /start to see the season menu.\n2. 🎬 Select a season (e.g., Season 1).\n3. 🔗 Click "Link-Shortner" to get the season link.\n4. 📺 Use /episode <number> to get a specific episode (e.g., /episode 100).\n5. ✅ Resolve the link to access your file.\n6. ℹ️ Use /help for more commands!',
        'broadcast': 'Broadcast message sent (logged to group). 📢',
        'not_allowed': 'You are not allowed to use this command. 🚫 Only admins can broadcast.'
    }
}

# Generate season data dynamically
def generate_season_data():
    season_data = {}
    episodes_per_season = 25
    total_episodes = 220
    num_seasons = (total_episodes + episodes_per_season - 1) // episodes_per_season  # Ceiling division

    for season_num in range(1, num_seasons + 1):
        season_key = f"season_{season_num}"
        start_episode = (season_num - 1) * episodes_per_season + 1
        end_episode = min(season_num * episodes_per_season, total_episodes)
        
        episodes = {}
        for ep_num in range(start_episode, end_episode + 1):
            ep_key = f"Episode {ep_num}"
            episodes[ep_num] = f"https://example.com/season{season_num}/episode{ep_num}"

        season_data[season_key] = {
            "start_id_ref": f"https://t.me/Naruto_multilangbot?start=season{season_num}",
            "episodes": episodes
        }

    # Add Season 10 as a placeholder (no episodes)
    season_data["season_10"] = {
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season10",
        "episodes": {}
    }

    return season_data

# Season data
season_data = generate_season_data()

# Helper function to send messages and schedule deletion
async def send_message_with_auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    try:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message
    except TelegramError as e:
        logger.error(f"Error sending message: {str(e)}")
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="An error occurred. Please try again later."
        )
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message

# Helper function to schedule message deletion
async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(AUTO_DELETE_DURATION)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id} in chat {chat_id}")
    except TelegramError as e:
        logger.error(f"Failed to delete message {message_id}: {str(e)}")

# Function to generate a shortlink using gplinks.co API
async def shorten_url(long_url: str, identifier: str) -> str:
    alias = f"{identifier}_{int(time.time())}"  # Unique alias with timestamp
    api_url = "https://api.gplinks.com/api"
    params = {
        "api": GPLINK_API,
        "url": long_url,
        "alias": alias,
        "format": "text"
    }
    query_string = urlencode(params)
    full_url = f"{api_url}?{query_string}"
    
    try:
        async with ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    short_url = await response.text()
                    logger.info(f"Shortened URL: {long_url} -> {short_url}")
                    return short_url.strip()
                else:
                    logger.error(f"Failed to shorten URL: HTTP {response.status}")
                    return long_url
    except Exception as e:
        logger.error(f"Error shortening URL: {str(e)}")
        return long_url

# Function to find an episode by number
def find_episode(episode_number: int):
    for season_key, season_info in season_data.items():
        episodes = season_info["episodes"]
        if episode_number in episodes:
            season_num = int(season_key.split('_')[1])
            return season_key, season_num, episodes[episode_number]
    return None, None, None

# Debug command to get the chat ID
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Chat ID requested: {chat_id}")
    await send_message_with_auto_delete(
        context,
        chat_id,
        f"This chat's ID is: {chat_id}"
    )

# Handler for episode requests
async def episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Check if an episode number was provided
    if not context.args:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['invalid_episode']
        )
        return

    try:
        episode_number = int(context.args[0])
        if episode_number < 1 or episode_number > 220:
            await send_message_with_auto_delete(
                context,
                chat_id,
                LANGUAGES[lang]['invalid_episode']
            )
            return

        # Find the episode in season_data
        season_key, season_num, episode_url = find_episode(episode_number)
        if not episode_url:
            await send_message_with_auto_delete(
                context,
                chat_id,
                LANGUAGES[lang]['episode_not_found']
            )
            return

        # Shorten the episode URL
        short_url = await shorten_url(episode_url, f"episode{episode_number}")
        
        # Create the "How to Resolve" button
        keyboard = [
            [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send the episode link
        await send_message_with_auto_delete(
            context,
            chat_id,
            f"Episode {episode_number} (Season {season_num}) Link: {short_url}",
            reply_markup=reply_markup
        )

        logger.info(f"User {user_id} requested Episode {episode_number}: {short_url}")

        # Log to group
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"📺 {update.effective_user.first_name} requested Episode {episode_number} (Season {season_num})"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (episode request): {str(e)}")

    except ValueError:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['invalid_episode']
        )
    except TelegramError as e:
        logger.error(f"Error in episode command: {str(e)}")
        await send_message_with_auto_delete(
            context,
            chat_id,
            "An error occurred. Please try again later."
        )

async def send_season_info(update: Update, context: ContextTypes.DEFAULT_TYPE, season_key: str):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    season_info = season_data.get(season_key)
    if not season_info:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['season_not_found']
        )
        return

    start_id_reference = season_info["start_id_ref"]
    season_name = f"Season {season_key.split('_')[1]}"
    logger.info(f"User {user_id} accessed {season_key}: {start_id_reference}")

    season_access = user_states[user_id]['season_access'].get(season_key, {})
    current_time = time.time()

    # Set first_access_time for logging purposes
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
    reply_markup = InlineKeyboardMarkup(keyboard)

    await send_message_with_auto_delete(
        context,
        chat_id,
        f"Accessing {season_name}:",
        reply_markup=reply_markup
    )
    user_states[user_id]['last_season'] = season_key

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📥 {update.effective_user.first_name} accessed {season_name}"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (season info): {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Check if the /start command has a parameter (e.g., /start season1)
    start_param = context.args[0] if context.args else None

    # If a start parameter is provided (e.g., season1, season2, etc.)
    if start_param and start_param.startswith('season'):
        season_key = f"season_{start_param.split('season')[1]}"
        if season_key in season_data:
            await send_season_info(update, context, season_key)
            logger.info(f"User {user_id} used /start with parameter {start_param}")
        else:
            await send_message_with_auto_delete(
                context,
                chat_id,
                LANGUAGES[lang]['season_not_found']
            )
            logger.info(f"User {user_id} used /start with invalid parameter {start_param}")
    else:
        # Default behavior: Show the season selection menu
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
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['welcome'],
            reply_markup=reply_markup
        )
        logger.info(f"User {user_id} used /start command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"🚀 {update.effective_user.first_name} started the bot"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (start command): {str(e)}")

async def clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    user_states[user_id] = {
        'language': 'en',
        'last_action': None,
        'last_season': None,
        'season_access': {},
    }
    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['clearhistory']
    )
    logger.info(f"User {user_id} used /clearhistory command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"🗑️ {update.effective_user.first_name} cleared their history"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (clearhistory command): {str(e)}")

async def owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['owner']
    )
    logger.info(f"User {user_id} used /owner command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"👨‍💼 {update.effective_user.first_name} requested owner info"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (owner command): {str(e)}")

async def mainchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['mainchannel']
    )
    logger.info(f"User {user_id} used /mainchannel command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📢 {update.effective_user.first_name} requested the main channel"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (mainchannel command): {str(e)}")

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['guide']
    )
    logger.info(f"User {user_id} used /guide command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📚 {update.effective_user.first_name} viewed the usage guide"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (guide command): {str(e)}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    if user_id not in ADMIN_USER_IDS:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['not_allowed']
        )
        logger.info(f"User {user_id} attempted /broadcast but is not an admin")

        # Log to group
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"🚫 {update.effective_user.first_name} attempted to broadcast but is not an admin"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (broadcast permission denied): {str(e)}")
        return

    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['broadcast']
    )
    logger.info(f"User {user_id} used /broadcast command")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📢 {update.effective_user.first_name} initiated a broadcast: New season links available!"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (broadcast command): {str(e)}")

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    text = update.message.text.lower()

    if text.startswith('season '):
        try:
            season_number = int(text.split(' ')[1])
            if 1 <= season_number <= 10:
                await send_season_info(update, context, f"season_{season_number}")
                user_states[user_id]['last_action'] = f"season_{season_number}"
            else:
                await send_message_with_auto_delete(
                    context,
                    chat_id,
                    LANGUAGES[lang]['invalid_season']
                )
        except ValueError:
            await send_message_with_auto_delete(
                context,
                chat_id,
                LANGUAGES[lang]['invalid_season']
            )
    elif text == 'help':
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['help']
        )
        logger.info(f"User {user_id} used /help command")

        # Log to group
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"ℹ️ {update.effective_user.first_name} used /help"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (help command): {str(e)}")
    elif text == 'settings':
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['settings']
        )
    else:
        await send_message_with_auto_delete(
            context,
            chat_id,
            "Invalid selection."
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

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
                long_url = season_info["start_id_ref"]
                short_url = await shorten_url(long_url, season_key)
                season_name = f"Season {season_key.split('_')[1]}"
                keyboard = [
                    [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await send_message_with_auto_delete(
                    context,
                    chat_id,
                    f"{season_name} Link: {short_url}",
                    reply_markup=reply_markup
                )

                # Log to group
                if IS_LOGGING_ENABLED:
                    try:
                        await context.bot.send_message(
                            LOG_CHANNEL_ID,
                            f"🔗 {update.effective_user.first_name} resolved link for {season_name}"
                        )
                    except TelegramError as e:
                        logger.error(f"Failed to log to group (resolve link): {str(e)}")
    except TelegramError as e:
        logger.error(f"Error handling button callback: {str(e)}")
        await send_message_with_auto_delete(
            context,
            chat_id,
            "An error occurred. Please try again later."
        )

async def set_command_menu(application):
    commands = [
        BotCommand(command="start", description="Start the bot and see the menu"),
        BotCommand(command="episode", description="Get a specific episode link (e.g., /episode 100)"),
        BotCommand(command="clearhistory", description="Remove history"),
        BotCommand(command="owner", description="Show owner info"),
        BotCommand(command="mainchannel", description="Join our main channel"),
        BotCommand(command="guide", description="View usage guide"),
        BotCommand(command="broadcast", description="Send a message to all users (admin only)"),
        BotCommand(command="getchatid", description="Get the chat ID of this group (for debugging)"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Custom command menu set successfully")
    except TelegramError as e:
        logger.error(f"Failed to set command menu: {str(e)}")

# Web server handler for keep-alive pings
async def health_check(request):
    logger.info("Received health check request")
    return web.Response(text="Bot is alive", status=200)

# Set up the web server
async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")
    return runner

async def main():
    try:
        # Validate environment variables
        if 'YOUR_BOT_TOKEN' in BOT_TOKEN:
            print("Invalid bot token. Please set a valid token.")
            sys.exit(1)

        if LOG_CHANNEL_ID == 0:
            print("Invalid log group ID. Please set a valid group ID.")
            sys.exit(1)

        if not ADMIN_USER_IDS or ADMIN_USER_IDS == ['']:
            print("Invalid admin user ID. Please set a valid admin ID.")
            sys.exit(1)

        if 'YOUR_GPLINK_API' in GPLINK_API:
            print("Invalid gplinks.co API token. Please set a valid token in GPLINK_API.")
            sys.exit(1)

        # Initialize the Telegram bot
        app = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        app.add_handler(CommandHandler('start', start))
        app.add_handler(CommandHandler('episode', episode))
        app.add_handler(CommandHandler('clearhistory', clearhistory))
        app.add_handler(CommandHandler('owner', owner))
        app.add_handler(CommandHandler('mainchannel', mainchannel))
        app.add_handler(CommandHandler('guide', guide))
        app.add_handler(CommandHandler('broadcast', broadcast))
        app.add_handler(CommandHandler('getchatid', get_chat_id))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_selection))
        app.add_handler(CallbackQueryHandler(button))

        # Set command menu
        await set_command_menu(app)

        # Set the loop in the logging handler
        telegram_handler.set_loop(asyncio.get_event_loop())

        # Start the web server
        web_runner = await start_web_server()

        # Log that the bot is starting
        print("Starting bot...")
        logger.info("Starting bot...")

        # Start polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Keep the application running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour, then loop

    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")
        raise
    finally:
        # Cleanup on shutdown
        if 'app' in locals():
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        if 'web_runner' in locals():
            await web_runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        sys.exit(1)
