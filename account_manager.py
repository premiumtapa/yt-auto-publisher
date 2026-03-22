"""
Account Manager
Handles multiple YouTube accounts — each identified by a nickname.
Stores account metadata in accounts.json and OAuth tokens in tokens/ directory.
On Render: reads from environment variables (ACCOUNTS_JSON, YT_TOKEN_*).
"""

import os
import json
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
ACCOUNTS_FILE = "accounts.json"
TOKENS_DIR = "tokens"


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


def get_youtube_service(nickname: str, client_secret_file: str = "client_secret.json"):
    """
    Get an authenticated YouTube service for a specific account.
    Refreshes expired tokens automatically.
    On Render: reads token from YT_TOKEN_<NICKNAME> env var.
    """
    accounts = _load_accounts()
    if nickname not in accounts:
        raise ValueError(f"Account not found: {nickname}")

    if _is_render():
        # Load token from environment variable
        env_key = f"YT_TOKEN_{nickname.upper()}"
        token_json = os.getenv(env_key)
        if not token_json:
            raise ValueError(f"Environment variable {env_key} not set on Render")
        token_data = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    else:
        # Load token from file (local mode)
        token_file = accounts[nickname]["token_file"]
        if not os.path.exists(token_file):
            raise FileNotFoundError(f"Token file not found for {nickname}: {token_file}")
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info(f"Refreshing token for account: {nickname}")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Failed to refresh token for {nickname}: {e}")
                raise RuntimeError(
                    f"Token for '{nickname}' has expired or been revoked. "
                    f"Please go to Accounts -> Remove Account and re-add it."
                ) from e
            # Save refreshed token back to file (only in local mode)
            if not _is_render():
                token_file = accounts[nickname]["token_file"]
                with open(token_file, "w") as tf:
                    tf.write(creds.to_json())
        else:
            raise RuntimeError(
                f"Token for '{nickname}' is invalid and can't be refreshed. "
                f"Please remove and re-add the account."
            )

    return build("youtube", "v3", credentials=creds)


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
