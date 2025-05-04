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
    JobQueue,
)
from telegram.error import TelegramError, Conflict
from collections import defaultdict
import time
from aiohttp import web, ClientSession
import math
import atexit

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '0')
DB_CHANNEL_1 = os.getenv('DB_CHANNEL_1', '0')
DB_CHANNEL_2 = os.getenv('DB_CHANNEL_2', '0')
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '').split(',')
GPLINK_API = os.getenv('GPLINK_API', 'YOUR_GPLINK_API')
PORT = int(os.getenv('PORT', 10000))
UPDATES_CHANNEL = '@bot_paiyan_official'

# Validate environment variables
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    LOG_CHANNEL_ID = 0
IS_LOGGING_ENABLED = LOG_CHANNEL_ID != 0 and LOG_CHANNEL_ID < 0

try:
    DB_CHANNEL_1 = int(DB_CHANNEL_1)
    DB_CHANNEL_2 = int(DB_CHANNEL_2)
except ValueError:
    DB_CHANNEL_1 = 0
    DB_CHANNEL_2 = 0
IS_DB_ENABLED = (DB_CHANNEL_1 != 0 and DB_CHANNEL_1 < 0) or (DB_CHANNEL_2 != 0 and DB_CHANNEL_2 < 0)

# Initialize bot for logging
log_bot = Bot(token=BOT_TOKEN)

# Global variables
COVER_PHOTO_ID = None  # Store cover photo file ID
FILES_PER_PAGE = 10  # Files per page for pagination
AUTO_DELETE_DURATION = 60 * 60  # 1 hour in seconds
USER_STATS_INTERVAL = 600  # 10 minutes in seconds

# User state management
user_states = defaultdict(lambda: {
    'language': 'en',
    'last_action': None,
    'last_season': None,
    'season_access': {},
    'broadcast': {},
    'search_results': [],
    'search_page': 1,
    'search_query': None,
})

# Track all users and new users
all_users = set()
new_users = set()

# Lock file to prevent multiple instances
LOCK_FILE = "/tmp/telegram_bot.lock"

# Custom logging handler for Telegram group
class TelegramGroupHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_queue = []
        self.loop = None
        self.bot_initialized = False

    def set_loop(self, loop):
        self.loop = loop
        self.bot_initialized = True
        if self.log_queue:
            for log_entry in self.log_queue:
                asyncio.run_coroutine_threadsafe(self.send_log_to_group(log_entry), self.loop)
            self.log_queue.clear()

    def emit(self, record):
        log_entry = self.format(record)
        if not IS_LOGGING_ENABLED:
            print(f"Logging disabled (LOG_CHANNEL_ID: {LOG_CHANNEL_ID})")
            print(f"Log: {log_entry}")
            return
        if not self.bot_initialized or self.loop is None:
            self.log_queue.append(log_entry)
            print(f"Queued log: {log_entry}")
        else:
            asyncio.run_coroutine_threadsafe(self.send_log_to_group(log_entry), self.loop)

    async def send_log_to_group(self, log_entry):
        try:
            await log_bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"**Log Entry:**\n{log_entry}")
        except TelegramError as e:
            print(f"Error sending log to group: {e}")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.handlers = []
telegram_handler = TelegramGroupHandler()
telegram_handler.setLevel(logging.INFO)
logger.addHandler(telegram_handler)

