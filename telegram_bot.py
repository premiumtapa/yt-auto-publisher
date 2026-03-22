"""
Telegram Bot Interface
Interactive menu system with inline buttons for multi-account YouTube publishing.
"""

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import youtube_api
import gemini_ai
import account_manager

logger = logging.getLogger(__name__)

# Will be set during initialization
AUTHORIZED_USER_IDS: set[int] = set()
CLIENT_SECRET_FILE: str = "client_secret.json"

# State keys for add_account flow (stored in context.user_data)
STATE_KEY = "add_account_state"
STATE_WAITING_NICKNAME = "waiting_nickname"
STATE_WAITING_LABEL = "waiting_label"
STATE_PICKING_CHANNEL = "picking_channel"  # shown when Google account has multiple channels

import asyncio


def init(authorized_user_ids: set[int] | int, client_secret_file: str = "client_secret.json"):
    """Initialize the bot with one or more authorized Telegram user IDs."""
    global AUTHORIZED_USER_IDS, CLIENT_SECRET_FILE
    # Accept both a set/list and a single int for backwards compatibility
    if isinstance(authorized_user_ids, int):
        AUTHORIZED_USER_IDS = {authorized_user_ids}
    else:
        AUTHORIZED_USER_IDS = set(authorized_user_ids)
    CLIENT_SECRET_FILE = client_secret_file
    logger.info(f"Telegram bot initialized for {len(AUTHORIZED_USER_IDS)} authorized user(s): {AUTHORIZED_USER_IDS}")


def is_authorized(update: Update) -> bool:
    """Check if the message/callback is from an authorized user."""
    user = update.effective_user
    return user is not None and user.id in AUTHORIZED_USER_IDS


def format_error(e: Exception) -> str:
    """Format an exception into a user-friendly string."""
    err_str = str(e)
    if "invalid_grant" in err_str or "Token has been expired or revoked" in err_str:
        return (
            "⚠️ *Token Expired/Revoked*\n"
            "Tap 🔁 *Re-auth* next to this account in 👤 Manage Accounts to fix it."
        )
    if "quotaExceeded" in err_str:
        return "⚠️ *YouTube API Quota Exceeded*\nYou must wait until midnight Pacific Time to publish more."
    return f"`{err_str[:300]}`"


# ──────────────────────────────────────────────
# Main Menu
# ──────────────────────────────────────────────

