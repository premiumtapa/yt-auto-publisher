"""
Account Manager
Handles multiple YouTube accounts — each identified by a nickname.
Stores account metadata in accounts.json and OAuth tokens in tokens/ directory.
On Render: reads from environment variables (ACCOUNTS_JSON, YT_TOKEN_*).
"""

import os
import json
import logging
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
ACCOUNTS_FILE = "accounts.json"
TOKENS_DIR = "tokens"

# ── In-memory creds cache (survives until process restart) ────────────────────
# Populated after re-auth so the bot works immediately without env var updates.
_live_creds: dict[str, Credentials] = {}

# ── Pending OAuth flows keyed by nickname ─────────────────────────────────────
# Stored between /oauth/start (URL generation) and /oauth/callback (code exchange).
_pending_flows: dict[str, object] = {}


def _is_render() -> bool:
    """Check if we're running on Render."""
    return bool(os.getenv("RENDER"))


def _load_accounts() -> dict:
    """Load accounts from disk or from ACCOUNTS_JSON env var on Render."""
    if _is_render():
        raw = os.getenv("ACCOUNTS_JSON", "{}")
        try:
            data = json.loads(raw)
            return data.get("accounts", {})
        except json.JSONDecodeError:
            logger.error("ACCOUNTS_JSON env var contains invalid JSON")
            return {}
    if not os.path.exists(ACCOUNTS_FILE):
        return {}
    with open(ACCOUNTS_FILE, "r") as f:
        data = json.load(f)
    return data.get("accounts", {})


def _save_accounts(accounts: dict):
    """Save accounts to disk."""
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump({"accounts": accounts}, f, indent=2)


def list_accounts() -> dict:
    """Return all saved accounts. Key = nickname, value = {label, token_file}."""
    return _load_accounts()


# ──────────────────────────────────────────────
# OAuth helpers (called via run_in_executor to avoid blocking asyncio)
# ──────────────────────────────────────────────

def run_oauth_flow(client_secret_file: str = "client_secret.json") -> Credentials:
    """
    Run the Google OAuth2 flow (blocking — must be called in a thread executor).
    Opens a browser window for the user to authorize.
    Returns a Credentials object.
    """
    if not os.path.exists(client_secret_file):
        raise FileNotFoundError(f"Client secret file not found: {client_secret_file}")
    logger.info("Starting OAuth flow (browser will open)...")
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0)
    logger.info("OAuth flow completed successfully")
    return creds


def list_channels_for_creds(creds: Credentials) -> list:
    """
    List ALL YouTube channels accessible with given credentials.
    A single Google account can manage multiple YouTube channels (brand accounts).
    Returns a list of dicts: [{id, name}, ...]
    """
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(
        part="snippet,id",
        mine=True,
        maxResults=50,
    ).execute()

    channels = []
    for item in response.get("items", []):
        channels.append({
            "id": item["id"],
            "name": item["snippet"]["title"],
        })

    logger.info(f"Found {len(channels)} channel(s) for credentials")
    return channels


def save_account(
    nickname: str,
    label: str,
    channel_id: str,
    channel_name: str,
    creds: Credentials,
) -> dict:
    """
    Save an account entry with a specific channel's credentials.
    Called after the user picks which channel to link.
    """
    os.makedirs(TOKENS_DIR, exist_ok=True)
    token_file = os.path.join(TOKENS_DIR, f"{nickname}.json")

    with open(token_file, "w") as f:
        f.write(creds.to_json())

    account = {
        "label": label,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "token_file": token_file,
    }

    accounts = _load_accounts()
    accounts[nickname] = account
    _save_accounts(accounts)

    logger.info(f"Saved account '{nickname}' → {channel_name} ({channel_id})")
    return account


def add_account(nickname: str, label: str, client_secret_file: str = "client_secret.json") -> list:
    """
    Legacy single-call flow: run OAuth, list channels, return them.
    Returns list of channels found — caller decides what to do if multiple.
    Use run_oauth_flow() + list_channels_for_creds() + save_account() for async usage.
    """
    creds = run_oauth_flow(client_secret_file)
    channels = list_channels_for_creds(creds)
    return creds, channels


