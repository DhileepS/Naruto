# Required dependencies: Install via `pip install -r requirements.txt`
# requirements.txt should include:
# python-telegram-bot==20.7
# aiohttp==3.9.5
# aiolimiter==1.1.0
# async-timeout==4.0.3

import logging
import asyncio
import sys
import os
import json
from urllib.parse import urlencode
from telegram import Bot, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from aiohttp import ClientSession, web
import math
import aiolimiter
from async_timeout import timeout

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '0')
DB_CHANNEL_1 = os.getenv('DB_CHANNEL_1', '0')
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '')
GPLINK_API = os.getenv('GPLINK_API', 'YOUR_GPLINK_API')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
PORT = int(os.getenv('PORT', 10000))
TOTAL_EPISODES = int(os.getenv('TOTAL_EPISODES', 220))
EPISODES_PER_SEASON = int(os.getenv('EPISODES_PER_SEASON', 25))
SEARCH_RESULT_LIMIT = int(os.getenv('SEARCH_RESULT_LIMIT', 50))
UPDATES_CHANNEL = '@bot_paiyan_official'

# Validate environment
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
    DB_CHANNEL_1 = int(DB_CHANNEL_1)
    ADMIN_USER_IDS = [str(id) for id in ADMIN_USER_IDS.split(',') if id] if ADMIN_USER_IDS else []
except ValueError as e:
    logger.error(f"Invalid environment variable format: {e}")
    sys.exit(1)

if not WEBHOOK_URL:
    logger.error("WEBHOOK_URL not set")
    sys.exit(1)

IS_LOGGING_ENABLED = LOG_CHANNEL_ID != 0 and LOG_CHANNEL_ID < 0
IS_DB_ENABLED = DB_CHANNEL_1 != 0 and DB_CHANNEL_1 < 0

# Initialize bot for logging
log_bot = Bot(token=BOT_TOKEN)

# Global variables
SETTINGS_FILE = "settings.json"
USERS_FILE = "users.json"
COVER_PHOTO_ID = None
FILES_PER_PAGE = 10
AUTO_DELETE_DURATION = 3600
RATE_LIMIT = 30 / 60  # 30 requests/min
SEARCH_CACHE_DURATION = 300  # 5 min
SEARCH_TIMEOUT = 10  # 10 seconds for search
BROADCAST_RATE_LIMIT = 30  # 30 messages per second

# Rate limiter and search cache
rate_limiters = defaultdict(lambda: aiolimiter.AsyncLimiter(RATE_LIMIT, 60))
broadcast_limiter = aiolimiter.AsyncLimiter(BROADCAST_RATE_LIMIT, 1)
search_cache = {}

# Load users
def load_users():
    try:
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
            logger.info(f"Loaded {len(users)} users from users.json")
            return set(users)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("users.json not found or invalid, starting with empty user list")
        return set()

def save_users(users):
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(list(users), f, indent=4)
        logger.info("Saved users to users.json")
    except Exception as e:
        logger.error(f"Failed to save users.json: {e}")
        if IS_LOGGING_ENABLED:
            asyncio.create_task(log_bot.send_message(LOG_CHANNEL_ID, f"Failed to save users.json: {e}"))

users = load_users()

# Generate season data
def generate_season_data():
    season_data = {}
    num_seasons = (TOTAL_EPISODES + EPISODES_PER_SEASON - 1) // EPISODES_PER_SEASON

    for season_num in range(1, num_seasons + 1):
        season_key = f"season_{season_num}"
        start_episode = (season_num - 1) * EPISODES_PER_SEASON + 1
        end_episode = min(season_num * EPISODES_PER_SEASON, TOTAL_EPISODES)
        episodes = {ep_num: f"https://example.com/season{season_num}/episode{ep_num}" for ep_num in range(start_episode, end_episode + 1)}
        season_data[season_key] = {
            "start_id_ref": f"https://t.me/Naruto_multilangbot?start=season{season_num}",
            "episodes": episodes,
            "content": None,
            "is_media": False,
            "buttons": []
        }
    logger.info(f"Generated {num_seasons} seasons with {TOTAL_EPISODES} total episodes")
    return season_data

