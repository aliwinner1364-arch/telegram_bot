import asyncio
import logging
import os

from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)


# =========================
# Configuration
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNELS = [
    "@Yui_peachpie",
    "@analystB_T_C",
]

VIDEO_URL = "https://www.w3schools.com/html/mov_bbb.mp4"

DELETE_AFTER_SECONDS = 10

PORT = int(os.getenv("PORT", "10000"))


# =========================
# Logging
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


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
# Membership Buttons
# =========================

def membership_keyboard():

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
                callback_data="check_membership"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# /start
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not update.message:
        return

    user_id = update.effective_user.id

    is_member = await check_membership(
        user_id,
        context
    )

    if not is_member:

        await update.message.reply_text(
            "👋 Welcome!\n\n"
            "To receive the requested video, please join "
            "both channels below first.\n\n"
            "After joining both channels, press "
            "\"I have joined both ✅\".\n\n"
            "Your membership will then be checked automatically.",
            reply_markup=membership_keyboard()
        )

        return

    await send_media(
        chat_id=update.effective_chat.id,
        context=context
    )


# =========================
# Button Handler
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    user_id = query.from_user.id

    is_member = await check_membership(
        user_id,
        context
    )

    if not is_member:

        await query.answer(
            "❌ You have not joined both channels yet.",
            show_alert=True
        )

        return

    await query.answer(
        "✅ Membership confirmed!"
    )

    try:

        if query.message:
            await query.message.delete()

    except Exception as e:

        logger.warning(
            "Could not delete membership message: %s",
            e
        )

    if query.message:

        await send_media(
            chat_id=query.message.chat_id,
            context=context
        )


# =========================
# Send Video
# =========================

async def send_media(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        warning_text = (
            "🎬 Here is your requested video!\n\n"
            "⚠️ IMPORTANT\n"
            "This video will be automatically deleted "
            "from this chat after 10 seconds.\n\n"
            "If you want to keep it, please save or "
            "forward the video before the 10 seconds expire."
        )

        sent_message = await context.bot.send_video(
            chat_id=chat_id,
            video=VIDEO_URL,
            caption=warning_text
        )

        await asyncio.sleep(
            DELETE_AFTER_SECONDS
        )

        try:

            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=sent_message.message_id
            )

            logger.info(
                "Video deleted for chat %s",
                chat_id
            )

        except Exception as e:

            logger.warning(
                "Could not delete video: %s",
                e
            )

    except Exception as e:

        logger.error(
            "Could not send video: %s",
            e
        )

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
# Main
# =========================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
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
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler,
            pattern="^check_membership$"
        )
    )

    logger.info(
        "Starting Telegram bot..."
    )

    # Initialize Telegram application
    await application.initialize()

    await application.start()

    # Start polling
    await application.updater.start_polling()

    logger.info(
        "Bot is running..."
    )

    # Keep application alive
    try:

        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info(
            "Stopping bot..."
        )

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================
# Run
# =========================

if __name__ == "__main__":
    asyncio.run(main())