def remove_account(nickname: str) -> bool:
    """Remove an account and delete its token file."""
    accounts = _load_accounts()
    if nickname not in accounts:
        return False

    token_file = accounts[nickname].get("token_file", "")
    if token_file and os.path.exists(token_file):
        os.remove(token_file)
        logger.info(f"Deleted token file: {token_file}")

    del accounts[nickname]
    _save_accounts(accounts)
    logger.info(f"Account '{nickname}' removed")
    return True


# ── In-memory / re-auth helpers ──────────────────────────────────────────────

def save_creds_in_memory(nickname: str, creds: Credentials):
    """Store credentials in the live cache so re-auth works immediately."""
    _live_creds[nickname] = creds
    logger.info(f"Stored live credentials for '{nickname}' in memory")


def mark_authorized(nickname: str):
    """Record the current UTC time as when this account was last authorized."""
    accounts = _load_accounts()
    if nickname in accounts:
        accounts[nickname]["authorized_at"] = datetime.now(timezone.utc).isoformat()
        if not _is_render():
            _save_accounts(accounts)
        logger.info(f"Marked '{nickname}' as authorized at {accounts[nickname]['authorized_at']}")


def get_oauth_url(nickname: str, redirect_uri: str, client_secret_file: str = "client_secret.json") -> str:
    """
    Generate a Google OAuth2 authorization URL for the given account nickname.
    Stores the Flow object in _pending_flows so the callback can exchange the code.
    Returns the URL string the user should visit.
    """
    if not os.path.exists(client_secret_file):
        raise FileNotFoundError(f"Client secret file not found: {client_secret_file}")

    flow = Flow.from_client_secrets_file(
        client_secret_file,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    # Use nickname as OAuth state so we can match it on callback
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",   # force refresh_token to be returned
        state=nickname,
    )
    _pending_flows[nickname] = flow
    logger.info(f"Generated OAuth URL for '{nickname}': {auth_url[:80]}...")
    return auth_url


def exchange_code_for_token(
    code: str,
    nickname: str,
    redirect_uri: str,
    client_secret_file: str = "client_secret.json",
) -> Credentials:
    """
    Exchange the OAuth authorization code for credentials.
    Uses the pending Flow stored by get_oauth_url, or creates a new one as fallback.
    """
    flow = _pending_flows.pop(nickname, None)
    if flow is None:
        # Fallback: recreate flow (may happen if server restarted between start and callback)
        logger.warning(f"No pending flow for '{nickname}', recreating from client secret")
        flow = Flow.from_client_secrets_file(
            client_secret_file,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
            state=nickname,
        )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    creds = flow.credentials
    logger.info(f"Token exchange successful for '{nickname}'")
    return creds


# ── YouTube service factory ───────────────────────────────────────────────────

