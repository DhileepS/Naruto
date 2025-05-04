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
import math

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '0')  # Default to '0' if not set
DB_CHANNEL_1 = os.getenv('DB_CHANNEL_1', '0')  # First database channel
DB_CHANNEL_2 = os.getenv('DB_CHANNEL_2', '0')  # Second database channel
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

# Convert DB_CHANNEL_1 to int and validate
try:
    DB_CHANNEL_1 = int(DB_CHANNEL_1)
except ValueError:
    DB_CHANNEL_1 = 0

# Convert DB_CHANNEL_2 to int and validate
try:
    DB_CHANNEL_2 = int(DB_CHANNEL_2)
except ValueError:
    DB_CHANNEL_2 = 0

# Validate DB channels (should be negative for channels)
IS_DB_ENABLED = (DB_CHANNEL_1 != 0 and DB_CHANNEL_1 < 0) or (DB_CHANNEL_2 != 0 and DB_CHANNEL_2 < 0)

# Initialize a Bot instance for sending logs to the group
log_bot = Bot(token=BOT_TOKEN)

# Global variable to store cover photo file ID
COVER_PHOTO_ID = None

# Maximum files per page for pagination
FILES_PER_PAGE = 10

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
    'broadcast': {},  # Temporary storage for broadcast creation
    'search_results': [],  # Store search results for pagination
    'search_page': 1,  # Current page for search results
    'search_query': None,  # Store the last search query
})

# Set to store all user IDs who have interacted with the bot
all_users = set()

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
        'guide': '✨ **Usage Guide** ✨\n1. 🌟 Click /start to see the season menu.\n2. 🎬 Select a season (e.g., Season 1).\n3. 🔗 Click "Link-Shortner" to get the season link.\n4. 📺 Use /episode <number> to get a specific episode (e.g., /episode 100).\n5. ✅ Resolve the link to access your file.\n6. ℹ️ Use /help for more commands!\n7. 🔍 Type any text to search for files (e.g., "naruto" to find all Naruto files).',
        'broadcast_start': 'Let’s create a broadcast message. Please send the text or image for the post.',
        'broadcast_add_button': 'Would you like to add a button to your post? Reply with "+" to add a button, or choose an action below.',
        'broadcast_button_text': 'Please enter the text for the button.',
        'broadcast_button_link': 'Please enter the link for the button (e.g., https://example.com).',
        'broadcast_options': 'What would you like to do next?',
        'broadcast_sent': 'Broadcast message sent to all users! 📢',
        'not_allowed': 'You are not allowed to use this command. 🚫 Only admins can use this.',
        'file_not_found': 'No files found for your search: {query}. Try a different keyword.',
        'file_search_error': 'Error searching for files. Please try again later.',
        'file_sent': 'File found: {file_name}\nLink: {short_url}',
        'multiple_files_found': 'Found {count} files matching "{query}":\n\n{file_list}\n\nUse the buttons to navigate pages or refine your search for more specific results.',
        'page_indicator': 'Page {current}/{total}',
        'db_not_configured': 'File search is not enabled. Please contact the bot owner.',
        'cover_set': 'Cover photo updated successfully! 📷',
        'cover_prompt': 'Please send the photo to set as the cover.',
        'cover_invalid': 'Please send a valid photo.'
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

