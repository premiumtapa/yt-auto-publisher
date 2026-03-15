"""
YouTube Auto-Publisher Bot
==========================

Entry point for the bot. Loads configuration, initializes all services,
and starts the Telegram bot.

Supports two modes:
  - LOCAL (default): Polling mode, reads tokens from files
  - RENDER (env RENDER=true): Webhook mode, reads tokens from env vars

Usage:
    Local:  python main.py
    Render: Set RENDER=true, PORT, RENDER_EXTERNAL_URL env vars
"""

import os
import sys
import asyncio
import signal
import json
import logging
import tempfile
from dotenv import load_dotenv

import gemini_ai
import telegram_bot
import account_manager
import health_check

# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


def _setup_client_secret_on_render():
    """On Render, write client_secret.json from env var to a temp file."""
    raw = os.getenv("CLIENT_SECRET_JSON")
    if not raw:
        logger.warning("CLIENT_SECRET_JSON env var not set — YouTube OAuth refresh may fail")
        return "client_secret.json"  # fallback
    tmp = os.path.join(tempfile.gettempdir(), "client_secret.json")
    with open(tmp, "w") as f:
        f.write(raw)
    return tmp


async def run_bot():
    """Async main entry point — runs the Telegram bot with proper lifecycle."""
    # ──────────────────────────────────────────
    # Load environment variables
    # ──────────────────────────────────────────
    load_dotenv()

    is_render = bool(os.getenv("RENDER"))

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    # Support both old single-ID key and new multi-ID key
    telegram_user_ids_raw = os.getenv("TELEGRAM_USER_IDS") or os.getenv("TELEGRAM_USER_ID")
    gemini_api_key = os.getenv("GEMINI_API_KEY")

    if is_render:
        client_secret_file = _setup_client_secret_on_render()
    else:
        client_secret_file = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")

    # Validate required config
    missing = []
    if not telegram_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not telegram_user_ids_raw:
        missing.append("TELEGRAM_USER_IDS")
    if not gemini_api_key:
        missing.append("GEMINI_API_KEY")

    if missing:
        logger.error(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in your credentials."
        )
        sys.exit(1)

    # Parse comma-separated list of user IDs
    try:
        authorized_ids = set(
            int(uid.strip())
            for uid in telegram_user_ids_raw.split(",")
            if uid.strip()
        )
    except ValueError:
        logger.error("TELEGRAM_USER_IDS must be numeric IDs separated by commas (e.g. 123456,789012).")
        sys.exit(1)

    if not authorized_ids:
        logger.error("TELEGRAM_USER_IDS is empty. Add at least one Telegram user ID.")
        sys.exit(1)

    # ──────────────────────────────────────────
    # Initialize services
    # ──────────────────────────────────────────
    logger.info("=" * 50)
    logger.info("  YouTube Auto-Publisher Bot Starting...")
    logger.info(f"  Mode: {'RENDER (webhook)' if is_render else 'LOCAL (polling)'}")
    logger.info("=" * 50)

    # 1. Configure Gemini AI
    logger.info("Configuring Gemini AI...")
    gemini_ai.configure(gemini_api_key)

    # 2. Migrate existing single-account token (if present, local mode only)
    logger.info("Checking for existing YouTube accounts...")
    if not is_render:
        account_manager.migrate_existing_token()

    accounts = account_manager.list_accounts()
    if accounts:
        logger.info(f"Found {len(accounts)} YouTube account(s):")
        for name, info in accounts.items():
            logger.info(f"  📺 {info.get('label', name)} ({info.get('channel_name', 'Unknown')})")
    else:
        logger.info("No YouTube accounts configured yet. Use /start in Telegram to add one.")

    # 3. Initialize Telegram bot
    logger.info(f"Initializing Telegram bot for {len(authorized_ids)} authorized user(s): {authorized_ids}")
    telegram_bot.init(authorized_ids, client_secret_file)
    app = telegram_bot.create_app(telegram_token)

    # ──────────────────────────────────────────
    # Start the bot
    # ──────────────────────────────────────────
    if is_render:
        await _run_webhook(app, telegram_token)
    else:
        await _run_polling(app)


async def _run_polling(app):
    """Run bot in polling mode (local development)."""
    logger.info("=" * 50)
    logger.info("  Bot is running! Waiting for Telegram commands...")
    logger.info("  Send /start to see the interactive menu.")
    logger.info("  Press Ctrl+C to stop.")
    logger.info("=" * 50)

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Keep running until interrupted
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, _signal_handler)
            loop.add_signal_handler(signal.SIGTERM, _signal_handler)
        except NotImplementedError:
            pass

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Shutting down bot...")
            await app.updater.stop()
            await app.stop()


async def _run_webhook(app, telegram_token):
    """Run bot in webhook mode (Render deployment)."""
    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", "")

    if not render_url:
        logger.error("RENDER_EXTERNAL_URL not set! Cannot configure webhook.")
        sys.exit(1)

    webhook_url = f"{render_url}/webhook"

    logger.info("=" * 50)
    logger.info(f"  Webhook mode on port {port}")
    logger.info(f"  Webhook URL: {webhook_url}")
    logger.info(f"  Health check: {render_url}/")
    logger.info("=" * 50)

    # python-telegram-bot's built-in webhook server
    async with app:
        await app.start()
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )

        logger.info("Bot is running in webhook mode!")

        # Keep running until terminated
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, _signal_handler)
            loop.add_signal_handler(signal.SIGTERM, _signal_handler)
        except NotImplementedError:
            pass

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Shutting down webhook...")
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