# Load settings
def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            logger.info("Loaded settings from settings.json")
            return settings
    except FileNotFoundError:
        logger.warning("settings.json not found, using default settings")
        default_settings = {
            'start_text': '🌟 Welcome! Choose a season or option:',
            'start_pic': None,
            'cover_pic': None,
            'season_data': generate_season_data()
        }
        save_settings(default_settings)
        return default_settings
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in settings.json: {e}")
        default_settings = {
            'start_text': '🌟 Welcome! Choose a season or option:',
            'start_pic': None,
            'cover_pic': None,
            'season_data': generate_season_data()
        }
        save_settings(default_settings)
        return default_settings

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info("Saved settings to settings.json")
    except Exception as e:
        logger.error(f"Failed to save settings.json: {e}")
        if IS_LOGGING_ENABLED:
            asyncio.create_task(log_bot.send_message(LOG_CHANNEL_ID, f"Failed to save settings.json: {e}"))

settings = load_settings()
COVER_PHOTO_ID = settings['cover_pic']
season_data = settings['season_data']

# User state management
user_states = defaultdict(lambda: {
    'last_action': None,
    'last_season': None,
    'search_results': [],
    'search_page': 1,
    'search_query': None,
    'edit_state': None,
})

# Language support (English only)
LANGUAGES = {
    'welcome': settings['start_text'],
    'invalid_season': 'Invalid season selection. 🚫 Try again!',
    'season_not_found': 'Season not found. 😔 Use /start to see available seasons.',
    'episode_not_found': 'Episode not found. 😔 Check the number and try again.',
    'invalid_episode': 'Invalid episode number. Use /episode <number> (e.g., /episode 100). 🚫',
    'help': 'Commands:\n/start - Start bot\n/episode <number> - Get episode link\n/clearhistory - Clear history\n/owner - Owner info\n/mainchannel - Join channel\n/guide - View guide\n/broadcast - Send message to all users (admin)\n/edit - Edit settings (admin)\n🔍 Type text to search (e.g., "naruto").',
    'clearhistory': 'History cleared! 🗑️',
    'owner': 'Owner: @Dhileep_S 👨‍💼',
    'mainchannel': f'Join our channel: {UPDATES_CHANNEL} 📢',
    'guide': f'✨ **Usage Guide** ✨\n1. 🌟 Use /start to see seasons.\n2. 🎬 Select a season.\n3. 🔗 Click "Link-Shortner" for link.\n4. 📺 Use /episode <number> (e.g., /episode 100).\n5. 🔍 Search files by typing (e.g., "naruto").\n6. 📢 Join {UPDATES_CHANNEL}!',
    'not_allowed': 'Command restricted to admins. 🚫 Contact @Dhileep_S.',
    'file_not_found': 'No files found for "{query}" 😔. Try another keyword.',
    'file_search_error': 'Error searching files. 😓 Try again later.',
    'multiple_files_found': 'Found {count} files for "{query}" 🎉:\n\n{file_list}\n\nNavigate pages or refine search.',
    'page_indicator': 'Page {current}/{total}',
    'db_not_configured': 'File search not enabled. 🚫 Contact @Dhileep_S.',
    'cover_set': 'Cover photo updated! 📷',
    'cover_prompt': 'Send the cover photo.',
    'cover_invalid': 'Please send a valid photo. 🚫',
    'not_subscribed': f'You must join {UPDATES_CHANNEL} to use this bot. 📢\nJoin: https://t.me/bot_paiyan_official',
    'edit_menu': 'Edit settings:',
    'edit_start_text_prompt': 'Current start text: "{current}"\nSend new start text.',
    'edit_start_text_set': 'Start text updated! ✅',
    'edit_start_pic_prompt': 'Current start photo: {current}\nSend new start photo.',
    'edit_start_pic_set': 'Start photo updated! 📷',
    'edit_start_pic_invalid': 'Please send a valid photo. 🚫',
    'edit_cover_prompt': 'Current cover photo: {current}\nSend new cover photo.',
    'edit_cover_set': 'Cover photo updated! 📷',
    'edit_cover_invalid': 'Please send a valid photo. 🚫',
    'select_season': 'Select a season to edit links:',
    'edit_link_content_prompt': 'Send text, video, or photo for the season link.',
    'edit_link_confirm': 'Finish editing this season? This will overwrite settings.',
    'edit_link_saved': 'Season link settings saved! ✅',
    'loading': 'Processing your request… ⏳',
    'searching': 'Trying to find your query... 🔍',
    'rate_limit': 'Too many requests! Please wait 60 seconds and try again. ⏲️',
    'retry_error': 'Error occurred. Retrying… 🔄',
    'cancel': 'Operation cancelled. ✅ Back to edit menu.',
    'refine_search': 'Refine search with a new keyword.',
    'broadcast_prompt': 'Send the message to broadcast to all users.',
    'broadcast_success': 'Broadcast sent to {success_count} users. Failed: {fail_count}.',
    'broadcast_invalid': 'Please send a valid text message, photo, or video.'
}