def _main_menu_keyboard():
    """Build the main menu inline keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Publish Videos", callback_data="menu_publish")],
        [InlineKeyboardButton("📊 Channel Status", callback_data="menu_status")],
        [InlineKeyboardButton("👤 Manage Accounts", callback_data="menu_accounts")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start, /menu, /help — show main menu."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    await update.message.reply_text(
        "🤖 *YouTube Auto-Publisher Bot*\n\n"
        "Choose an option below:",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
    )


# ──────────────────────────────────────────────
# Callback Handler (all button taps)
# ──────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button taps."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized")
        return

    await query.answer()
    data = query.data

    # Main menu options
    if data == "menu_publish":
        await _show_publish_picker(query)
    elif data == "menu_status":
        await _show_status_picker(query)
    elif data == "menu_accounts":
        await _show_accounts_menu(query)
    elif data == "menu_help":
        await _show_help(query)
    elif data == "menu_main":
        await query.edit_message_text(
            "🤖 *YouTube Auto-Publisher Bot*\n\nChoose an option below:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(),
        )

    # Publish actions
    elif data == "publish_all":
        await _publish_all(query)
    elif data.startswith("publish_"):
        nickname = data.replace("publish_", "")
        await _publish_account(query, nickname)

    # Status actions
    elif data.startswith("status_"):
        nickname = data.replace("status_", "")
        await _status_account(query, nickname)

    # Re-auth actions
    elif data.startswith("reauth_"):
        nickname = data.replace("reauth_", "")
        await _send_reauth_link(query, nickname)

    # Account management actions
    elif data.startswith("remove_"):
        nickname = data.replace("remove_", "")
        await _remove_account(query, nickname)
    elif data.startswith("confirm_remove_"):
        nickname = data.replace("confirm_remove_", "")
        await _confirm_remove(query, nickname)
    elif data == "cancel_remove":
        await _show_accounts_menu(query)
    elif data == "add_account":
        await add_account_start(update, context)

    # Channel picker (after OAuth, user picks which channel to link)
    elif data.startswith("pick_channel_"):
        channel_id = data.replace("pick_channel_", "", 1)
        await _save_picked_channel(query, channel_id, context)


# ──────────────────────────────────────────────
# Publish Flow
# ──────────────────────────────────────────────

async def _show_publish_picker(query):
    """Show the channel picker for publishing."""
    accounts = account_manager.list_accounts()

    if not accounts:
        await query.edit_message_text(
            "📭 *No accounts connected!*\n\n"
            "Use 👤 Manage Accounts to add your YouTube channel first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Manage Accounts", callback_data="menu_accounts")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
            ]),
        )
        return

    buttons = []
    if len(accounts) > 1:
        buttons.append([InlineKeyboardButton("🌐 ALL Channels", callback_data="publish_all")])

    for nickname, info in accounts.items():
        label = info.get("label", nickname)
        buttons.append([InlineKeyboardButton(f"📺 {label}", callback_data=f"publish_{nickname}")])

    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])

    await query.edit_message_text(
        "🚀 *Publish Videos*\n\nSelect which channel to publish from:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _publish_account(query, nickname: str):
    """Publish private videos from a specific account."""
    accounts = account_manager.list_accounts()
    label = accounts.get(nickname, {}).get("label", nickname)

    await query.edit_message_text(
        f"🔍 *Scanning {label} for private videos...*",
        parse_mode="Markdown",
    )

    try:
        youtube = await asyncio.to_thread(account_manager.get_youtube_service, nickname, CLIENT_SECRET_FILE)
        private_videos = await asyncio.to_thread(youtube_api.get_private_videos, youtube)

        if not private_videos:
            await query.edit_message_text(
                f"📭 *No private videos found on {label}!*\n\n"
                f"All videos are already public or unlisted.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                ]),
            )
            return

        await query.edit_message_text(
            f"📹 Found *{len(private_videos)}* private video(s) on {label}.\n"
            f"⏳ Optimizing and publishing...",
            parse_mode="Markdown",
        )

        # Process each video
        success_count = 0
        for i, video in enumerate(private_videos, 1):
            video_id = video["video_id"]
            original_title = video["title"]
            category_id = video["categoryId"]

            try:
                # Send progress update as new message
                await query.message.reply_text(
                    f"🤖 *[{i}/{len(private_videos)}]* Analyzing: `{original_title}`\n"
                    f"🔍 Searching trending videos in this niche...",
                    parse_mode="Markdown",
                )

                # Step 1: Find trending videos in this niche for context
                trending = await asyncio.to_thread(
                    youtube_api.get_trending_videos, youtube, original_title, max_results=5
                )

                # Step 2: Generate AI metadata with trend context
                optimized = await asyncio.to_thread(
                    gemini_ai.optimize_video_metadata, original_title, trending_videos=trending
                )
                new_title = optimized["title"]
                new_description = optimized["description"]
                new_tags = optimized["tags"]

                # Update metadata
                await asyncio.to_thread(
                    youtube_api.update_video_metadata,
                    youtube, video_id, new_title, new_description, new_tags, category_id,
                )

                # Set public
                await asyncio.to_thread(
                    youtube_api.set_video_public, youtube, video_id
                )

                # Report success
                video_url = f"https://youtu.be/{video_id}"
                tags_str = ", ".join(new_tags[:10])
                if len(new_tags) > 10:
                    tags_str += f" (+{len(new_tags) - 10} more)"

                desc_preview = new_description[:300]
                if len(new_description) > 300:
                    desc_preview += "..."

                trend_note = (
                    f"📈 Analyzed {len(trending)} trending videos"
                    if trending else "⚠️ No trend data (used AI only)"
                )

                await query.message.reply_text(
                    f"✅ *Video Published!*\n\n"
                    f"🔗 *URL:* {video_url}\n\n"
                    f"📝 *Original:*\n`{original_title}`\n\n"
                    f"🚀 *New Title:*\n`{new_title}`\n\n"
                    f"📄 *Description:*\n{desc_preview}\n\n"
                    f"🏷️ *Tags:*\n{tags_str}\n\n"
                    f"{trend_note}",
                    parse_mode="Markdown",
                )
                success_count += 1

            except Exception as e:
                logger.error(f"Error processing video {video_id}: {e}", exc_info=True)
                await query.message.reply_text(
                    f"❌ *Error processing:* `{original_title}`\n"
                    f"{format_error(e)}",
                    parse_mode="Markdown",
                )

        await query.message.reply_text(
            f"🎉 *Done!* Published {success_count}/{len(private_videos)} video(s) on {label}.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )

    except Exception as e:
        logger.error(f"Error publishing from {nickname}: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ *Error:*\n{format_error(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )


async def _publish_all(query):
    """Publish from all connected accounts."""
    accounts = account_manager.list_accounts()

    await query.edit_message_text(
        f"🌐 *Publishing from ALL {len(accounts)} channel(s)...*",
        parse_mode="Markdown",
    )

    total_published = 0
    for nickname in accounts:
        try:
            label = accounts[nickname].get("label", nickname)
            youtube = await asyncio.to_thread(account_manager.get_youtube_service, nickname, CLIENT_SECRET_FILE)
            private_videos = await asyncio.to_thread(youtube_api.get_private_videos, youtube)

            if not private_videos:
                await query.message.reply_text(f"📭 {label}: No private videos found.")
                continue

            await query.message.reply_text(
                f"📹 {label}: Found *{len(private_videos)}* private video(s). Processing...",
                parse_mode="Markdown",
            )

            for video in private_videos:
                try:
                    # Step 1: Find trending videos in this niche for context
                    trending = await asyncio.to_thread(
                        youtube_api.get_trending_videos, youtube, video["title"], max_results=5
                    )

                    # Step 2: Generate AI metadata with trend context
                    optimized = await asyncio.to_thread(
                        youtube_api.generate_ai_metadata,
                        video["title"], trending_videos=trending
                    )
                    await asyncio.to_thread(
                        youtube_api.update_video_metadata,
                        youtube, video["video_id"],
                        optimized["title"], optimized["description"],
                        optimized["tags"], video["categoryId"],
                    )
                    await asyncio.to_thread(
                        youtube_api.set_video_public, youtube, video["video_id"]
                    )

                    video_url = f"https://youtu.be/{video['video_id']}"
                    trend_note = (
                        f"📈 {len(trending)} trends"
                        if trending else "⚠️ No trends"
                    )
                    await query.message.reply_text(
                        f"✅ *Published on {label}:*\n`{optimized['title']}`\n🔗 {video_url}\n{trend_note}",
                        parse_mode="Markdown",
                    )
                    total_published += 1
                except Exception as e:
                    logger.error(f"Error: {e}", exc_info=True)
                    await query.message.reply_text(
                        f"❌ Error on {label}:\n{format_error(e)}",
                        parse_mode="Markdown",
                    )

        except Exception as e:
            logger.error(f"Error with account {nickname}: {e}", exc_info=True)
            await query.message.reply_text(
                f"❌ Error with {nickname}:\n{format_error(e)}",
                parse_mode="Markdown",
            )

    await query.message.reply_text(
        f"🎉 *All done!* Published {total_published} video(s) across all channels.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]),
    )


# ──────────────────────────────────────────────
# Status Flow
# ──────────────────────────────────────────────

async def _show_status_picker(query):
    """Show channel picker for status check."""
    accounts = account_manager.list_accounts()

    if not accounts:
        await query.edit_message_text(
            "📭 *No accounts connected!*\n\n"
            "Use 👤 Manage Accounts to add your YouTube channel first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Manage Accounts", callback_data="menu_accounts")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
            ]),
        )
        return

    buttons = []
    for nickname, info in accounts.items():
        label = info.get("label", nickname)
        buttons.append([InlineKeyboardButton(f"📺 {label}", callback_data=f"status_{nickname}")])

    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])

    await query.edit_message_text(
        "📊 *Channel Status*\n\nSelect a channel:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _status_account(query, nickname: str):
    """Show status for a specific account."""
    accounts = account_manager.list_accounts()
    info = accounts.get(nickname, {})
    label = info.get("label", nickname)

    try:
        youtube = await asyncio.to_thread(account_manager.get_youtube_service, nickname, CLIENT_SECRET_FILE)

        # Get channel stats
        request = youtube.channels().list(part="statistics,snippet", mine=True)
        channels = await asyncio.to_thread(request.execute)
        if not channels.get("items"):
            await query.edit_message_text(f"❌ Could not fetch stats for {label}")
            return

        ch = channels["items"][0]
        stats = ch["statistics"]
        name = ch["snippet"]["title"]

        # Count private videos
        private_videos = await asyncio.to_thread(youtube_api.get_private_videos, youtube)

        await query.edit_message_text(
            f"📊 *Channel: {name}*\n\n"
            f"👥 Subscribers: *{int(stats.get('subscriberCount', 0)):,}*\n"
            f"👁️ Total Views: *{int(stats.get('viewCount', 0)):,}*\n"
            f"📹 Total Videos: *{int(stats.get('videoCount', 0)):,}*\n"
            f"🔒 Private Videos: *{len(private_videos)}*\n\n"
            f"✅ Connection: Working",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Publish This Channel", callback_data=f"publish_{nickname}")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )

    except Exception as e:
        logger.error(f"Error getting status for {nickname}: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ *Error checking {label}:*\n{format_error(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )


# ──────────────────────────────────────────────
# Account Management Flow
# ──────────────────────────────────────────────

async def _show_accounts_menu(query):
    """Show accounts list with add/remove/reauth options."""
    accounts = account_manager.list_accounts()
    is_render = bool(os.getenv("RENDER"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

    if not accounts:
        text = "\U0001f464 *Manage Accounts*\n\nNo accounts connected yet."
    else:
        text = "\U0001f464 *Manage Accounts*\n\n"
        for nickname, info in accounts.items():
            label = info.get("label", nickname)
            channel = info.get("channel_name", "Unknown")
            text += f"\U0001f4fa *{label}* ({channel})\n"

    buttons = []
    for nickname, info in accounts.items():
        label = info.get("label", nickname)
        row = []
        if not is_render:
            row.append(InlineKeyboardButton(f"\u274c Remove {label}", callback_data=f"remove_{nickname}"))
        row.append(InlineKeyboardButton(f"\U0001f501 Re-auth {label}", callback_data=f"reauth_{nickname}"))
        buttons.append(row)

    if not is_render:
        buttons.append([InlineKeyboardButton("\u2795 Add Account", callback_data="add_account")])
    elif not accounts:
        text += "\n_\u2601\ufe0f Running on cloud. Use 🔁 Re-auth to re-authorize existing accounts._"

    buttons.append([InlineKeyboardButton("\u2b05\ufe0f Back", callback_data="menu_main")])

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _send_reauth_link(query, nickname: str):
    """Generate OAuth URL and send it to the user as a clickable link."""
    import oauth_server
    accounts = account_manager.list_accounts()
    label = accounts.get(nickname, {}).get("label", nickname)
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

    if not render_url:
        # Local mode: show instructions
        await query.edit_message_text(
            f"🔁 *Re-auth {label}*\n\n"
            "In local mode, remove the account and re-add it to trigger OAuth.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_accounts")],
            ]),
        )
        return

    reauth_url = f"{render_url}/oauth/start/{nickname}"

    await query.edit_message_text(
        f"🔁 *Re-authorize {label}*\n\n"
        f"Tap the link below to sign in with Google and re-authorize this channel.\n\n"
        f"After you approve access, return here — the bot will confirm automatically.\n\n"
        f"🔗 [Tap here to re-authorize]({reauth_url})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="menu_accounts")],
        ]),
    )


async def _remove_account(query, nickname: str):
    """Confirm account removal."""
    accounts = account_manager.list_accounts()
    label = accounts.get(nickname, {}).get("label", nickname)

    await query.edit_message_text(
        f"⚠️ *Remove {label}?*\n\nThis will delete the saved credentials.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Yes, Remove", callback_data=f"confirm_remove_{nickname}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_remove")],
        ]),
    )


async def _confirm_remove(query, nickname: str):
    """Actually remove the account."""
    accounts = account_manager.list_accounts()
    label = accounts.get(nickname, {}).get("label", nickname)

    success = account_manager.remove_account(nickname)
    if success:
        await query.edit_message_text(
            f"✅ *{label}* has been removed.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Accounts", callback_data="menu_accounts")],
            ]),
        )
    else:
        await query.edit_message_text(f"❌ Failed to remove {label}")


# ──────────────────────────────────────────────
# Add Account Flow (state-based, no ConversationHandler)
# ──────────────────────────────────────────────

async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add account flow from button tap."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized")
        return

    await query.answer()

    # Disable on Render (OAuth needs a local browser)
    if bool(os.getenv("RENDER")):
        await query.edit_message_text(
            "☁️ *Cloud Mode*\n\n"
            "Account management is only available when running the bot locally.\n\n"
            "To add/remove accounts:\n"
            "1. Run the bot on your PC\n"
            "2. Add the account via Telegram\n"
            "3. Run `python export_tokens.py`\n"
            "4. Update the env vars on Render",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
            ]),
        )
        return

    # Set state so MessageHandler knows what to do next
    context.user_data[STATE_KEY] = STATE_WAITING_NICKNAME
    context.user_data.pop("new_account_nickname", None)

    await query.edit_message_text(
        "➕ *Add New Account*\n\n"
        "Enter a short *nickname* for this account (e.g. `gaming`, `cooking`, `vlogs`):\n\n"
        "_Type /cancel to abort._",
        parse_mode="Markdown",
    )


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Top-level message handler. Routes text input based on current state.
    Handles the multi-step add_account flow.
    """
    if not is_authorized(update):
        return

    state = context.user_data.get(STATE_KEY)

    if state == STATE_WAITING_NICKNAME:
        nickname = update.message.text.strip().lower().replace(" ", "_")

        # Basic validation — allow alphanumeric + underscore
        if not all(c.isalnum() or c == "_" for c in nickname) or not nickname:
            await update.message.reply_text(
                "❌ Nickname should only contain letters, numbers, and underscores. Try again:",
            )
            return

        # Check duplicate
        accounts = account_manager.list_accounts()
        if nickname in accounts:
            await update.message.reply_text(
                f"❌ Account '{nickname}' already exists. Try a different name:",
            )
            return

        context.user_data["new_account_nickname"] = nickname
        context.user_data[STATE_KEY] = STATE_WAITING_LABEL

        await update.message.reply_text(
            f"✅ Nickname: `{nickname}`\n\n"
            f"Now enter a *display label* with an emoji\n"
            f"(e.g. `🎮 Gaming`, `🍳 Cooking`, `📱 Tech Reviews`):",
            parse_mode="Markdown",
        )

    elif state == STATE_WAITING_LABEL:
        label = update.message.text.strip()
        nickname = context.user_data.get("new_account_nickname", "unknown")

        # Store label for later (needed when user picks a channel)
        context.user_data["new_account_label"] = label
        # Clear state now — it gets set to STATE_PICKING_CHANNEL if multiple channels
        context.user_data.pop(STATE_KEY, None)

        status_msg = await update.message.reply_text(
            f"🔐 *Opening browser for Google authorization...*\n\n"
            f"• Sign in with the Google account that owns the YouTube channel\n"
            f"• After authorizing, come back here\n\n"
            f"_Waiting for authorization..._",
            parse_mode="Markdown",
        )

        try:
            # ── Run OAuth in a thread executor so the bot stays responsive ──
            loop = asyncio.get_event_loop()
            creds = await loop.run_in_executor(
                None, account_manager.run_oauth_flow, CLIENT_SECRET_FILE
            )

            # ── List ALL channels on this Google account ──
            channels = account_manager.list_channels_for_creds(creds)

            if not channels:
                await status_msg.delete()
                await update.message.reply_text(
                    "❌ *No YouTube channels found on that Google account.*\n\n"
                    "Make sure the account has at least one YouTube channel.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                    ]),
                )
                return

            await status_msg.delete()

            if len(channels) == 1:
                # Only one channel — save directly, no picker needed
                ch = channels[0]
                account_manager.save_account(nickname, label, ch["id"], ch["name"], creds)
                await update.message.reply_text(
                    f"✅ *Account Connected!*\n\n"
                    f"📺 *{label}*\n"
                    f"📡 Channel: {ch['name']}\n\n"
                    f"You can now publish from this channel!",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                    ]),
                )

            else:
                # Multiple channels — show picker
                # Store creds as JSON string temporarily in user_data
                context.user_data["pending_creds_json"] = creds.to_json()
                context.user_data["pending_nickname"] = nickname
                context.user_data["pending_label"] = label
                context.user_data["pending_channels"] = {ch["id"]: ch["name"] for ch in channels}
                context.user_data[STATE_KEY] = STATE_PICKING_CHANNEL

                buttons = [
                    [InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"pick_channel_{ch['id']}")]
                    for ch in channels
                ]
                buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_main")])

                await update.message.reply_text(
                    f"🎉 Found *{len(channels)} YouTube channels* on this account!\n\n"
                    f"Which channel do you want to link as *{label}*?",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

        except Exception as e:
            logger.error(f"Error in add account flow: {e}", exc_info=True)
            try:
                await status_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(
                f"❌ *Error during authorization:*\n{format_error(e)}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                ]),
            )

    # If no active state, ignore the message silently


async def _save_picked_channel(query, channel_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Called when user taps a channel button from the picker after OAuth."""
    await query.answer()

    creds_json = context.user_data.get("pending_creds_json")
    nickname = context.user_data.get("pending_nickname", "unknown")
    label = context.user_data.get("pending_label", "Unknown")
    channels = context.user_data.get("pending_channels", {})
    channel_name = channels.get(channel_id, "Unknown Channel")

    if not creds_json:
        await query.edit_message_text(
            "❌ Session expired. Please start the add account flow again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )
        return

    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(
            info=__import__("json").loads(creds_json),
            scopes=account_manager.SCOPES,
        )
        account_manager.save_account(nickname, label, channel_id, channel_name, creds)

        # Clean up temp data
        context.user_data.pop("pending_creds_json", None)
        context.user_data.pop("pending_nickname", None)
        context.user_data.pop("pending_label", None)
        context.user_data.pop("pending_channels", None)
        context.user_data.pop(STATE_KEY, None)

        await query.edit_message_text(
            f"✅ *Channel Linked!*\n\n"
            f"📺 *{label}*\n"
            f"📡 Channel: {channel_name}\n\n"
            f"You can now publish from this channel!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )

    except Exception as e:
        logger.error(f"Error saving picked channel: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ *Error saving channel:*\n{format_error(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]),
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing input flow."""
    if not is_authorized(update):
        return
    context.user_data.pop(STATE_KEY, None)
    context.user_data.pop("new_account_nickname", None)
    context.user_data.pop("new_account_label", None)
    context.user_data.pop("pending_creds_json", None)
    context.user_data.pop("pending_nickname", None)
    context.user_data.pop("pending_label", None)
    context.user_data.pop("pending_channels", None)
    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]),
    )