# Language support
LANGUAGES = {
    'en': {
        'welcome': 'Choose an option:',
        'invalid_season': 'Invalid season selection. 🚫',
        'season_not_found': 'Season not found. 😔',
        'episode_not_found': 'Episode not found. 😔',
        'invalid_episode': 'Invalid episode number. Use /episode <number> (e.g., /episode 100). 🚫',
        'help': 'Available commands:\n/start - Start the bot\n/episode <number> - Get episode link\n/clearhistory - Clear history\n/owner - Show owner info\n/mainchannel - Join main channel\n/guide - View guide\n/broadcast - Broadcast message (admin only)\n/getchatid - Get chat ID\n🔍 Type any text to search files (e.g., "naruto").',
        'settings': 'Settings not implemented yet. 🛠️',
        'clearhistory': 'History cleared! 🗑️',
        'owner': 'Owner: @Dhileep_S 👨‍💼',
        'mainchannel': f'Join our main channel: {UPDATES_CHANNEL} 📢',
        'guide': f'✨ **Usage Guide** ✨\n1. 🌟 Use /start to see seasons.\n2. 🎬 Select a season.\n3. 🔗 Click "Link-Shortner" for season link.\n4. 📺 Use /episode <number> (e.g., /episode 100).\n5. ✅ Resolve links to access files.\n6. 🔍 Search files by typing (e.g., "naruto").\n7. 📢 Join {UPDATES_CHANNEL} to use search!',
        'broadcast_start': 'Send the broadcast text or image. 📢',
        'broadcast_add_button': 'Add a button? Reply "+" or choose below.',
        'broadcast_button_text': 'Enter button text.',
        'broadcast_button_link': 'Enter button link (e.g., https://example.com).',
        'broadcast_options': 'What next?',
        'broadcast_sent': 'Broadcast sent to all users! 📢',
        'not_allowed': 'Command restricted to admins. 🚫',
        'file_not_found': 'No files found for "{query}" 😔. Try another keyword.',
        'file_search_error': 'Error searching files. Try again later. 😓',
        'multiple_files_found': 'Found {count} files for "{query}" 🎉:\n\n{file_list}\n\nNavigate pages or refine your search.',
        'page_indicator': 'Page {current}/{total}',
        'db_not_configured': 'File search not enabled. Contact the owner. 🚫',
        'cover_set': 'Cover photo updated! 📷',
        'cover_prompt': 'Send the photo for the cover.',
        'cover_invalid': 'Please send a valid photo. 🚫',
        'not_subscribed': f'You must join {UPDATES_CHANNEL} to use this bot. 📢\nJoin and try again: https://t.me/bot_paiyan_official',
        'stats_message': '📊 **User Stats**\nTotal Users: {total}\nNew Users (last 10 min): {new}',
        'job_queue_missing': '⚠️ User stats notifications disabled: JobQueue not available. Install python-telegram-bot[job-queue].'
    }
}

# Generate season data
def generate_season_data():
    season_data = {}
    episodes_per_season = 25
    total_episodes = 220
    num_seasons = (total_episodes + episodes_per_season - 1) // episodes_per_season

    for season_num in range(1, num_seasons + 1):
        season_key = f"season_{season_num}"
        start_episode = (season_num - 1) * episodes_per_season + 1
        end_episode = min(season_num * episodes_per_season, total_episodes)
        episodes = {ep_num: f"https://example.com/season{season_num}/episode{ep_num}" for ep_num in range(start_episode, end_episode + 1)}
        season_data[season_key] = {
            "start_id_ref": f"https://t.me/Naruto_multilangbot?start=season{season_num}",
            "episodes": episodes
        }
    season_data["season_10"] = {
        "start_id_ref": "https://t.me/Naruto_multilangbot?start=season10",
        "episodes": {}
    }
    return season_data

season_data = generate_season_data()

# Helper functions
def create_link_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")],
        [InlineKeyboardButton("CE Sub", url="https://t.me/ce_sub_placeholder")],
        [InlineKeyboardButton("Try Again", url="https://t.me/bot_paiyan_official")]
    ])

def create_broadcast_options_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Preview", callback_data="broadcast_preview")],
        [InlineKeyboardButton("Send to All Users", callback_data="broadcast_send")],
        [InlineKeyboardButton("Continue", callback_data="broadcast_continue")]
    ])

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

async def send_message_with_auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    try:
        message = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message
    except TelegramError as e:
        logger.error(f"Error sending message: {e}")
        message = await context.bot.send_message(chat_id=chat_id, text="An error occurred. Try again later.")
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(AUTO_DELETE_DURATION)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id} in chat {chat_id}")
    except TelegramError as e:
        logger.error(f"Failed to delete message {message_id}: {e}")

async def shorten_url(long_url: str, identifier: str) -> str:
    alias = f"{identifier}_{int(time.time())}"
    api_url = "https://api.gplinks.com/api"
    params = {"api": GPLINK_API, "url": long_url, "alias": alias, "format": "text"}
    query_string = urlencode(params)
    full_url = f"{api_url}?{query_string}"
    
    try:
        async with ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    short_url = await response.text()
                    logger.info(f"Shortened URL: {long_url} -> {short_url}")
                    return short_url.strip()
                logger.error(f"Failed to shorten URL: HTTP {response.status}")
                return long_url
    except Exception as e:
        logger.error(f"Error shortening URL: {e}")
        return long_url