# Helper functions
def create_link_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("How to Resolve", url="https://t.me/+_SQNyZD8hns3NzY1")],
        [InlineKeyboardButton("Try Again", url="https://t.me/bot_paiyan_official")]
    ])

def create_pagination_keyboard(current_page: int, total_pages: int):
    keyboard = []
    if total_pages > 1:
        buttons = []
        if current_page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
        if current_page < total_pages:
            buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
        if buttons:
            keyboard.append(buttons)
        keyboard.append([InlineKeyboardButton(f"Page {current_page}/{total_pages}", callback_data="noop")])
    keyboard.append([InlineKeyboardButton("🔍 Refine Search", callback_data="refine_search")])
    return InlineKeyboardMarkup(keyboard)

def create_edit_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Start Text", callback_data="edit_start_text")],
        [InlineKeyboardButton("📷 Start Pic", callback_data="edit_start_pic")],
        [InlineKeyboardButton("🖼️ Cover Pic", callback_data="edit_cover")],
        [InlineKeyboardButton("🔗 Link Edit", callback_data="edit_link")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def create_season_selection_keyboard(prefix: str):
    keyboard = [[InlineKeyboardButton(f"Season {i} 🎬", callback_data=f"{prefix}_season_{i}")] for i in range(1, len(season_data) + 1)]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def create_confirm_keyboard(action: str, data: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes", callback_data=f"confirm_{action}_{data}")],
        [InlineKeyboardButton("❌ No", callback_data="cancel")]
    ])

async def send_message_with_auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    try:
        message = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message
    except TelegramError as e:
        logger.error(f"Error sending message: {e}")
        message = await context.bot.send_message(chat_id=chat_id, text="Error occurred. Try again.")
        asyncio.create_task(schedule_message_deletion(context, chat_id, message.message_id))
        return message

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(AUTO_DELETE_DURATION)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id}")
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
                    logger.info(f"Shortened URL: {short_url}")
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

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=UPDATES_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"Error checking subscription: {e}")
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['not_subscribed'])
        return False

async def search_file_in_channel(context: ContextTypes.DEFAULT_TYPE, query: str, user_id: int) -> list:
    cache_key = f"{user_id}:{query.lower()}"
    cached = search_cache.get(cache_key)
    if cached and (time.time() - cached['timestamp']) < SEARCH_CACHE_DURATION:
        return cached['results']

    matching_files = []
    if IS_DB_ENABLED and DB_CHANNEL_1 < 0:
        try:
            async with timeout(SEARCH_TIMEOUT):
                async for message in context.bot.get_chat_history(chat_id=DB_CHANNEL_1, limit=200):
                    if len(matching_files) >= SEARCH_RESULT_LIMIT:
                        break
                    if message.document or message.video or message.audio or message.photo:
                        file_name = (message.document.file_name if message.document else
                                     message.caption or "video_file" if message.video else
                                     message.audio.file_name or "audio_file" if message.audio else
                                     message.caption or "photo_file")
                        if file_name and query.lower() in file_name.lower():
                            file_id = (message.document.file_id if message.document else
                                       message.video.file_id if message.video else
                                       message.audio.file_id if message.audio else
                                       message.photo[-1].file_id)
                            matching_files.append({'file_id': file_id, 'file_name': file_name})
        except (TelegramError, asyncio.TimeoutError) as e:
            logger.error(f"Error searching channel {DB_CHANNEL_1}: {e}")

    matching_files.sort(key=lambda x: x['file_name'].lower())
    search_cache[cache_key] = {'results': matching_files, 'timestamp': time.time()}
    logger.info(f"User {user_id} searched for '{query}', found {len(matching_files)} results")
    return matching_files

