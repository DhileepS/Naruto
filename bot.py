from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext
import requests, os
from config import BOT_TOKEN, LOG_CHANNEL_ID, GPLINK_API, ADMIN_USER_IDS

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Welcome! Use /get S01E01 to get an episode.")

def gplink_shorten(url):
    response = requests.get(f"https://gplinks.in/api?api={GPLINK_API}&url={url}")
    return response.json().get("shortenedUrl", url)

async def get(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Usage: /get S01E01")
        return
    ep = context.args[0]
    filename = f"{ep}.mp4"
    mega_url = f"https://mega.nz/file/dummyid#{ep}"  # Replace with real file lookup logic
    short_link = gplink_shorten(mega_url)

    buttons = [[InlineKeyboardButton("Download", url=short_link)]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(f"{ep} is ready!", reply_markup=reply_markup)

    # Log to group
    await context.bot.send_message(LOG_CHANNEL_ID, f"📥 {update.effective_user.first_name} used /get {ep}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("get", get))
    app.run_polling()

if __name__ == "__main__":
    main()