def find_episode(episode_number: int):
    for season_key, season_info in season_data.items():
        episodes = season_info["episodes"]
        if episode_number in episodes:
            season_num = int(season_key.split('_')[1])
            return season_key, season_num, episodes[episode_number]
    return None, None, None

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int, lang: str) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=UPDATES_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"Error checking subscription for user {user_id}: {e}")
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['not_subscribed'])
        return False

async def search_file_in_channel(context: ContextTypes.DEFAULT_TYPE, query: str) -> list:
    matching_files = []
    channels = [DB_CHANNEL_1, DB_CHANNEL_2] if IS_DB_ENABLED else []
    for channel_id in channels:
        if channel_id == 0 or channel_id >= 0:
            continue
        try:
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
                    if file_name and query.lower() in file_name.lower():
                        file_id = (message.document.file_id if message.document else
                                  message.video.file_id if message.video else
                                  message.audio.file_id if message.audio else
                                  message.photo[-1].file_id)
                        matching_files.append({'file_id': file_id, 'file_name': file_name})
        except TelegramError as e:
            logger.error(f"Error searching channel {channel_id}: {e}")
    return matching_files

async def send_user_stats(context: ContextTypes.DEFAULT_TYPE):
    total_users = len(all_users)
    new_user_count = len(new_users)
    stats_message = LANGUAGES['en']['stats_message'].format(total=total_users, new=new_user_count)
    try:
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=stats_message)
        logger.info(f"Sent user stats: {total_users} total, {new_user_count} new")
        new_users.clear()  # Reset new users after reporting
    except TelegramError as e:
        logger.error(f"Error sending user stats: {e}")

# Command handlers
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Chat ID requested: {chat_id}")
    await send_message_with_auto_delete(context, chat_id, f"This chat's ID is: {chat_id}")

async def episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    if not context.args:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['invalid_episode'])
        return

    try:
        episode_number = int(context.args[0])
        if episode_number < 1 or episode_number > 220:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['invalid_episode'])
            return

        season_key, season_num, episode_url = find_episode(episode_number)
        if not episode_url:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['episode_not_found'])
            return

        short_url = await shorten_url(episode_url, f"episode{episode_number}")
        caption = f"Episode {episode_number} (Season {season_num}) Link: {short_url}\n" \
                  f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
                  f"Updates: {UPDATES_CHANNEL}"
        reply_markup = create_link_keyboard()
        if COVER_PHOTO_ID:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=COVER_PHOTO_ID,
                caption="Episode Cover 📷"
            )
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=reply_markup
        )
        asyncio.create_task(schedule_message_deletion(context, chat_id, (await context.bot.get_updates())[-1].message.message_id))
        logger.info(f"User {user_id} requested Episode {episode_number}")

        if IS_LOGGING_ENABLED:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📺 {update.effective_user.first_name} requested Episode {episode_number} (Season {season_num})"
            )
    except ValueError:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['invalid_episode'])
    except TelegramError as e:
        logger.error(f"Error in episode command: {e}")
        await send_message_with_auto_delete(context, chat_id, "An error occurred. Try again later.")

async def cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if user_id not in ADMIN_USER_IDS:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['not_allowed'])
        logger.info(f"User {user_id} attempted /cover but is not admin")
        if IS_LOGGING_ENABLED:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"🚫 {update.effective_user.first_name} attempted /cover"
            )
        return

    user_states[user_id]['awaiting_cover'] = True
    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['cover_prompt'])
    logger.info(f"User {user_id} initiated /cover")

async def handle_cover_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id

    if not user_states[user_id].get('awaiting_cover', False):
        return

    if update.message.photo:
        global COVER_PHOTO_ID
        COVER_PHOTO_ID = update.message.photo[-1].file_id
        user_states[user_id]['awaiting_cover'] = False
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['cover_set'])
        logger.info(f"User {user_id} set cover photo: {COVER_PHOTO_ID}")
        if IS_LOGGING_ENABLED:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📷 {update.effective_user.first_name} updated cover photo"
            )
    else:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['cover_invalid'])