async def retry_with_backoff(coro, max_retries=3, initial_delay=1):
    for attempt in range(max_retries):
        try:
            async with timeout(10):
                return await coro
        except (TelegramError, asyncio.TimeoutError) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed after {max_retries} retries: {e}")
                raise
            delay = initial_delay * (2 ** attempt)
            logger.warning(f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}")
            await asyncio.sleep(delay)

# Webhook handler
async def webhook(request):
    update = Update.de_json(await request.json(), bot_app.bot)
    if update:
        await bot_app.process_update(update)
    return web.Response(status=200)

# Health check endpoint
async def health_check(request):
    return web.Response(text="Bot is running")

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        users.add(user_id)
        save_users(users)
        logger.info(f"Added user {user_id} to user list")

        start_param = context.args[0] if context.args else None
        if start_param and start_param.startswith('season'):
            season_key = f"season_{start_param.split('season')[1]}"
            if season_key in season_data:
                await send_season_info(update, context, season_key)
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['season_not_found'])
            return

        keyboard = [[f"Season {i} 🎬"] for i in range(1, len(season_data) + 1)] + [['Help ❓'], ['Settings ⚙️']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        if settings['start_pic']:
            await retry_with_backoff(context.bot.send_photo(
                chat_id=chat_id,
                photo=settings['start_pic'],
                caption=LANGUAGES['welcome'],
                reply_markup=reply_markup
            ))
        else:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['welcome'], reply_markup=reply_markup)
        logger.info(f"User {user_id} used /start")

async def episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        loading = await context.bot.send_message(chat_id=chat_id, text=LANGUAGES['loading'])
        try:
            if not context.args:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['invalid_episode'])
                return

            episode_number = int(context.args[0])
            if episode_number < 1 or episode_number > TOTAL_EPISODES:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['invalid_episode'])
                return

            season_key, season_num, episode_url = find_episode(episode_number)
            if not episode_url:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['episode_not_found'])
                return

            short_url = await retry_with_backoff(shorten_url(episode_url, f"episode{episode_number}"))
            caption = f"Episode {episode_number} (Season {season_num}) Link: {short_url}\n" \
                      f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
                      f"Updates: {UPDATES_CHANNEL}"
            if COVER_PHOTO_ID:
                await retry_with_backoff(context.bot.send_photo(
                    chat_id=chat_id,
                    photo=COVER_PHOTO_ID,
                    caption="Episode Cover 📷"
                ))
            await retry_with_backoff(context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=create_link_keyboard()
            ))
            logger.info(f"User {user_id} requested Episode {episode_number}")
        except ValueError:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['invalid_episode'])
        except TelegramError as e:
            logger.error(f"Error in episode command: {e}")
            await send_message_with_auto_delete(context, chat_id, f"{LANGUAGES['file_search_error']} {LANGUAGES['retry_error']}")
        finally:
            await context.bot.delete_message(chat_id=chat_id, message_id=loading.message_id)

async def clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        user_states[user_id] = {
            'last_action': None,
            'last_season': None,
            'search_results': [],
            'search_page': 1,
            'search_query': None,
            'edit_state': None
        }
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['clearhistory'])
        logger.info(f"User {user_id} cleared history")

async def owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        await send_message_with_auto_delete(context, chat_id, LANGUAGES['owner'])
        logger.info(f"User {user_id} used /owner")

async def mainchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['mainchannel'])
        logger.info(f"User {user_id} used /mainchannel")

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['guide'])
        logger.info(f"User {user_id} used /guide")

async def cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if user_id not in ADMIN_USER_IDS:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['not_allowed'])
            logger.info(f"User {user_id} attempted /cover")
            return

        user_states[user_id]['awaiting_cover'] = True
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['cover_prompt'])
        logger.info(f"User {user_id} initiated /cover")

async def handle_cover_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    if not user_states[user_id].get('awaiting_cover', False):
        return

    async with rate_limiters[user_id]:
        if update.message.photo:
            global COVER_PHOTO_ID
            COVER_PHOTO_ID = update.message.photo[-1].file_id
            settings['cover_pic'] = COVER_PHOTO_ID
            save_settings(settings)
            user_states[user_id]['awaiting_cover'] = False
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['cover_set'])
            logger.info(f"User {user_id} set cover photo")
        else:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['cover_invalid'])

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if user_id not in ADMIN_USER_IDS:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['not_allowed'])
            logger.info(f"User {user_id} attempted /edit (not admin)")
            return

        user_states[user_id]['edit_state'] = {'stage': 'menu'}
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_menu'], reply_markup=create_edit_menu_keyboard())
        logger.info(f"User {user_id} initiated /edit")

