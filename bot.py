import asyncio
import json
import logging
import os

from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)


# =========================
# Configuration
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNELS = [
    "@Yui_peachpie",
    "@analystB_T_C",
]

# The private channel where YOU post the source video.
# The bot MUST be an ADMIN of this channel (not just a member).
# Set this as an environment variable on Render, e.g. -1001234567890
SOURCE_CHANNEL_ID = int(os.getenv("SOURCE_CHANNEL_ID", "0"))

# Your own Telegram numeric user id. The bot sends you the ready-to-use
# deep link here whenever a new video is captured from the source channel.
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

DELETE_AFTER_SECONDS = 30

PORT = int(os.getenv("PORT", "10000"))

# Where captured video message ids are stored on disk.
# NOTE: Render's free-tier disk is ephemeral and resets on every new
# deploy. If that happens, just re-post the videos in the channel once
# and the bot will pick them up again automatically.
STATE_FILE = "videos_state.json"


# =========================
# Logging
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================
# Persistence for known video message ids
# =========================

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("videos", [])), data.get("latest")
    except Exception:
        return set(), None


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(
                {
                    "videos": list(KNOWN_VIDEO_IDS),
                    "latest": LATEST_VIDEO_MESSAGE_ID,
                },
                f,
            )
    except Exception as e:
        logger.error("Could not persist state: %s", e)


KNOWN_VIDEO_IDS, LATEST_VIDEO_MESSAGE_ID = load_state()


# =========================
# Health Check
# =========================

async def health_check(request):
    return web.Response(
        text="Telegram bot is running!"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    logger.info(
        "Web server started on port %s",
        PORT
    )


# =========================
# Membership Check
# =========================

async def check_membership(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE
) -> bool:

    for channel in CHANNELS:

        try:

            member = await context.bot.get_chat_member(
                chat_id=channel,
                user_id=user_id
            )

            if member.status not in (
                "member",
                "administrator",
                "creator",
            ):
                return False

        except Exception as e:

            logger.error(
                "Membership check failed for %s: %s",
                channel,
                e
            )

            return False

    return True


# =========================
# Keyboards
# =========================

def _payload(code):
    return str(code) if code else "latest"


def membership_keyboard(code=None):

    keyboard = [
        [
            InlineKeyboardButton(
                "Join Entertainment Channel 📢",
                url="https://t.me/Yui_peachpie"
            )
        ],
        [
            InlineKeyboardButton(
                "Join Analyst Channel 📢",
                url="https://t.me/analystB_T_C"
            )
        ],
        [
            InlineKeyboardButton(
                "I have joined both ✅",
                callback_data=f"check_membership:{_payload(code)}"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def restart_keyboard(code=None):

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 دریافت دوباره این ویدیو",
                callback_data=f"restart:{_payload(code)}"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# Shared flow: check membership then deliver video
# =========================

async def deliver_or_ask_join(
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    code=None,
):

    is_member = await check_membership(user_id, context)

    if not is_member:

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "👋 Welcome!\n\n"
                "To receive the requested video, please join "
                "both channels below first.\n\n"
                "After joining both channels, press "
                "\"I have joined both ✅\".\n\n"
                "Your membership will then be checked automatically."
            ),
            reply_markup=membership_keyboard(code)
        )

        return

    await send_media(chat_id=chat_id, context=context, code=code)


# =========================
# /start
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user or not update.message:
        return

    code = None

    if context.args:
        raw = context.args[0]
        if raw.startswith("v"):
            raw = raw[1:]
        if raw.isdigit():
            code = int(raw)

    await deliver_or_ask_join(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        context=context,
        code=code,
    )


# =========================
# Button Handler (join-check + restart)
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    user_id = query.from_user.id

    is_member = await check_membership(user_id, context)

    if not is_member:

        await query.answer(
            "❌ You have not joined both channels yet.",
            show_alert=True
        )

        return

    await query.answer("✅ Membership confirmed!")

    payload = query.data.split(":", 1)[1] if ":" in query.data else "latest"
    code = int(payload) if payload.isdigit() else None

    try:
        if query.message:
            await query.message.delete()
    except Exception as e:
        logger.warning("Could not delete membership message: %s", e)

    if query.message:
        await send_media(
            chat_id=query.message.chat_id,
            context=context,
            code=code,
        )


# =========================
# Capture videos posted in the private source channel
# =========================

async def channel_video_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global LATEST_VIDEO_MESSAGE_ID

    msg = update.channel_post

    if not msg or not msg.video:
        return

    LATEST_VIDEO_MESSAGE_ID = msg.message_id
    KNOWN_VIDEO_IDS.add(msg.message_id)
    save_state()

    logger.info(
        "New source video captured: message_id=%s",
        msg.message_id
    )

    if ADMIN_CHAT_ID:

        try:
            bot_username = (await context.bot.get_me()).username
            link = f"https://t.me/{bot_username}?start=v{msg.message_id}"

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "✅ ویدیوی جدید ثبت شد.\n\n"
                    "این لینک اختصاصی همین ویدیوست — آن را به‌عنوان "
                    "کپشن زیر عکس در کانال اصلی بگذار:\n\n"
                    f"{link}"
                ),
            )
        except Exception as e:
            logger.warning("Could not notify admin: %s", e)


