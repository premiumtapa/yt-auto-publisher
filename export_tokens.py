"""
Export Tokens → Render Environment Variables
=============================================

Run this LOCALLY (on your PC) to generate all the environment variable
values you need to paste into Render's dashboard.

Usage:
    python export_tokens.py

It reads your local files and prints the env vars you need to set on Render.
"""

import os
import json
import sys


def main():
    print("=" * 60)
    print("  RENDER ENVIRONMENT VARIABLES EXPORT")
    print("  Copy each value below into Render's Environment Variables")
    print("=" * 60)
    print()

    errors = []

    # ── 1. Read .env file for API keys ──
    env_vars = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip()

    # ── 2. Core env vars ──
    print("━" * 60)
    print("STEP 1: Core Environment Variables")
    print("━" * 60)

    core_vars = {
        "TELEGRAM_BOT_TOKEN": env_vars.get("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_USER_IDS": env_vars.get("TELEGRAM_USER_IDS", env_vars.get("TELEGRAM_USER_ID", "")),
        "GEMINI_API_KEY": env_vars.get("GEMINI_API_KEY", ""),
        "RENDER": "true",
    }

    for key, val in core_vars.items():
        if val:
            print(f"\n  Key:   {key}")
            print(f"  Value: {val}")
        else:
            errors.append(f"Missing: {key} (not found in .env)")

    # ── 3. Accounts JSON ──
    print()
    print("━" * 60)
    print("STEP 2: Accounts Data")
    print("━" * 60)

    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            accounts_data = f.read()
        print(f"\n  Key:   ACCOUNTS_JSON")
        print(f"  Value: {accounts_data.strip()}")
    else:
        errors.append("Missing: accounts.json file")

    # ── 4. YouTube OAuth Tokens ──
    print()
    print("━" * 60)
    print("STEP 3: YouTube OAuth Tokens (one per channel)")
    print("━" * 60)

    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            acct_data = json.load(f)
        for nickname, info in acct_data.get("accounts", {}).items():
            token_file = info.get("token_file", "")
            env_key = f"YT_TOKEN_{nickname.upper()}"
            if token_file and os.path.exists(token_file):
                with open(token_file, "r") as f:
                    token_json = f.read().strip()
                print(f"\n  Key:   {env_key}")
                print(f"  Value: {token_json}")
            else:
                errors.append(f"Missing token file: {token_file} for account '{nickname}'")

    # ── 5. Client Secret ──
    print()
    print("━" * 60)
    print("STEP 4: Google Client Secret")
    print("━" * 60)

    cs_file = env_vars.get("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
    if os.path.exists(cs_file):
        with open(cs_file, "r") as f:
            cs_data = f.read().strip()
        print(f"\n  Key:   CLIENT_SECRET_JSON")
        print(f"  Value: {cs_data}")
    else:
        errors.append(f"Missing: {cs_file}")

    # ── Summary ──
    print()
    print("━" * 60)
    if errors:
        print("⚠️  WARNINGS:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("✅ All environment variables exported successfully!")
    print("━" * 60)

    # Count total vars
    total = 4  # core vars
    if os.path.exists("accounts.json"):
        total += 1  # ACCOUNTS_JSON
        with open("accounts.json", "r") as f:
            acct_data = json.load(f)
        total += len(acct_data.get("accounts", {}))  # YT_TOKEN_*
    if os.path.exists(cs_file):
        total += 1  # CLIENT_SECRET_JSON

    print(f"\nTotal environment variables to set on Render: {total}")
    print()
    print("📋 Instructions:")
    print("  1. Go to your Render service → Environment tab")
    print("  2. Add each Key/Value pair shown above")
    print("  3. Click 'Save Changes' → Render will auto-redeploy")
    print()


if __name__ == "__main__":
    main()