async def handle_edit_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    edit_state = user_states[user_id].get('edit_state')
    if not edit_state:
        return

    async with rate_limiters[user_id]:
        stage = edit_state.get('stage')
        logger.info(f"User {user_id} in edit stage: {stage}")
        if stage == 'start_text':
            if update.message.text:
                settings['start_text'] = update.message.text
                LANGUAGES['welcome'] = settings['start_text']
                save_settings(settings)
                user_states[user_id]['edit_state'] = {'stage': 'menu'}
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_start_text_set'], reply_markup=create_edit_menu_keyboard())
                logger.info(f"User {user_id} updated start text")
            else:
                await send_message_with_auto_delete(context, chat_id, "Please send text.")
        elif stage == 'start_pic':
            if update.message.photo:
                settings['start_pic'] = update.message.photo[-1].file_id
                save_settings(settings)
                user_states[user_id]['edit_state'] = {'stage': 'menu'}
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_start_pic_set'], reply_markup=create_edit_menu_keyboard())
                logger.info(f"User {user_id} updated start pic")
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_start_pic_invalid'])
        elif stage == 'cover':
            if update.message.photo:
                global COVER_PHOTO_ID
                COVER_PHOTO_ID = update.message.photo[-1].file_id
                settings['cover_pic'] = COVER_PHOTO_ID
                save_settings(settings)
                user_states[user_id]['edit_state'] = {'stage': 'menu'}
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_cover_set'], reply_markup=create_edit_menu_keyboard())
                logger.info(f"User {user_id} updated cover photo")
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_cover_invalid'])
        elif stage == 'link_content':
            if update.message.text or update.message.photo or update.message.video:
                edit_state['content'] = (update.message.text if update.message.text else
                                        update.message.photo[-1].file_id if update.message.photo else
                                        update.message.video.file_id)
                edit_state['is_media'] = bool(update.message.photo or update.message.video)
                edit_state['stage'] = 'confirm'
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_link_confirm'], reply_markup=create_confirm_keyboard('link_save', edit_state['season_key']))
                logger.info(f"User {user_id} provided link content for {edit_state['season_key']}")
            else:
                await send_message_with_auto_delete(context, chat_id, "Send text, photo, or video for the season link.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if user_id not in ADMIN_USER_IDS:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['not_allowed'])
            logger.info(f"User {user_id} attempted /broadcast (not admin)")
            return

        user_states[user_id]['awaiting_broadcast'] = True
        await send_message_with_auto_delete(context, chat_id, LANGUAGES['broadcast_prompt'])
        logger.info(f"User {user_id} initiated /broadcast")

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    if not user_states[user_id].get('awaiting_broadcast', False):
        return

    async with rate_limiters[user_id]:
        if update.message.text or update.message.photo or update.message.video:
            user_states[user_id]['awaiting_broadcast'] = False
            success_count = 0
            fail_count = 0
            content = update.message.text or update.message.caption or ""
            photo = update.message.photo[-1].file_id if update.message.photo else None
            video = update.message.video.file_id if update.message.video else None

            for target_user_id in users:
                async with broadcast_limiter:
                    try:
                        if photo:
                            await context.bot.send_photo(
                                chat_id=target_user_id,
                                photo=photo,
                                caption=content
                            )
                        elif video:
                            await context.bot.send_video(
                                chat_id=target_user_id,
                                video=video,
                                caption=content
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=target_user_id,
                                text=content
                            )
                        success_count += 1
                    except TelegramError as e:
                        logger.error(f"Failed to send broadcast to {target_user_id}: {e}")
                        fail_count += 1

            message = LANGUAGES['broadcast_success'].format(
                success_count=success_count,
                fail_count=fail_count
            )
            await send_message_with_auto_delete(context, chat_id, message)
            if IS_LOGGING_ENABLED:
                await log_bot.send_message(
                    LOG_CHANNEL_ID,
                    f"Broadcast by {user_id}: {message}"
                )
            logger.info(f"User {user_id} completed broadcast: {success_count} succeeded, {fail_count} failed")
        else:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['broadcast_invalid'])