async def send_season_info(update: Update, context: ContextTypes.DEFAULT_TYPE, season_key: str):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    season_info = season_data.get(season_key)
    if not season_info:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['season_not_found'])
        return

    start_id_reference = season_info["start_id_ref"]
    season_name = f"Season {season_key.split('_')[1]}"
    logger.info(f"User {user_id} accessed {season_key}")

    season_access = user_states[user_id]['season_access'].get(season_key, {})
    if not season_access.get('first_access_time'):
        user_states[user_id]['season_access'][season_key] = {
            'first_access_time': time.time(),
            'resolved_time': None
        }

    keyboard = [
        [InlineKeyboardButton(season_name, callback_data=f"info_{season_key}")],
        [InlineKeyboardButton("Link-Shortner", callback_data=f"resolve_{season_key}")],
        [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_message_with_auto_delete(context, chat_id, f"Accessing {season_name}:", reply_markup=reply_markup)
    user_states[user_id]['last_season'] = season_key

    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"📥 {update.effective_user.first_name} accessed {season_name}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    start_param = context.args[0] if context.args else None
    if start_param:
        if start_param.startswith('season'):
            season_key = f"season_{start_param.split('season')[1]}"
            if season_key in season_data:
                await send_season_info(update, context, season_key)
                logger.info(f"User {user_id} used /start with {start_param}")
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['season_not_found'])
        elif start_param.startswith('file_'):
            file_id = start_param.split('file_')[1]
            try:
                if COVER_PHOTO_ID:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=COVER_PHOTO_ID,
                        caption="Cover Photo 📷"
                    )
                start_link = f"https://t.me/Naruto_multilangbot?start=file_{file_id}"
                short_url = await shorten_url(start_link, f"file_{file_id}")
                caption = f"File Link: {short_url}\n" \
                          f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
                          f"Updates: {UPDATES_CHANNEL}"
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption,
                    reply_markup=create_link_keyboard()
                )
                logger.info(f"User {user_id} accessed file {file_id}")
                if IS_LOGGING_ENABLED:
                    await context.bot.send_message(
                        LOG_CHANNEL_ID,
                        f"📎 {update.effective_user.first_name} accessed file"
                    )
            except TelegramError as e:
                logger.error(f"Error sending file {file_id}: {e}")
                await send_message_with_auto_delete(context, chat_id, "Failed to send file. It may be invalid.")
            return
    else:
        keyboard = [
            ['Season 1'], ['Season 2'], ['Season 3'], ['Season 4'], ['Season 5'],
            ['Season 6'], ['Season 7'], ['Season 8'], ['Season 9'], ['Season 10'],
            ['Help'], ['Settings']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['welcome'], reply_markup=reply_markup)
        logger.info(f"User {user_id} used /start")

    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"🚀 {update.effective_user.first_name} started the bot")

async def clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

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
    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['clearhistory'])
    logger.info(f"User {user_id} cleared history")
    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"🗑️ {update.effective_user.first_name} cleared history")

async def owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['owner'])
    logger.info(f"User {user_id} used /owner")
    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"👨‍💼 {update.effective_user.first_name} requested owner info")

async def mainchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['mainchannel'])
    logger.info(f"User {user_id} used /mainchannel")
    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"📢 {update.effective_user.first_name} requested main channel")

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['guide'])
    logger.info(f"User {user_id} used /guide")
    if IS_LOGGING_ENABLED:
        await context.bot.send_message(LOG_CHANNEL_ID, f"📚 {update.effective_user.first_name} viewed guide")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if user_id not in ADMIN_USER_IDS:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['not_allowed'])
        logger.info(f"User {user_id} attempted /broadcast")
        if IS_LOGGING_ENABLED:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"🚫 {update.effective_user.first_name} attempted /broadcast"
            )
        return

    user_states[user_id]['broadcast'] = {'stage': 'content', 'content': None, 'is_image': False, 'buttons': []}
    await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_start'])
    logger.info(f"User {user_id} started broadcast")

async def handle_broadcast_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    broadcast_state = user_states[user_id].get('broadcast', {})
    if not broadcast_state:
        return

    stage = broadcast_state.get('stage')
    if stage == 'content':
        if update.message.text:
            broadcast_state['content'] = update.message.text
            broadcast_state['is_image'] = False
        elif update.message.photo:
            broadcast_state['content'] = update.message.photo[-1].file_id
            broadcast_state['is_image'] = True
        else:
            await send_message_with_auto_delete(context, chat_id, "Send text or image for broadcast.")
            return
        broadcast_state['stage'] = 'add_button'
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_add_button'], reply_markup=create_broadcast_options_keyboard())
    elif stage == 'button_text':
        broadcast_state['current_button'] = {'text': update.message.text}
        broadcast_state['stage'] = 'button_link'
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_button_link'])
    elif stage == 'button_link':
        button = broadcast_state.get('current_button', {})
        button['url'] = update.message.text
        broadcast_state['buttons'].append([InlineKeyboardButton(button['text'], url=button['url'])])
        del broadcast_state['current_button']
        broadcast_state['stage'] = 'add_button'
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_add_button'], reply_markup=create_broadcast_options_keyboard())