# =========================
# Send Video (copied from the private source channel)
# =========================

async def send_media(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    code=None,
):

    message_id = None

    if code and code in KNOWN_VIDEO_IDS:
        message_id = code
    else:
        message_id = LATEST_VIDEO_MESSAGE_ID

    if not message_id or not SOURCE_CHANNEL_ID:

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ این ویدیو یافت نشد یا فعلاً چیزی تنظیم نشده است. "
                "لطفاً بعداً دوباره تلاش کنید."
            )
        )

        return

    try:

        warning_text = (
            "🎬 Here is your requested video!\n\n"
            "⚠️ IMPORTANT\n"
            "This video will be automatically deleted "
            "from this chat after 30 seconds.\n\n"
            "If you want to keep it, please save or "
            "forward the video before the 30 seconds expire."
        )

        sent_message = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=SOURCE_CHANNEL_ID,
            message_id=message_id,
            caption=warning_text,
        )

        await asyncio.sleep(DELETE_AFTER_SECONDS)

        try:

            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=sent_message.message_id
            )

            logger.info("Video deleted for chat %s", chat_id)

        except Exception as e:

            logger.warning("Could not delete video: %s", e)

        # Offer a restart button so the user can request it again
        await context.bot.send_message(
            chat_id=chat_id,
            text="می‌خواهید دوباره ویدیو را دریافت کنید؟",
            reply_markup=restart_keyboard(code)
        )

    except Exception as e:

        logger.error("Could not send video: %s", e)

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ Sorry, there was a problem "
                    "sending the video. Please try again."
                )
            )

        except Exception as send_error:

            logger.error(
                "Could not send error message: %s",
                send_error
            )


# =========================
# Restart button handler
# =========================

async def restart_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    payload = query.data.split(":", 1)[1] if ":" in query.data else "latest"
    code = int(payload) if payload.isdigit() else None

    await deliver_or_ask_join(
        chat_id=query.message.chat_id,
        user_id=query.from_user.id,
        context=context,
        code=code,
    )


# =========================
# Main
# =========================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    if not SOURCE_CHANNEL_ID:
        logger.warning(
            "SOURCE_CHANNEL_ID is not set. The bot will not be able "
            "to deliver any video until this is configured."
        )

    # Start HTTP server for Render
    await start_web_server()

    # Create Telegram application
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # Handlers
    application.add_handler(CommandHandler("start", start))

    application.add_handler(
        CallbackQueryHandler(button_handler, pattern=r"^check_membership:")
    )

    application.add_handler(
        CallbackQueryHandler(restart_handler, pattern=r"^restart:")
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=SOURCE_CHANNEL_ID) & filters.VIDEO,
            channel_video_handler,
        )
    )

    logger.info("Starting Telegram bot...")

    # Initialize Telegram application
    await application.initialize()

    # Remove any stale webhook so polling doesn't conflict
    await application.bot.delete_webhook(drop_pending_updates=True)

    await application.start()

    # Start polling (also poll channel_post updates)
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    logger.info("Bot is running...")

    # Keep application alive
    try:

        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info("Stopping bot...")

    finally:

        await application.updater.stop()
        await application.stop()
        await application.shutdown()


# =========================
# Run
# =========================

if __name__ == "__main__":
    asyncio.run(main())