async def edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if user_id not in ADMIN_USER_IDS:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['not_allowed'])
            logger.info(f"User {user_id} attempted edit button (not admin)")
            return

        if query.data == 'edit_start_text':
            user_states[user_id]['edit_state'] = {'stage': 'start_text'}
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_start_text_prompt'].format(current=settings['start_text']))
            logger.info(f"User {user_id} selected edit_start_text")
        elif query.data == 'edit_start_pic':
            user_states[user_id]['edit_state'] = {'stage': 'start_pic'}
            current = settings['start_pic'] or "None"
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_start_pic_prompt'].format(current=current))
            logger.info(f"User {user_id} selected edit_start_pic")
        elif query.data == 'edit_cover':
            user_states[user_id]['edit_state'] = {'stage': 'cover'}
            current = settings['cover_pic'] or "None"
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_cover_prompt'].format(current=current))
            logger.info(f"User {user_id} selected edit_cover")
        elif query.data == 'edit_link':
            user_states[user_id]['edit_state'] = {'stage': 'select_season', 'type': 'link'}
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['select_season'], reply_markup=create_season_selection_keyboard('link'))
            logger.info(f"User {user_id} selected edit_link")
        elif query.data.startswith('link_season_'):
            season_key = query.data.split('_', 2)[2]
            if season_key not in season_data:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['season_not_found'])
                return
            user_states[user_id]['edit_state'] = {
                'stage': 'link_content',
                'season_key': season_key,
                'content': None,
                'is_media': False,
                'buttons': []
            }
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_link_content_prompt'])
            logger.info(f"User {user_id} selected season {season_key} for link edit")
        elif query.data.startswith('confirm_'):
            action, data = query.data.split('_', 2)[1:3]
            if action == 'link_save':
                season_key = user_states[user_id]['edit_state']['season_key']
                season_data[season_key].update({
                    'content': user_states[user_id]['edit_state']['content'],
                    'is_media': user_states[user_id]['edit_state']['is_media'],
                    'buttons': user_states[user_id]['edit_state']['buttons']
                })
                settings['season_data'] = season_data
                save_settings(settings)
                user_states[user_id]['edit_state'] = {'stage': 'menu'}
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['edit_link_saved'], reply_markup=create_edit_menu_keyboard())
                logger.info(f"User {user_id} saved link settings for {season_key}")
        elif query.data == 'cancel':
            user_states[user_id]['edit_state'] = {'stage': 'menu'}
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['cancel'], reply_markup=create_edit_menu_keyboard())
            logger.info(f"User {user_id} cancelled edit")

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip().lower()

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        if text.startswith('season '):
            try:
                season_number = int(text.split(' ')[1])
                if 1 <= season_number <= len(season_data):
                    await send_season_info(update, context, f"season_{season_number}")
                    user_states[user_id]['last_action'] = f"season_{season_number}"
                else:
                    await send_message_with_auto_delete(context, chat_id, LANGUAGES['invalid_season'])
            except ValueError:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['invalid_season'])
            return

        if text == 'help':
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['help'])
            logger.info(f"User {user_id} used /help")
            return
        elif text == 'settings':
            await send_message_with_auto_delete(context, chat_id, "Use /edit to change settings (admin only).")
            return
        elif text == 'back':
            keyboard = [[f"Season {i} 🎬"] for i in range(1, len(season_data) + 1)] + [['Help ❓'], ['Settings ⚙️']]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            if settings['start_pic']:
                await retry_with_backoff(context.bot.send_photo(
                    chat_id=chat_id,
                    photo=settings['start_pic'],
                    caption=LANGUAGES['welcome'],
                    reply_markup=reply_markup
                ))
            else:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['welcome'], reply_markup=reply_markup)
            logger.info(f"User {user_id} returned to menu")
            return

        if not IS_DB_ENABLED:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['db_not_configured'])
            return

        await context.bot.send_message(chat_id=chat_id, text=LANGUAGES['searching'])
        loading = await context.bot.send_message(chat_id=chat_id, text=LANGUAGES['loading'])
        try:
            file_infos = await retry_with_backoff(search_file_in_channel(context, text, user_id))
            if not file_infos:
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['file_not_found'].format(query=text))
                return

            user_states[user_id]['search_results'] = file_infos
            user_states[user_id]['search_query'] = text
            user_states[user_id]['search_page'] = 1
            await display_search_results(update, context, page=1)
        except TelegramError as e:
            logger.error(f"Error searching files: {e}")
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['file_search_error'])
        finally:
            await context.bot.delete_message(chat_id=chat_id, message_id=loading.message_id)

