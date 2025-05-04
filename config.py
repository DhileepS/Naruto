import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
GPLINK_API = os.getenv("GPLINK_API")
ADMIN_USER_IDS = list(map(int, os.getenv("ADMIN_USER_IDS").split(",")))