# ──────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────

async def _show_help(query):
    """Show help text."""
    await query.edit_message_text(
        "❓ *Help — YouTube Auto-Publisher Bot*\n\n"
        "This bot automatically optimizes and publishes your private YouTube videos.\n\n"
        "*How it works:*\n"
        "1️⃣ Upload a video as *Private* on YouTube\n"
        "2️⃣ Tap 🚀 *Publish Videos*\n"
        "3️⃣ Bot uses AI to generate an optimized title, description, and tags\n"
        "4️⃣ Video is updated and set to *Public*\n\n"
        "*Commands:*\n"
        "• /start — Main menu\n"
        "• /publish — Quick publish\n"
        "• /status — Quick status check\n"
        "• /accounts — Manage accounts\n\n"
        "*Multi-Account:*\n"
        "You can connect multiple YouTube channels from different Google accounts.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]),
    )


# ──────────────────────────────────────────────
# Quick commands (shortcuts)
# ──────────────────────────────────────────────

async def cmd_publish_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /publish — quick publish shortcut."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    accounts = account_manager.list_accounts()
    if not accounts:
        await update.message.reply_text(
            "📭 No accounts connected! Use /start to set up.",
        )
        return

    if len(accounts) == 1:
        # Single account — publish directly
        nickname = list(accounts.keys())[0]
        label = accounts[nickname].get("label", nickname)
        await update.message.reply_text(
            f"🔍 *Scanning {label} for private videos...*",
            parse_mode="Markdown",
        )

        try:
            youtube = await asyncio.to_thread(account_manager.get_youtube_service, nickname, CLIENT_SECRET_FILE)
            private_videos = await asyncio.to_thread(youtube_api.get_private_videos, youtube)

            if not private_videos:
                await update.message.reply_text(
                    f"📭 No private videos found on {label}!",
                    parse_mode="Markdown",
                )
                return

            await update.message.reply_text(
                f"📹 Found *{len(private_videos)}* private video(s). Processing...",
                parse_mode="Markdown",
            )

            for i, video in enumerate(private_videos, 1):
                try:
                    await update.message.reply_text(
                        f"🤖 *[{i}/{len(private_videos)}]* Analyzing: `{video['title']}`\n"
                        f"🔍 Searching trending videos in this niche...",
                        parse_mode="Markdown",
                    )
                    # Step 1: Find trending videos in this niche for context
                    trending = await asyncio.to_thread(
                        youtube_api.get_trending_videos, youtube, video["title"], max_results=5
                    )

                    # Step 2: Generate AI metadata with trend context
                    optimized = await asyncio.to_thread(
                        gemini_ai.optimize_video_metadata, video["title"], trending_videos=trending
                    )

                    # Step 3: Apply metadata and publish
                    await asyncio.to_thread(
                        youtube_api.update_video_metadata,
                        youtube, video["video_id"],
                        optimized["title"], optimized["description"],
                        optimized["tags"], video["categoryId"],
                    )
                    await asyncio.to_thread(
                        youtube_api.set_video_public, youtube, video["video_id"]
                    )

                    video_url = f"https://youtu.be/{video['video_id']}"
                    tags_str = ", ".join(optimized["tags"][:8])
                    trend_note = (
                        f"📈 Analyzed {len(trending)} trending videos"
                        if trending else "⚠️ No trend data (used AI only)"
                    )

                    await update.message.reply_text(
                        f"✅ *Published!*\n\n"
                        f"🔗 {video_url}\n"
                        f"🚀 `{optimized['title']}`\n"
                        f"🏷️ {tags_str}\n"
                        f"{trend_note}",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"Error: {e}", exc_info=True)
                    await update.message.reply_text(f"❌ Error:\n{format_error(e)}", parse_mode="Markdown")

            await update.message.reply_text(f"🎉 Done! Processed {len(private_videos)} video(s).")

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Error:\n{format_error(e)}", parse_mode="Markdown")
    else:
        # Multiple accounts — show picker
        buttons = [
            [InlineKeyboardButton("🌐 ALL Channels", callback_data="publish_all")]
        ]
        for nick, info in accounts.items():
            buttons.append([InlineKeyboardButton(
                f"📺 {info.get('label', nick)}", callback_data=f"publish_{nick}"
            )])

        await update.message.reply_text(
            "🚀 *Select channel to publish:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def cmd_status_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — quick status shortcut."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    accounts = account_manager.list_accounts()
    if not accounts:
        await update.message.reply_text("📭 No accounts connected! Use /start to set up.")
        return

    # Show picker
    buttons = []
    for nick, info in accounts.items():
        buttons.append([InlineKeyboardButton(
            f"📺 {info.get('label', nick)}", callback_data=f"status_{nick}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")])

    await update.message.reply_text(
        "📊 *Select channel:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_accounts_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /accounts — quick accounts shortcut."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    is_render = bool(os.getenv("RENDER"))
    accounts = account_manager.list_accounts()

    if not accounts:
        text = "👤 *Manage Accounts*\n\nNo accounts connected yet."
    else:
        text = "👤 *Manage Accounts*\n\n"
        for nickname, info in accounts.items():
            text += f"📺 *{info.get('label', nickname)}* ({info.get('channel_name', 'Unknown')})\n"

    if is_render:
        text += "\n_☁️ Running on cloud. Account management is only available locally._"

    buttons = []
    if not is_render:
        for nickname, info in accounts.items():
            buttons.append([InlineKeyboardButton(
                f"❌ Remove {info.get('label', nickname)}", callback_data=f"remove_{nickname}"
            )])
        buttons.append([InlineKeyboardButton("➕ Add Account", callback_data="add_account")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ──────────────────────────────────────────────
# App Builder
# ──────────────────────────────────────────────

def create_app(bot_token: str) -> Application:
    """Create and configure the Telegram bot application."""
    app = Application.builder().token(bot_token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("publish", cmd_publish_quick))
    app.add_handler(CommandHandler("status", cmd_status_quick))
    app.add_handler(CommandHandler("accounts", cmd_accounts_quick))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callback handler for all button taps (including add_account_start)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Top-level message handler for multi-step flows (add account)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    logger.info("Telegram bot created with interactive menu system")
    return app
