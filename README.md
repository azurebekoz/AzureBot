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

For Instagram stories or private content, export browser cookies and place them in `cookies.txt`.