# Helper function to create the common inline keyboard for links
def create_link_keyboard():
    keyboard = [
        [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")],
        [InlineKeyboardButton("CE Sub", url="https://t.me/ce_sub_placeholder")],  # Replace with actual URL
        [InlineKeyboardButton("Try Again", url="https://t.me/bot_paiyan_official")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Helper function to create the broadcast options keyboard
def create_broadcast_options_keyboard():
    keyboard = [
        [InlineKeyboardButton("Preview", callback_data="broadcast_preview")],
        [InlineKeyboardButton("Send to All Users", callback_data="broadcast_send")],
        [InlineKeyboardButton("Continue", callback_data="broadcast_continue")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Helper function to create pagination keyboard
def create_pagination_keyboard(current_page: int, total_pages: int):
    keyboard = []
    if total_pages > 1:
        buttons = []
        if current_page > 1:
            buttons.append(InlineKeyboardButton("Previous Page", callback_data="prev_page"))
        if current_page < total_pages:
            buttons.append(InlineKeyboardButton("Next Page", callback_data="next_page"))
        if buttons:
            keyboard.append(buttons)
        keyboard.append([InlineKeyboardButton(f"Page {current_page}/{total_pages}", callback_data="noop")])
    return InlineKeyboardMarkup(keyboard)

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

# Function to search for files in the database channels
async def search_file_in_channel(context: ContextTypes.DEFAULT_TYPE, query: str) -> list:
    """
    Search for files in DB_CHANNEL_1 and DB_CHANNEL_2 that match the query.
    Returns a list of dicts with file_id and file_name for all matching files.
    """
    matching_files = []
    channels = [DB_CHANNEL_1, DB_CHANNEL_2] if IS_DB_ENABLED else []
    for channel_id in channels:
        if channel_id == 0 or channel_id >= 0:
            continue
        try:
            # Fetch up to 500 recent messages from the channel
            async for message in context.bot.get_chat_history(chat_id=channel_id, limit=500):
                if message.document or message.video or message.audio or message.photo:
                    file_name = None
                    if message.document:
                        file_name = message.document.file_name
                    elif message.video:
                        file_name = message.caption or "video_file"
                    elif message.audio:
                        file_name = message.audio.file_name or "audio_file"
                    elif message.photo:
                        file_name = message.caption or "photo_file"

                    # Check if the query matches the file name or caption
                    if file_name and query.lower() in file_name.lower():
                        file_id = (message.document.file_id if message.document else
                                  message.video.file_id if message.video else
                                  message.audio.file_id if message.audio else
                                  message.photo[-1].file_id)
                        matching_files.append({'file_id': file_id, 'file_name': file_name})
        except TelegramError as e:
            logger.error(f"Error searching channel {channel_id}: {str(e)}")
    return matching_files

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

    # Add user to all_users set
    all_users.add(user_id)

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
        
        # Use the common link keyboard
        reply_markup = create_link_keyboard()

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

# Handler for setting cover photo
async def cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Add user to all_users set
    all_users.add(user_id)

    # Check if the user is an admin
    if user_id not in ADMIN_USER_IDS:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['not_allowed']
        )
        logger.info(f"User {user_id} attempted /cover but is not an admin")
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"🚫 {update.effective_user.first_name} attempted /cover but is not an admin"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (cover permission denied): {str(e)}")
        return

    # Prompt for photo
    user_states[user_id]['awaiting_cover'] = True
    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['cover_prompt']
    )
    logger.info(f"User {user_id} initiated /cover command")

async def handle_cover_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Check if awaiting cover photo
    if not user_states[user_id].get('awaiting_cover', False):
        return

    # Handle photo
    if update.message.photo:
        global COVER_PHOTO_ID
        COVER_PHOTO_ID = update.message.photo[-1].file_id  # Get the highest quality photo
        user_states[user_id]['awaiting_cover'] = False
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['cover_set']
        )
        logger.info(f"User {user_id} set new cover photo: {COVER_PHOTO_ID}")

        # Log to group
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"📷 {update.effective_user.first_name} updated the cover photo"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (cover set): {str(e)}")
    else:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['cover_invalid']
        )

async def send_season_info(update: Update, context: ContextTypes.DEFAULT_TYPE, season_key: str):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Add user to all_users set
    all_users.add(user_id)

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

    # Add user to all_users set
    all_users.add(user_id)

    # Check if the /start command has a parameter
    start_param = context.args[0] if context.args else None

    if start_param:
        if start_param.startswith('season'):
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
        elif start_param.startswith('file_'):
            file_id = start_param.split('file_')[1]
            try:
                # Send cover photo if set
                if COVER_PHOTO_ID:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=COVER_PHOTO_ID,
                        caption="Cover Photo"
                    )
                # Send the file
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    reply_markup=create_link_keyboard()
                )
                logger.info(f"User {user_id} accessed file with ID {file_id}")

                # Log to group
                if IS_LOGGING_ENABLED:
                    try:
                        await context.bot.send_message(
                            LOG_CHANNEL_ID,
                            f"📎 {update.effective_user.first_name} accessed file via start link"
                        )
                    except TelegramError as e:
                        logger.error(f"Failed to log to group (file access): {str(e)}")
            except TelegramError as e:
                logger.error(f"Error sending file {file_id}: {str(e)}")
                await send_message_with_auto_delete(
                    context,
                    chat_id,
                    "Failed to send the file. It may have been deleted or is invalid."
                )
            return
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

    # Add user to all_users set
    all_users.add(user_id)

    user_states[user_id] = {
        'language': 'en',
        'last_action': None,
        'last_season': None,
        'season_access': {},
        'broadcast': {},
        'search_results': [],
        'search_page': 1,
        'search_query': None
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

    # Add user to all_users set
    all_users.add(user_id)

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

    # Add user to all_users set
    all_users.add(user_id)

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

    # Add user to all_users set
    all_users.add(user_id)

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

# Broadcast command handler
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Add user to all_users set
    all_users.add(user_id)

    # Check if the user is an admin
    if user_id not in ADMIN_USER_IDS:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['not_allowed']
        )
        logger.info(f"User {user_id} attempted /broadcast but is not an admin")
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"🚫 {update.effective_user.first_name} attempted to broadcast but is not an admin"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (broadcast permission denied): {str(e)}")
        return

    # Initialize broadcast state
    user_states[user_id]['broadcast'] = {
        'stage': 'content',
        'content': None,
        'is_image': False,
        'buttons': []
    }

    # Prompt for broadcast content
    await send_message_with_auto_delete(
        context,
        chat_id,
        LANGUAGES[lang]['broadcast_start']
    )
    logger.info(f"User {user_id} started broadcast creation")