def get_youtube_service(nickname: str, client_secret_file: str = "client_secret.json"):
    """
    Get an authenticated YouTube service for a specific account.
    Priority:
      1. In-memory cache (_live_creds) — set after a successful in-bot re-auth
      2. Environment variable YT_TOKEN_<NICKNAME> (Render mode)
      3. Token file tokens/<nickname>.json (local mode)
    Refreshes expired access tokens automatically.
    Raises RuntimeError with a user-friendly message when the refresh token has expired.
    """
    accounts = _load_accounts()
    if nickname not in accounts:
        raise ValueError(f"Account not found: {nickname}")

    # 1. Check in-memory cache first (populated after in-bot re-auth)
    if nickname in _live_creds:
        creds = _live_creds[nickname]
        if creds.valid:
            return build("youtube", "v3", credentials=creds)
        # Try refreshing cached creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                return build("youtube", "v3", credentials=creds)
            except Exception:
                # Cached creds failed — fall through to env var / file
                del _live_creds[nickname]

    # 2. Load from env var (Render) or file (local)
    if _is_render():
        env_key = f"YT_TOKEN_{nickname.upper()}"
        token_json = os.getenv(env_key)
        if not token_json:
            raise ValueError(
                f"invalid_grant: No token for '{nickname}' found. "
                f"Please tap 🔁 Re-auth to authorize this account."
            )
        token_data = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    else:
        token_file = accounts[nickname]["token_file"]
        if not os.path.exists(token_file):
            raise FileNotFoundError(f"Token file not found for {nickname}: {token_file}")
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    # 3. Refresh if needed
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info(f"Refreshing token for account: {nickname}")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Failed to refresh token for {nickname}: {e}")
                raise RuntimeError(
                    f"invalid_grant: Token for '{nickname}' has expired or been revoked. "
                    f"Please tap 🔁 Re-auth in Manage Accounts to re-authorize."
                ) from e

            # Persist refreshed token (local only)
            if not _is_render():
                token_file = accounts[nickname]["token_file"]
                with open(token_file, "w") as tf:
                    tf.write(creds.to_json())
            else:
                logger.warning(
                    f"[RENDER] Token for '{nickname}' refreshed in-memory. "
                    f"Update YT_TOKEN_{nickname.upper()} on Render after next re-auth "
                    f"to avoid needing re-auth on restart."
                )
        else:
            raise RuntimeError(
                f"invalid_grant: Token for '{nickname}' is invalid. "
                f"Please tap 🔁 Re-auth in Manage Accounts."
            )

    # Cache refreshed creds for next call
    _live_creds[nickname] = creds
    return build("youtube", "v3", credentials=creds)


def is_authorized(nickname: str) -> bool:
    """
    Return True if this account currently has valid (or refreshable) credentials.
    - On Render: True if in-memory cache is valid OR the env var token is present and not expired.
    - Locally: True if the token file exists.
    This is a lightweight check — it does NOT make a network call.
    """
    # In-memory live creds (set after successful re-auth) take priority
    if nickname in _live_creds:
        creds = _live_creds[nickname]
        if creds.valid or (creds.expired and creds.refresh_token):
            return True

    if _is_render():
        env_key = f"YT_TOKEN_{nickname.upper()}"
        token_json = os.getenv(env_key)
        if not token_json:
            return False
        try:
            data = json.loads(token_json)
            # If there's a refresh_token it can stay alive
            return bool(data.get("refresh_token") or data.get("token"))
        except Exception:
            return False
    else:
        accounts = _load_accounts()
        token_file = accounts.get(nickname, {}).get("token_file", "")
        return os.path.exists(token_file)


def migrate_existing_token():
    """
    Migrate the old single-account token.json to the new multi-account system.
    If token.json exists, we create entries for all channels it has access to.
    """
    old_token = "token.json"
    if not os.path.exists(old_token):
        return

    logger.info("Migrating existing token.json to multi-account system...")
    os.makedirs(TOKENS_DIR, exist_ok=True)

    try:
        creds = Credentials.from_authorized_user_file(old_token, SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token locally so we copy the fresh one
            with open(old_token, "w") as f:
                f.write(creds.to_json())

        channels = list_channels_for_creds(creds)
        if not channels:
            logger.warning("No channels found for existing token.json")
            os.rename(old_token, old_token + ".migrated")
            return

        accounts = _load_accounts()
        for idx, chan in enumerate(channels):
            # create safe nickname from channel name
            safe_name = "".join(c for c in chan["name"] if c.isalnum()).lower()
            if not safe_name: safe_name = f"channel_{idx}"

            # ensure nickname is unique
            nickname = safe_name
            counter = 1
            while nickname in accounts:
                nickname = f"{safe_name}{counter}"
                counter += 1

            new_token = os.path.join(TOKENS_DIR, f"{nickname}.json")
            with open(old_token, "r") as f:
                token_data = f.read()
            with open(new_token, "w") as f:
                f.write(token_data)

            accounts[nickname] = {
                "label": chan["name"],
                "channel_name": chan["name"],
                "channel_id": chan["id"],
                "token_file": new_token,
            }
            logger.info(f"Migrated channel {chan['name']} as account '{nickname}'")

        _save_accounts(accounts)
        os.rename(old_token, old_token + ".migrated")
        logger.info("Migration complete.")

    except Exception as e:
        logger.error(f"Failed to migrate token.json: {e}")
