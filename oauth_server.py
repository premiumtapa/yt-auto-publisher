"""
OAuth Server
============
Minimal aiohttp web server that serves as the SINGLE HTTP server for the bot on Render.

Routes:
  POST /webhook          → forward Telegram updates to python-telegram-bot
  GET  /oauth/callback   → Google OAuth2 callback (exchange code → save token)
  GET  /oauth/start/<nick> → redirect browser to Google OAuth consent screen
  GET  /                 → health check (200 OK)

This replaces health_check.py (no longer needed).
"""

import json
import logging
import os
from datetime import datetime, timezone

from aiohttp import web

import account_manager

logger = logging.getLogger(__name__)

# Set by main.py before server starts
_ptb_app = None          # python-telegram-bot Application
_authorized_user_ids: set[int] = set()
_render_url: str = ""
_client_secret_file: str = "client_secret.json"


def configure(ptb_app, authorized_user_ids: set[int], render_url: str, client_secret_file: str):
    global _ptb_app, _authorized_user_ids, _render_url, _client_secret_file
    _ptb_app = ptb_app
    _authorized_user_ids = authorized_user_ids
    _render_url = render_url.rstrip("/")
    _client_secret_file = client_secret_file


# ── Health check ──────────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK - YouTube Auto-Publisher Bot is running")


# ── Telegram webhook ──────────────────────────────────────────────────────────

async def handle_webhook(request: web.Request) -> web.Response:
    """Receive a Telegram update and hand it to python-telegram-bot."""
    if _ptb_app is None:
        return web.Response(status=503, text="Bot not initialized")
    try:
        from telegram import Update
        body = await request.read()
        data = json.loads(body)
        update = Update.de_json(data, _ptb_app.bot)
        await _ptb_app.process_update(update)
    except Exception as exc:
        logger.error(f"Error processing Telegram update: {exc}", exc_info=True)
    return web.Response(status=200, text="OK")


# ── OAuth start (redirect to Google) ──────────────────────────────────────────

async def handle_oauth_start(request: web.Request) -> web.Response:
    """Generate the Google OAuth URL and redirect the user's browser to it."""
    nickname = request.match_info.get("nickname", "").lower().strip()
    if not nickname:
        return web.Response(status=400, text="Missing account nickname")

    accounts = account_manager.list_accounts()
    if nickname not in accounts:
        return web.Response(status=404, text=f"Account '{nickname}' not found")

    redirect_uri = f"{_render_url}/oauth/callback"
    try:
        auth_url = account_manager.get_oauth_url(nickname, redirect_uri, _client_secret_file)
    except Exception as exc:
        logger.error(f"Failed to generate OAuth URL for '{nickname}': {exc}")
        return web.Response(status=500, text=f"Error generating auth URL: {exc}")

    raise web.HTTPFound(location=auth_url)


# ── OAuth callback (exchange code → save token) ────────────────────────────────

async def handle_oauth_callback(request: web.Request) -> web.Response:
    """Receive Google's redirect with ?code=...&state=<nickname>, exchange for token."""
    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")  # nickname
    error = request.rel_url.query.get("error")

    if error:
        logger.warning(f"OAuth callback returned error: {error}")
        return web.Response(
            content_type="text/html",
            text=_html_page(
                "❌ Authorization Failed",
                f"<p>Google returned an error: <code>{error}</code></p>"
                "<p>Return to Telegram and try again.</p>",
            ),
        )

    if not code or not state:
        return web.Response(status=400, text="Missing code or state parameter")

    nickname = state
    redirect_uri = f"{_render_url}/oauth/callback"

    try:
        creds = account_manager.exchange_code_for_token(
            code=code,
            nickname=nickname,
            redirect_uri=redirect_uri,
            client_secret_file=_client_secret_file,
        )
        # Save in-memory so the bot can use it immediately without restart
        account_manager.save_creds_in_memory(nickname, creds)
        # Update 'authorized_at' timestamp
        account_manager.mark_authorized(nickname)

        # Persist token: log it and send to user so they can update env var
        token_json_str = creds.to_json()
        env_key = f"YT_TOKEN_{nickname.upper()}"
        logger.info(
            f"[REAUTH-TOKEN] '{nickname}' new token (copy this entire JSON "
            f"to Render env var {env_key}):\n{token_json_str}"
        )

        logger.info(f"OAuth callback success for '{nickname}'")

        # Notify user via Telegram
        accounts = account_manager.list_accounts()
        label = accounts.get(nickname, {}).get("label", nickname)
        await _send_reauth_success(nickname, label, token_json_str, env_key)

        return web.Response(
            content_type="text/html",
            text=_html_page(
                "✅ Authorization Complete!",
                f"<p><strong>{label}</strong> has been re-authorized successfully.</p>"
                "<p>You can now close this browser tab and return to Telegram.</p>",
            ),
        )

    except Exception as exc:
        logger.error(f"OAuth token exchange failed for '{nickname}': {exc}", exc_info=True)
        return web.Response(
            content_type="text/html",
            text=_html_page(
                "❌ Authorization Failed",
                f"<p>Could not exchange code for token.</p>"
                f"<p><code>{exc}</code></p>"
                "<p>Return to Telegram and try again.</p>",
            ),
        )


async def _send_reauth_success(nickname: str, label: str, token_json_str: str = "", env_key: str = ""):
    """Send a Telegram confirmation message + token JSON after successful re-auth."""
    if _ptb_app is None:
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Check Status", callback_data=f"status_{nickname}")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
    ])
    for uid in _authorized_user_ids:
        try:
            await _ptb_app.bot.send_message(
                chat_id=uid,
                text=f"✅ *{label}* re-authorized successfully!\n\nThe bot can now use this account.",
                parse_mode="Markdown",
                reply_markup=markup,
            )
            # Send the new token JSON so user can update Render env var
            if token_json_str and env_key:
                await _ptb_app.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"🔑 *Update Render env var to persist this token:*\n\n"
                        f"*Variable:* `{env_key}`\n\n"
                        f"*Value (copy all):*\n"
                        f"```\n{token_json_str}\n```\n\n"
                        f"_Go to Render → Environment → update {env_key} with the JSON above. "
                        f"Without this, the token is lost on next server restart._"
                    ),
                    parse_mode="Markdown",
                )
        except Exception as exc:
            logger.warning(f"Could not notify user {uid} of reauth success: {exc}")


def _html_page(title: str, body: str) -> str:
    """Simple HTML response page."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 480px; margin: 60px auto; padding: 20px; text-align: center; }}
    h1 {{ font-size: 1.8em; margin-bottom: 0.5em; }}
    p {{ color: #555; line-height: 1.6; }}
    code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {body}
</body>
</html>"""


# ── App factory ────────────────────────────────────────────────────────────────

def create_web_app() -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/oauth/start/{nickname}", handle_oauth_start)
    app.router.add_get("/oauth/callback", handle_oauth_callback)
    return app