async def send_season_info(update: Update, context: ContextTypes.DEFAULT_TYPE, season_key: str):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        season_info = season_data.get(season_key)
        if not season_info:
            await send_message_with_auto_delete(context, chat_id, LANGUAGES['season_not_found'])
            return

        season_name = f"Season {season_key.split('_')[1]}"
        keyboard = [
            [InlineKeyboardButton(f"🎬 {season_name}", callback_data=f"info_{season_key}")],
            [InlineKeyboardButton("🔗 Link-Shortner", callback_data=f"resolve_{season_key}")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ] + season_info['buttons']
        reply_markup = InlineKeyboardMarkup(keyboard)
        if season_info['is_media']:
            if season_info['content'].endswith('.mp4'):
                await retry_with_backoff(context.bot.send_video(chat_id=chat_id, video=season_info['content'], caption=f"{season_name}:", reply_markup=reply_markup))
            else:
                await retry_with_backoff(context.bot.send_photo(chat_id=chat_id, photo=season_info['content'], caption=f"{season_name}:", reply_markup=reply_markup))
        else:
            await send_message_with_auto_delete(context, chat_id, season_info['content'] or f"{season_name}:", reply_markup=reply_markup)
        user_states[user_id]['last_season'] = season_key
        logger.info(f"User {user_id} accessed {season_key}")

async def display_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    file_infos = user_states[user_id]['search_results']
    query = user_states[user_id]['search_query']
    total_files = len(file_infos)
    total_pages = math.ceil(total_files / FILES_PER_PAGE)

    async with rate_limiters[user_id]:
        if page < 1 or page > total_pages:
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
            short_url = await retry_with_backoff(shorten_url(start_link, f"file_{file_id}"))
            file_list.append(f"- {file_name}: {short_url}")

        file_list_text = "\n".join(file_list)
        message_text = LANGUAGES['multiple_files_found'].format(count=total_files, query=query, file_list=file_list_text)
        caption = f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\nUpdates: {UPDATES_CHANNEL}"
        reply_markup = create_pagination_keyboard(page, total_pages)

        if COVER_PHOTO_ID:
            await retry_with_backoff(context.bot.send_photo(
                chat_id=chat_id,
                photo=COVER_PHOTO_ID,
                caption="Search Results Cover 📷"
            ))
        await send_message_with_auto_delete(context, chat_id, f"{message_text}\n\n{caption}", reply_markup=reply_markup)
        logger.info(f"User {user_id} viewed search page {page}/{total_pages} for '{query}'")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async with rate_limiters[user_id]:
        if not await check_subscription(context, user_id, chat_id):
            return

        try:
            if query.data.startswith('info_'):
                season_key = query.data.split('_', 1)[1]
                await send_season_info(update, context, season_key)
            elif query.data.startswith('resolve_'):
                season_key = query.data.split('_', 1)[1]
                season_info = season_data.get(season_key)
                if season_info:
                    long_url = season_info["start_id_ref"]
                    short_url = await retry_with_backoff(shorten_url(long_url, season_key))
                    season_name = f"Season {season_key.split('_')[1]}"
                    caption = f"{season_name} Link: {short_url}\n" \
                              f"How to resolve: Follow the guide at https://t.me/+_SQNyZD8hns3NzY1\n" \
                              f"Updates: {UPDATES_CHANNEL}"
                    reply_markup = InlineKeyboardMarkup(season_info['buttons'] + create_link_keyboard().inline_keyboard)
                    if season_info['is_media']:
                        if season_info['content'].endswith('.mp4'):
                            await retry_with_backoff(context.bot.send_video(chat_id=chat_id, video=season_info['content'], caption=caption, reply_markup=reply_markup))
                        else:
                            await retry_with_backoff(context.bot.send_photo(chat_id=chat_id, photo=season_info['content'], caption=caption, reply_markup=reply_markup))
                    else:
                        await send_message_with_auto_delete(context, chat_id, season_info['content'] or caption, reply_markup=reply_markup)
            elif query.data.startswith('edit_') or query.data.startswith('confirm_') or query.data == 'cancel':
                await edit_button(update, context)
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
            elif query.data == 'refine_search':
                user_states[user_id]['search_results'] = []
                user_states[user_id]['search_query'] = None
                user_states[user_id]['search_page'] = 1
                await send_message_with_auto_delete(context, chat_id, LANGUAGES['refine_search'])
            elif query.data == 'back_to_menu':
                keyboard = [[f"Season {i} 🎬"] for i in range(1, len(season_data) + 1)] + [['Help ❓'], ['Settings ⚙️']]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                if settings['start_pic']:
                    await retry_with_backoff(context.bot.send_photo(
                        chat_id=chat_id,
                        photo=settings['start_pic'],
                        caption=LANGUAGES['welcome'],
                        reply_markup=reply_markup
                    ))
                else:
                    await send_message_with_auto_delete(context, chat_id, LANGUAGES['welcome'], reply_markup=reply_markup)
            elif query.data == 'noop':
                pass
        except TelegramError as e:
            logger.error(f"Error handling button: {e}")
            await send_message_with_auto_delete(context, chat_id, f"{LANGUAGES['file_search_error']} {LANGUAGES['retry_error']}")

# Global bot application
bot_app = None

async def main():
    global bot_app
    try:
        # Validate environment
        if 'YOUR_BOT_TOKEN' in BOT_TOKEN:
            logger.error("Invalid bot token")
            sys.exit(1)
        if LOG_CHANNEL_ID == 0:
            logger.error("Invalid log channel ID")
            sys.exit(1)
        if not ADMIN_USER_IDS:
            logger.error("Invalid admin IDs")
            sys.exit(1)
        if 'YOUR_GPLINK_API' in GPLINK_API:
            logger.error("Invalid gplinks API token")
            sys.exit(1)
        if not IS_DB_ENABLED:
            logger.error("Invalid database channel ID")
            sys.exit(1)
        if TOTAL_EPISODES <= 0 or EPISODES_PER_SEASON <= 0:
            logger.error("Invalid TOTAL_EPISODES or EPISODES_PER_SEASON")
            sys.exit(1)
        if SEARCH_RESULT_LIMIT <= 0:
            logger.error("Invalid SEARCH_RESULT_LIMIT")
            sys.exit(1)

        logger.info(f"Bot configuration: SEARCH_TIMEOUT={SEARCH_TIMEOUT}s, TOTAL_EPISODES={TOTAL_EPISODES}, EPISODES_PER_SEASON={EPISODES_PER_SEASON}, SEARCH_RESULT_LIMIT={SEARCH_RESULT_LIMIT}")

        # Start HTTP server for webhooks and health checks
        app = web.Application()
        app.add_routes([
            web.post('/', webhook),
            web.get('/health', health_check)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"Webhook server started on port {PORT}")

        # Initialize Telegram bot
        bot_app = Application.builder().token(BOT_TOKEN).build()

        bot_app.add_handler(CommandHandler('start', start))
        bot_app.add_handler(CommandHandler('episode', episode))
        bot_app.add_handler(CommandHandler('clearhistory', clearhistory))
        bot_app.add_handler(CommandHandler('owner', owner))
        bot_app.add_handler(CommandHandler('mainchannel', mainchannel))
        bot_app.add_handler(CommandHandler('guide', guide))
        bot_app.add_handler(CommandHandler('cover', cover))
        bot_app.add_handler(CommandHandler('edit', edit))
        bot_app.add_handler(CommandHandler('broadcast', broadcast))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_selection))
        bot_app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_cover_photo))
        bot_app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, handle_broadcast_message))
        bot_app.add_handler(CallbackQueryHandler(button))

        await bot_app.initialize()
        await bot_app.start()

        # Set webhook
        webhook_path = f"{WEBHOOK_URL}/"
        try:
            await bot_app.bot.set_webhook(webhook_path)
            logger.info(f"Webhook set to {webhook_path}")
        except TelegramError as e:
            logger.error(f"Failed to set webhook: {e}")
            if IS_LOGGING_ENABLED:
                await log_bot.send_message(LOG_CHANNEL_ID, f"Failed to set webhook: {e}")
            sys.exit(1)

        logger.info("Bot started")

        # Keep the bot running
        while True:
            await asyncio.sleep(3600)

    except Exception as e:
        logger.error(f"Error in main: {e}")
        if IS_LOGGING_ENABLED:
            await log_bot.send_message(LOG_CHANNEL_ID, f"Critical error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
