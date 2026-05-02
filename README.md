# Azure Music Bot

Telegram bot for downloading media from YouTube, Spotify, TikTok, Instagram, and Twitter/X links.

## Setup

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.example` and set your Telegram bot token:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

Run the bot:

```powershell
python bot.py
```

## Server Deployment

On Render, Railway, Fly.io, Docker, or any other server, `.env` is usually not uploaded.
Add this environment variable in the hosting dashboard:

```env
TELEGRAM_BOT_TOKEN=your_real_bot_token
```

`BOT_TOKEN` and `TELEGRAM_TOKEN` are also supported as fallback names.

For Instagram stories, private content, or YouTube "Sign in to confirm you're not a bot" errors,
export browser cookies and place them in `cookies.txt`.

On a server, upload the cookie file with your app and optionally set:

```env
YTDLP_COOKIE_FILE=cookies.txt
```

If the host does not support file uploads, put the cookie file content in an environment variable instead.
Recommended for Railway:

```env
YTDLP_COOKIES_B64=base64_encoded_cookies_txt
```

Create the value on Windows PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))
```

The bot will create `/tmp/cookies.txt` automatically at startup.