async def broadcast_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    broadcast_state = user_states[user_id].get('broadcast', {})
    if not broadcast_state:
        return

    content = broadcast_state.get('content')
    is_image = broadcast_state.get('is_image', False)
    buttons = broadcast_state.get('buttons', [])
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    if query.data == 'broadcast_preview':
        if is_image:
            await context.bot.send_photo(chat_id=chat_id, photo=content, reply_markup=reply_markup)
        else:
            await send_message_with_auto_delete(context, chat_id, content, reply_markup=reply_markup)
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_options'], reply_markup=create_broadcast_options_keyboard())
    elif query.data == 'broadcast_send':
        for target_user_id in all_users:
            try:
                if is_image:
                    await context.bot.send_photo(chat_id=target_user_id, photo=content, reply_markup=reply_markup)
                else:
                    await context.bot.send_message(chat_id=target_user_id, text=content, reply_markup=reply_markup)
            except TelegramError as e:
                logger.error(f"Failed to send broadcast to {target_user_id}: {e}")
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_sent'])
        if IS_LOGGING_ENABLED:
            await context.bot.send_message(
                LOG_CHANNEL_ID,
                f"📢 {update.effective_user.first_name} sent broadcast to {len(all_users)} users"
            )
        user_states[user_id]['broadcast'] = {}
    elif query.data == 'broadcast_continue':
        broadcast_state['stage'] = 'button_text'
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_button_text'])

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    text = update.message.text.strip().lower()
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    broadcast_state = user_states[user_id].get('broadcast', {})
    if broadcast_state and broadcast_state.get('stage') == 'add_button' and text == '+':
        broadcast_state['stage'] = 'button_text'
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['broadcast_button_text'])
        return

    if text.startswith('season '):
        try:
            season_number = int(text.split(' ')[1])
            if 1 <= season_number <= 10:
                await send_season_info(update, context, f"season_{season_number}")
                user_states[user_id]['last_action'] = f"season_{season_number}"
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['invalid_season'])
        except ValueError:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['invalid_season'])
        return

    if text == 'help':
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['help'])
        logger.info(f"User {user_id} used /help")
        if IS_LOGGING_ENABLED:
            await context.bot.send_message(LOG_CHANNEL_ID, f"ℹ️ {update.effective_user.first_name} used /help")
        return
    elif text == 'settings':
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['settings'])
        return

    if not IS_DB_ENABLED:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['db_not_configured'])
        logger.error("DB channels not configured")
        return

    file_infos = await search_file_in_channel(context, text)
    if not file_infos:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES[lang]['file_not_found'].format(query=text))
        logger.info(f"No files found for query '{text}' by user {user_id}")
        return

    user_states[user_id]['search_results'] = file_infos
    user_states[user_id]['search_query'] = text
    user_states[user_id]['search_page'] = 1
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
        logger.error(f"Invalid page {page} for user {user_id}")
        return

    user_states[user_id]['search_page'] = page
    start_idx = (page - 1) * FILES_PER_PAGE
    end_idx = min(start_idx + FILES_PER_PAGE, total_files)
    page_files = file_infos[start_idx:end_idx]

    file_list = []
    for file_info in page_files:
        file_id = file_info['file_id']
        file_name = file_info['file_name']
        start_link = f"https://t.me/Naruto_multilangbot?start=file_{file_id}"
        short_url = await shorten_url(start_link, f"file_{file_id}")
        file_list.append(f"- {file_name}: {short_url}")

    file_list_text = "\n".join(file_list)
    message_text = LANGUAGES[lang]['multiple_files_found'].format(count=total_files, query=query, file_list=file_list_text)
    caption = f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
              f"Updates: {UPDATES_CHANNEL}"
    reply_markup = create_pagination_keyboard(page, total_pages)

    if COVER_PHOTO_ID:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=COVER_PHOTO_ID,
            caption="Search Results Cover 📷"
        )
    await send_message_with_auto_delete(context, chat_id, f"{message_text}\n\n{caption}", reply_markup=reply_markup)
    logger.info(f"User {user_id} viewed page {page}/{total_pages} for '{query}'")

    if IS_LOGGING_ENABLED:
        await context.bot.send_message(
            LOG_CHANNEL_ID,
            f"🔍 {update.effective_user.first_name} searched '{query}' (page {page}/{total_pages}, {total_files} files)"
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang = user_states[user_id]['language']
    chat_id = update.effective_chat.id
    all_users.add(user_id)
    new_users.add(user_id)

    if not await check_subscription(context, user_id, chat_id, lang):
        return

    try:
        if query.data.startswith('info_'):
            season_key = query.data.split('_', 1)[1]
            await send_season_info(update, context, season_key)
        elif query.data.startswith('resolve_'):
            season_key = query.data.split('_', 1)[1]
            season_info = season_data.get(season_key)
            if season_info:
                user_states[user_id]['season_access'][season_key]['resolved_time'] = time.time()
                long_url = season_info["start_id_ref"]
                short_url = await shorten_url(long_url, season_key)
                season_name = f"Season {season_key.split('_')[1]}"
                caption = f"{season_name} Link: {short_url}\n" \
                          f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
                          f"Updates: {UPDATES_CHANNEL}"
                reply_markup = create_link_keyboard()
                if COVER_PHOTO_ID:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=COVER_PHOTO_ID,
                        caption="Season Cover 📷"
                    )
                await send_message_with_auto_delete(
                    context,
                    chat_id,
                    caption,
                    reply_markup=reply_markup
                )
                if IS_LOGGING_ENABLED:
                    await context.bot.send_message(
                        LOG_CHANNEL_ID,
                        f"🔗 {update.effective_user.first_name} resolved {season_name}"
                    )
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
            pass
    except TelegramError as e:
        logger.error(f"Error handling button: {e}")
        await send_message_with_auto_delete(context, chat_id, "An error occurred. Try again later.")