# Handler for broadcast creation steps
async def handle_broadcast_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Add user to all_users set
    all_users.add(user_id)

    broadcast_state = user_states[user_id].get('broadcast', {})
    if not broadcast_state:
        return  # Not in broadcast creation mode

    stage = broadcast_state.get('stage')

    if stage == 'content':
        # Handle text or image content
        if update.message.text:
            broadcast_state['content'] = update.message.text
            broadcast_state['is_image'] = False
        elif update.message.photo:
            broadcast_state['content'] = update.message.photo[-1].file_id  # Get the highest quality photo
            broadcast_state['is_image'] = True
        else:
            await send_message_with_auto_delete(
                context,
                chat_id,
                "Please send text or an image for the broadcast."
            )
            return

        # Move to button stage
        broadcast_state['stage'] = 'add_button'
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_add_button'],
            reply_markup=create_broadcast_options_keyboard()
        )

    elif stage == 'button_text':
        # Store the button text
        broadcast_state['current_button'] = {'text': update.message.text}
        broadcast_state['stage'] = 'button_link'
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_button_link']
        )

    elif stage == 'button_link':
        # Store the button link
        button = broadcast_state.get('current_button', {})
        button['url'] = update.message.text
        broadcast_state['buttons'].append([InlineKeyboardButton(button['text'], url=button['url'])])
        del broadcast_state['current_button']
        broadcast_state['stage'] = 'add_button'
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_add_button'],
            reply_markup=create_broadcast_options_keyboard()
        )

# Handler for broadcast options (Preview, Send, Continue)
async def broadcast_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    broadcast_state = user_states[user_id].get('broadcast', {})
    if not broadcast_state:
        return

    action = query.data

    # Prepare the post
    content = broadcast_state.get('content')
    is_image = broadcast_state.get('is_image', False)
    buttons = broadcast_state.get('buttons', [])
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    if action == 'broadcast_preview':
        # Show preview
        if is_image:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=content,
                reply_markup=reply_markup
            )
        else:
            await send_message_with_auto_delete(
                context,
                chat_id,
                content,
                reply_markup=reply_markup
            )
        # Show options again
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_options'],
            reply_markup=create_broadcast_options_keyboard()
        )

    elif action == 'broadcast_send':
        # Send to all users
        for target_user_id in all_users:
            try:
                if is_image:
                    await context.bot.send_photo(
                        chat_id=target_user_id,
                        photo=content,
                        reply_markup=reply_markup
                    )
                else:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=content,
                        reply_markup=reply_markup
                    )
            except TelegramError as e:
                logger.error(f"Failed to send broadcast to user {target_user_id}: {str(e)}")

        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_sent']
        )

        # Log to group
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"📢 {update.effective_user.first_name} sent a broadcast to {len(all_users)} users"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (broadcast sent): {str(e)}")

        # Clear broadcast state
        user_states[user_id]['broadcast'] = {}

    elif action == 'broadcast_continue':
        # Continue adding buttons
        broadcast_state['stage'] = 'button_text'
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_button_text']
        )

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    text = update.message.text.lower().strip()

    # Add user to all_users set
    all_users.add(user_id)

    # Check if in broadcast creation mode and handle button addition
    broadcast_state = user_states[user_id].get('broadcast', {})
    if broadcast_state and broadcast_state.get('stage') == 'add_button' and text == '+':
        broadcast_state['stage'] = 'button_text'
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['broadcast_button_text']
        )
        return

    # Handle season selection
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
        return

    # Handle help and settings
    if text == 'help':
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['help']
        )
        logger.info(f"User {user_id} used /help command")
        if IS_LOGGING_ENABLED:
            try:
                await context.bot.send_message(
                    LOG_CHANNEL_ID,
                    f"ℹ️ {update.effective_user.first_name} used /help"
                )
            except TelegramError as e:
                logger.error(f"Failed to log to group (help command): {str(e)}")
        return
    elif text == 'settings':
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['settings']
        )
        return

    # Treat any other text as a search query
    if not IS_DB_ENABLED:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['db_not_configured']
        )
        logger.error("DB_CHANNEL_1 or DB_CHANNEL_2 is not configured properly")
        return

    # Search for files in DB_CHANNEL_1 and DB_CHANNEL_2
    file_infos = await search_file_in_channel(context, text)
    if not file_infos:
        await send_message_with_auto_delete(
            context,
            chat_id,
            LANGUAGES[lang]['file_not_found'].format(query=text)
        )
        logger.info(f"No files found for query '{text}' by user {user_id}")
        return

    # Store search results and query in user state
    user_states[user_id]['search_results'] = file_infos
    user_states[user_id]['search_query'] = text
    user_states[user_id]['search_page'] = 1

    # Display the first page of results
    await display_search_results(update, context, page=1)

