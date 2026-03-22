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

import aiohttp
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

        # Auto-persist token to Render env var via API
        token_json_str = creds.to_json()
        env_key = f"YT_TOKEN_{nickname.upper()}"
        render_saved = await _update_render_env_var(env_key, token_json_str)

        logger.info(f"OAuth callback success for '{nickname}' (render_saved={render_saved})")

        # Notify user via Telegram
        accounts = account_manager.list_accounts()
        label = accounts.get(nickname, {}).get("label", nickname)
        await _send_reauth_success(nickname, label, render_saved)

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


async def _update_render_env_var(key: str, value: str) -> bool:
    """
    Update a single env var on Render via the API.
    Reads ALL current env vars, replaces/adds the target key, PUTs them all back.
    Returns True on success, False if API key/service ID are missing or API call fails.
    """
    api_key = os.getenv("RENDER_API_KEY", "")
    service_id = os.getenv("RENDER_SERVICE_ID", "")
    if not api_key or not service_id:
        logger.warning(
            f"Cannot auto-update env var '{key}': RENDER_API_KEY or RENDER_SERVICE_ID not set"
        )
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base_url = f"https://api.render.com/v1/services/{service_id}/env-vars"

    try:
        async with aiohttp.ClientSession() as session:
            # GET current env vars
            async with session.get(base_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Render GET env-vars failed: {resp.status}")
                    return False
                current = await resp.json()

            # Build updated list: replace target key or add it
            env_vars = []
            found = False
            for ev in current:
                e = ev.get("envVar", ev)
                k = e.get("key", "")
                v = e.get("value", "")
                if k == key:
                    env_vars.append({"key": k, "value": value})
                    found = True
                else:
                    env_vars.append({"key": k, "value": v})
            if not found:
                env_vars.append({"key": key, "value": value})

            # PUT all env vars back
            async with session.put(base_url, headers=headers, json=env_vars) as resp:
                if resp.status == 200:
                    logger.info(f"✅ Auto-updated Render env var '{key}'")
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"Render PUT env-vars failed: {resp.status} — {body[:200]}")
                    return False
    except Exception as exc:
        logger.error(f"Render API error updating '{key}': {exc}")
        return False


async def _send_reauth_success(nickname: str, label: str, render_saved: bool = False):
    """Send a Telegram confirmation message after successful re-auth."""
    if _ptb_app is None:
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Check Status", callback_data=f"status_{nickname}")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
    ])
    if render_saved:
        save_note = "\n\n💾 Token auto\-saved to Render — survives restarts\."
    else:
        save_note = (
            "\n\n⚠️ Token saved in memory only\. "
            "Set `RENDER_API_KEY` and `RENDER_SERVICE_ID` env vars on Render "
            "to enable auto\-save\."
        )
    for uid in _authorized_user_ids:
        try:
            await _ptb_app.bot.send_message(
                chat_id=uid,
                text=(
                    f"✅ *{label}* re\-authorized successfully\!"
                    f"{save_note}"
                ),
                parse_mode="MarkdownV2",
                reply_markup=markup,
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