async def set_command_menu(application):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("episode", "Get episode link (e.g., /episode 100)"),
        BotCommand("clearhistory", "Clear history"),
        BotCommand("owner", "Show owner info"),
        BotCommand("mainchannel", "Join main channel"),
        BotCommand("guide", "View guide"),
        BotCommand("broadcast", "Broadcast message (admin only)"),
        BotCommand("getchatid", "Get chat ID"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Command menu set")
    except TelegramError as e:
        logger.error(f"Failed to set command menu: {e}")

async def health_check(request):
    logger.info("Health check received")
    return web.Response(text="Bot is alive", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")
    return runner

def check_lock_file():
    if os.path.exists(LOCK_FILE):
        logger.error("Lock file exists. Another instance may be running.")
        sys.exit(1)
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_lock_file():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

atexit.register(remove_lock_file)

async def main():
    try:
        # Validate environment
        if 'YOUR_BOT_TOKEN' in BOT_TOKEN:
            logger.error("Invalid bot token")
            sys.exit(1)
        if LOG_CHANNEL_ID == 0:
            logger.error("Invalid log channel ID")
            sys.exit(1)
        if not ADMIN_USER_IDS or ADMIN_USER_IDS == ['']:
            logger.error("Invalid admin IDs")
            sys.exit(1)
        if 'YOUR_GPLINK_API' in GPLINK_API:
            logger.error("Invalid gplinks API token")
            sys.exit(1)
        if not IS_DB_ENABLED:
            logger.error("Invalid database channel IDs")
            sys.exit(1)

        check_lock_file()

        app = Application.builder().token(BOT_TOKEN).build()

        # Check for JobQueue availability
        job_queue = app.job_queue
        if job_queue is not None:
            job_queue.run_repeating(send_user_stats, interval=USER_STATS_INTERVAL, first=USER_STATS_INTERVAL)
            logger.info("User stats notification scheduled every 10 minutes")
        else:
            logger.warning("JobQueue not available. User stats notifications disabled.")
            if IS_LOGGING_ENABLED:
                await log_bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=LANGUAGES['en']['job_queue_missing']
                )

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

        await set_command_menu(app)
        telegram_handler.set_loop(asyncio.get_event_loop())
        web_runner = await start_web_server()

        logger.info("Starting bot...")
        await app.initialize()
        await app.start()
        try:
            await app.updater.start_polling()
        except Conflict as e:
            logger.error(f"Conflict detected: {e}. Ensure only one bot instance is running.")
            sys.exit(1)

        while True:
            await asyncio.sleep(3600)

    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        if IS_LOGGING_ENABLED:
            await log_bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"⚠️ Bot failed to start: {e}"
            )
        raise
    finally:
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
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
