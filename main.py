"""
YouTube Auto-Publisher Bot
==========================

Entry point for the bot. Loads configuration, initializes all services,
and starts the Telegram bot.

Supports two modes:
  - LOCAL (default): Polling mode, reads tokens from files
  - RENDER (env RENDER=true): Single aiohttp server on PORT handling:
      POST /webhook       — Telegram updates
      GET  /oauth/callback — Google OAuth2 code exchange
      GET  /oauth/start/<nick> — Redirect user to Google consent screen
      GET  /             — Health check for UptimeRobot

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
import oauth_server
import token_monitor

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
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


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
    logger.info(f"  Mode: {'RENDER (aiohttp webhook)' if is_render else 'LOCAL (polling)'}")
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
        await _run_render(app, telegram_token, authorized_ids, client_secret_file)
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


async def _run_render(app, telegram_token: str, authorized_ids: set, client_secret_file: str):
    """
    Run bot in Render mode with a single aiohttp server.
    Handles Telegram webhook, OAuth callback, and health check all on PORT.
    """
    from aiohttp import web

    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

    if not render_url:
        logger.error("RENDER_EXTERNAL_URL not set! Cannot configure webhook.")
        sys.exit(1)

    webhook_url = f"{render_url}/webhook"

    # Configure oauth_server with references to the PTB app and settings
    oauth_server.configure(
        ptb_app=app,
        authorized_user_ids=authorized_ids,
        render_url=render_url,
        client_secret_file=client_secret_file,
    )

    logger.info("=" * 50)
    logger.info(f"  aiohttp server on port {port}")
    logger.info(f"  Webhook URL: {webhook_url}")
    logger.info(f"  OAuth callback: {render_url}/oauth/callback")
    logger.info(f"  Health check: {render_url}/")
    logger.info("=" * 50)

    # Initialize the PTB application (sets up bot, handlers, etc.)
    async with app:
        await app.start()

        # Register webhook with Telegram
        await app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
        )
        logger.info(f"Telegram webhook registered at {webhook_url}")

        # Build and start aiohttp server
        web_app = oauth_server.create_web_app()
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
        logger.info(f"aiohttp server listening on 0.0.0.0:{port}")

        # Start token monitor background task
        monitor_task = asyncio.create_task(
            token_monitor.start_monitor(
                bot=app.bot,
                user_ids=authorized_ids,
                render_url=render_url,
            )
        )

        logger.info("Bot is running in Render mode!")

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
            logger.info("Shutting down...")
            monitor_task.cancel()
            await runner.cleanup()
            await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
