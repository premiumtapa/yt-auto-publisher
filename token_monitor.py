"""
Token Monitor
=============
Background task that:
1. Checks all account tokens every 24 hours.
2. Sends a Telegram alert when a token is within TOKEN_WARN_DAYS of expiry.
3. Sends an urgent 🚨 alert if a token has already expired.

Token expiry is tracked via the 'authorized_at' field in accounts.json / ACCOUNTS_JSON.
Google Testing-mode apps expire refresh tokens after 7 days.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import account_manager

logger = logging.getLogger(__name__)

# Warn this many days before 7-day Testing expiry
TOKEN_WARN_DAYS = 2
TOKEN_TOTAL_DAYS = 7  # Google Testing mode refresh-token lifetime

# Check every 24 hours (test mode: set TOKEN_MONITOR_TEST=1 to fire immediately)
CHECK_INTERVAL_HOURS = 24


def _days_since_auth(info: dict) -> float | None:
    """Return how many days since the account was last authorized. None if unknown."""
    authorized_at = info.get("authorized_at")
    if not authorized_at:
        return None
    try:
        ts = datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400
    except Exception:
        return None


async def _check_and_notify(bot, user_ids: set[int], render_url: str = ""):
    """Check all accounts and send Telegram alerts for expiring/expired tokens."""
    accounts = account_manager.list_accounts()
    if not accounts:
        return

    for nickname, info in accounts.items():
        label = info.get("label", nickname)
        days_old = _days_since_auth(info)

        if days_old is None:
            # No authorized_at → can't tell; skip (legacy accounts)
            continue

        days_left = TOKEN_TOTAL_DAYS - days_old

        if days_left <= 0:
            # Already expired
            msg = (
                f"🚨 *Token EXPIRED!*\n\n"
                f"Account *{label}* token has expired.\n"
                f"Tap below to re-authorize now:\n\n"
                f"_The bot cannot access this channel until you re-auth._"
            )
            urgency = "expired"
        elif days_left <= TOKEN_WARN_DAYS:
            days_str = f"{days_left:.0f} day{'s' if days_left != 1 else ''}"
            msg = (
                f"⚠️ *Token Expiring Soon!*\n\n"
                f"Account *{label}* expires in *{days_str}*.\n"
                f"Tap below to re-authorize before it breaks:"
            )
            urgency = "warning"
        else:
            continue  # Fine, nothing to do

        logger.warning(f"[TokenMonitor] {urgency} for '{nickname}' ({label}) — days_left={days_left:.1f}")

        # Send alert to all authorized users
        reauth_url = f"{render_url}/oauth/start/{nickname}" if render_url else None

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = [
            [InlineKeyboardButton("🔁 Re-auth Now", callback_data=f"reauth_{nickname}")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        markup = InlineKeyboardMarkup(buttons)

        for uid in user_ids:
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=msg,
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
                logger.info(f"[TokenMonitor] Sent {urgency} alert to user {uid} for '{nickname}'")
            except Exception as exc:
                logger.error(f"[TokenMonitor] Failed to send alert to {uid}: {exc}")


async def start_monitor(bot, user_ids: set[int], render_url: str = ""):
    """
    Start the background token monitor loop.
    Run this as an asyncio task alongside the Telegram bot.
    """
    test_mode = bool(os.getenv("TOKEN_MONITOR_TEST"))
    interval_seconds = 5 if test_mode else CHECK_INTERVAL_HOURS * 3600

    logger.info(
        f"[TokenMonitor] Started — checking every "
        f"{'5 seconds (TEST MODE)' if test_mode else f'{CHECK_INTERVAL_HOURS}h'}"
    )

    # In test mode fire immediately; otherwise wait first interval before first check
    if not test_mode:
        await asyncio.sleep(interval_seconds)

    while True:
        try:
            await _check_and_notify(bot, user_ids, render_url)
        except Exception as exc:
            logger.error(f"[TokenMonitor] Unexpected error during check: {exc}", exc_info=True)
        await asyncio.sleep(interval_seconds)
