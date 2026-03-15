# 🚀 Deploy YouTube Auto-Publisher Bot to Render.com (Free Tier)

This guide walks you through deploying your bot to Render.com with UptimeRobot to keep it alive 24/7.

---

## Overview

| What | Details |
|------|---------|
| **Hosting** | Render.com (Free Web Service) |
| **Keep-Alive** | UptimeRobot.com (Free plan) |
| **Cost** | $0/month |
| **Bot Mode** | Webhook (not polling) |
| **Tokens** | Stored as environment variables |

---

## Part 1: Prepare Your Code (On Your PC)

### Step 1: Export Your Environment Variables

Run this command in your project folder (where `main.py` is):

```bash
python export_tokens.py
```

This will print out all the environment variables you need. **Copy the output somewhere safe** (e.g., a Notepad window). You'll paste each Key/Value pair into Render later.

**How to test:** The script should print STEP 1 through STEP 4 without errors. At the end it should say "✅ All environment variables exported successfully!"

### Step 2: Create a GitHub Repository

1. Go to [github.com](https://github.com) → Click **"+" → "New repository"**
2. Name it something like `yt-auto-publisher`
3. Set it to **Private**
4. Click **"Create repository"**

Now push your code. Open a terminal in your project folder:

```bash
git init
git add *.py Dockerfile requirements.txt
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/yt-auto-publisher.git
git push -u origin main
```

> ⚠️ **Do NOT push** `.env`, `tokens/`, `client_secret.json`, or `accounts.json` — these contain secrets. Only push `.py` files, `Dockerfile`, and `requirements.txt`.

**How to test:** Go to your GitHub repo page → you should see `main.py`, `Dockerfile`, and other `.py` files. You should NOT see `.env` or `tokens/` folder.

---

## Part 2: Deploy on Render.com

### Step 3: Create a Render Account

1. Go to [render.com](https://render.com)
2. Click **"Get Started for Free"**
3. Sign up with your **GitHub account** (this makes Step 4 easier)

### Step 4: Create a New Web Service

1. From the Render Dashboard, click **"New +"** → **"Web Service"**
2. Connect your GitHub account if not already connected
3. Find and select your `yt-auto-publisher` repository
4. Fill in the settings:

| Setting | Value |
|---------|-------|
| **Name** | `yt-auto-publisher` (or anything you like) |
| **Region** | Pick the closest to you (e.g., Singapore) |
| **Branch** | `main` |
| **Runtime** | `Docker` |
| **Instance Type** | **Free** |

5. Click **"Create Web Service"** (don't deploy yet — we need to add env vars first)

**How to test:** You should see your service page on Render with status "Creating..."

### Step 5: Add Environment Variables

1. On your service page, click the **"Environment"** tab (left sidebar)
2. Click **"Add Environment Variable"**
3. Add each variable from the `export_tokens.py` output (from Step 1):

| Key | Where to find the Value |
|-----|------------------------|
| `RENDER` | `true` |
| `TELEGRAM_BOT_TOKEN` | From export_tokens.py output |
| `TELEGRAM_USER_IDS` | From export_tokens.py output |
| `GEMINI_API_KEY` | From export_tokens.py output |
| `ACCOUNTS_JSON` | From export_tokens.py output (the entire JSON string) |
| `CLIENT_SECRET_JSON` | From export_tokens.py output (the entire JSON string) |
| `YT_TOKEN_DEFAULT` | From export_tokens.py output |
| `YT_TOKEN_TRENDINGTOP10` | From export_tokens.py output |
| `YT_TOKEN_HINDU_REELS_VIDEO` | From export_tokens.py output |
| `YT_TOKEN_FLOOR_ELITE` | From export_tokens.py output |

> 💡 **Tip:** For the JSON values (`ACCOUNTS_JSON`, `CLIENT_SECRET_JSON`, `YT_TOKEN_*`), paste the **entire JSON string** as-is. Don't add quotes around it.

4. Click **"Save Changes"**

**How to test:** Count the variables — you should have exactly **10** environment variables listed.

### Step 6: Deploy

1. After saving environment variables, Render will auto-deploy
2. Or click **"Manual Deploy"** → **"Deploy latest commit"**
3. Watch the **Logs** tab — you should see:
   ```
   YouTube Auto-Publisher Bot Starting...
   Mode: RENDER (webhook)
   Webhook mode on port 10000
   Bot is running in webhook mode!
   ```

**How to test:** 
- Open Telegram → send `/start` to your bot
- You should see the main menu with buttons
- Tap "📊 Channel Status" → should show your 4 channels
- Tap "👤 Manage Accounts" → should show accounts WITHOUT add/remove buttons, with a message "☁️ Running on cloud..."

### Common Errors and Fixes

| Error in Logs | Fix |
|--------------|-----|
| `Missing required environment variables: TELEGRAM_BOT_TOKEN` | You forgot to add it in Step 5 |
| `RENDER_EXTERNAL_URL not set` | This is auto-set by Render — wait for deploy to complete |
| `YT_TOKEN_DEFAULT not set on Render` | Add the missing token env var |
| `ACCOUNTS_JSON env var contains invalid JSON` | Check you pasted the JSON correctly (no extra quotes) |

---

## Part 3: Set Up UptimeRobot (Keep Bot Alive)

Render's free tier puts your service to sleep after 15 minutes of no traffic. UptimeRobot pings your URL every 5 minutes to keep it awake.

### Step 7: Create an UptimeRobot Account

1. Go to [uptimerobot.com](https://uptimerobot.com)
2. Click **"Register for FREE"**
3. Enter your email and create a password
4. Verify your email (check inbox)

### Step 8: Create a Monitor

1. After logging in, click **"+ Add New Monitor"**
2. Fill in:

| Setting | Value |
|---------|-------|
| **Monitor Type** | `HTTP(s)` |
| **Friendly Name** | `YT Bot` (or whatever you like) |
| **URL (or IP)** | Your Render URL (find it on Render dashboard, e.g., `https://yt-auto-publisher.onrender.com`) |
| **Monitoring Interval** | `5 minutes` (free plan default) |

3. Click **"Create Monitor"**

**How to test:**
- Wait 5 minutes
- The monitor should show **green "Up"** status
- If it shows **red "Down"**, check:
  - Is the URL correct? (copy it from Render dashboard)
  - Is the Render service deployed successfully? (check Render logs)

### Step 9: Verify Everything Works End-to-End

Do this final checklist:

| Test | How | Expected Result |
|------|-----|----------------|
| Bot responds | Send `/start` in Telegram | Main menu appears with buttons |
| Channel status works | Tap "📊 Channel Status" | Shows all 4 channels |
| Publishing works | Tap "🚀 Publish Videos" → pick a channel | Scans and publishes private videos |
| Cloud mode active | Tap "👤 Manage Accounts" | Shows accounts but NO add/remove buttons |
| UptimeRobot is pinging | Check UptimeRobot dashboard | Shows "Up" with green dot |
| Bot survives sleep | Wait 20+ minutes, then `/start` | Bot responds (may take 30-50 seconds for first response after cold start) |

---

## FAQ

### How do I add a new YouTube channel after deploying?

1. Run the bot **locally** on your PC (`python main.py`)
2. In Telegram, tap "👤 Manage Accounts" → "➕ Add Account"
3. Complete the OAuth flow
4. Run `python export_tokens.py` to get the new env vars
5. Go to Render → Environment → add/update the new `YT_TOKEN_*` variable
6. Render will auto-redeploy

### Will my tokens expire?

OAuth refresh tokens are long-lived. The bot auto-refreshes access tokens. On Render, refreshed tokens are held in memory during the session. If the service restarts (which is normal), it uses the stored refresh token from the env var to get a new access token.

### What if Render changes their free tier?

Your code still works 100% locally. Just run `python main.py` on your PC as before. The Render/webhook code only activates when the `RENDER` env var is set.

### The bot is slow to respond after being idle?

This is normal. Render free tier "spins down" after inactivity. Even with UptimeRobot pinging every 5 minutes, the first real user request after a sleep period may take 30-50 seconds. Subsequent requests are fast.

### How do I update the bot code?

1. Make changes to code files on your PC
2. Push to GitHub: `git add . && git commit -m "update" && git push`
3. Render auto-deploys from GitHub within ~2 minutes