async def display_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    file_infos = user_states[user_id]['search_results']
    query = user_states[user_id]['search_query']
    total_files = len(file_infos)
    total_pages = math.ceil(total_files / FILES_PER_PAGE)

    if page < 1 or page > total_pages:
        logger.error(f"Invalid page number {page} for user {user_id}")
        return

    # Update current page
    user_states[user_id]['search_page'] = page

    # Get files for the current page
    start_idx = (page - 1) * FILES_PER_PAGE
    end_idx = min(start_idx + FILES_PER_PAGE, total_files)
    page_files = file_infos[start_idx:end_idx]

    # Generate file list with shortened links
    file_list = []
    for file_info in page_files:
        file_id = file_info['file_id']
        file_name = file_info['file_name']
        start_link = f"https://t.me/Naruto_multilangbot?start=file_{file_id}"
        short_url = await shorten_url(start_link, f"file_{file_id}")
        file_list.append(f"- {file_name}: {short_url}")

    file_list_text = "\n".join(file_list)
    message_text = LANGUAGES[lang]['multiple_files_found'].format(
        count=total_files,
        query=query,
        file_list=file_list_text
    )

    # Create pagination keyboard
    reply_markup = create_pagination_keyboard(page, total_pages)

    # Send the results
    await send_message_with_auto_delete(
        context,
        chat_id,
        message_text,
        reply_markup=reply_markup
    )

    logger.info(f"User {user_id} viewed page {page}/{total_pages} for query '{query}' with {total_files} results")

    # Log to group
    if IS_LOGGING_ENABLED:
        try:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"🔍 {update.effective_user.first_name} searched for '{query}' and viewed page {page}/{total_pages} ({total_files} files)"
            )
        except TelegramError as e:
            logger.error(f"Failed to log to group (file search): {str(e)}")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    # Add user to all_users set
    all_users.add(user_id)

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
                reply_markup = create_link_keyboard()
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
        elif query.data.startswith('broadcast_'):
            await broadcast_options(update, context)
        elif query.data == 'prev_page':
            current_page = user_states[user_id]['search_page']
            if current_page > 1:
                await display_search_results(update, context, page=current_page - 1)
        elif query.data == 'next_page':
            current_page = user_states[user_id]['search_page']
            total_files = len(user_states[user_id]['search_results'])
            total_pages = math.ceil(total_files / FILES_PER_PAGE)
            if current_page < total_pages:
                await display_search_results(update, context, page=current_page + 1)
        elif query.data == 'noop':
            pass  # No-op for page indicator button
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

        if not IS_DB_ENABLED:
            print("Invalid database channel IDs. Please set at least one valid DB_CHANNEL_1 or DB_CHANNEL_2.")
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
        app.add_handler(CommandHandler('cover', cover))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_selection))
        app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_cover_photo))
        app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, handle_broadcast_creation))
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
