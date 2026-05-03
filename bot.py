import base64
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

import requests
import telebot
import yt_dlp
from telebot import types


def load_env_file(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

if not TOKEN:
    raise RuntimeError(
        "Telegram bot token is not set. Add TELEGRAM_BOT_TOKEN in your server environment variables."
    )

bot = telebot.TeleBot(TOKEN)
DOWNLOADS_DIR = Path("downloads")
PENDING_DOWNLOADS = {}


def get_ffmpeg_location():
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return str(Path(ffmpeg_path).parent)

    winget_packages = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    matches = list(winget_packages.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"))
    if matches:
        return str(matches[0].parent)

    return None


def is_spotify_url(url):
    return "open.spotify.com/" in url.lower()


def is_instagram_url(url):
    return "instagram.com/" in url.lower()


def is_youtube_url(url):
    lower_url = url.lower()
    return "youtube.com/" in lower_url or "youtu.be/" in lower_url


def write_cookie_file_from_env():
    cookie_b64 = os.getenv("YTDLP_COOKIES_B64")
    cookie_content = os.getenv("YTDLP_COOKIES_CONTENT")

    if not cookie_b64 and not cookie_content:
        return None

    cookie_path = Path(os.getenv("YTDLP_COOKIE_FILE", "/tmp/cookies.txt"))
    if os.name == "nt" and str(cookie_path).startswith("/tmp/"):
        cookie_path = Path("cookies.txt")

    cookie_path.parent.mkdir(parents=True, exist_ok=True)

    if cookie_b64:
        cookie_path.write_bytes(base64.b64decode(cookie_b64))
    else:
        cookie_path.write_text(cookie_content, encoding="utf-8")

    return str(cookie_path)


def get_cookie_file():
    env_cookie_file = write_cookie_file_from_env()
    if env_cookie_file:
        return env_cookie_file

    cookie_file = os.getenv("YTDLP_COOKIE_FILE") or os.getenv("INSTAGRAM_COOKIE_FILE")
    candidates = [Path(cookie_file)] if cookie_file else []
    candidates.extend([Path("cookies.txt"), Path("instagram_cookies.txt")])

    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)

    return None


def cleanup_downloads_dir():
    if not DOWNLOADS_DIR.exists():
        return

    for path in DOWNLOADS_DIR.iterdir():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    if DOWNLOADS_DIR.exists() and not any(DOWNLOADS_DIR.iterdir()):
        DOWNLOADS_DIR.rmdir()


def clean_audio_performer(info):
    return (
        info.get("artist")
        or info.get("creator")
        or info.get("uploader")
        or info.get("channel")
        or ""
    )


def clean_audio_title(info):
    title = info.get("track") or info.get("title") or "Audio"
    performer = clean_audio_performer(info)

    if performer:
        prefixes = (f"{performer} - ", f"{performer} – ")
        for prefix in prefixes:
            if title.lower().startswith(prefix.lower()):
                return title[len(prefix) :].strip()

    return title


def build_download_options(download_dir, mode, use_cookies=True, youtube_client=None):
    cookie_file = get_cookie_file()
    ydl_opts = {
        "outtmpl": str(download_dir / "%(uploader).100B - %(title).200B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        **({"cookiefile": cookie_file} if cookie_file and use_cookies else {}),
    }

    if youtube_client:
        ydl_opts["extractor_args"] = {"youtube": {"player_client": [youtube_client]}}

    if mode == "audio":
        ydl_opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        )
    else:
        ydl_opts.update(
            {
                "format": "best[ext=mp4]/best",
            }
        )

    ffmpeg_location = get_ffmpeg_location()
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    return ydl_opts


def extract_info_with_fallback(url, download_dir, mode):
    option_sets = []

    if is_youtube_url(url):
        option_sets.extend(
            [
                ("youtube:android", build_download_options(download_dir, mode, use_cookies=False, youtube_client="android")),
                (
                    "youtube:tv_embedded",
                    build_download_options(download_dir, mode, use_cookies=False, youtube_client="tv_embedded"),
                ),
            ]
        )

    option_sets.append(("default", build_download_options(download_dir, mode)))
    last_error = None

    for label, ydl_opts in option_sets:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                info = normalize_info(info)
                return info, Path(ydl.prepare_filename(info))
        except Exception as exc:
            print(f"{label} download failed: {exc}", flush=True)
            last_error = exc

    raise last_error


def find_downloaded_file(download_dir, suffix=None):
    files = [path for path in download_dir.iterdir() if path.is_file()]
    if suffix:
        files = [path for path in files if path.suffix.lower() == suffix]

    if not files:
        return None

    return max(files, key=lambda path: path.stat().st_size)


def get_spotify_search_query(url):
    response = requests.get(
        "https://open.spotify.com/oembed",
        params={"url": url},
        timeout=20,
    )
    response.raise_for_status()
    title = response.json().get("title", "").strip()

    artist = ""
    page_response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    if page_response.ok:
        match = re.search(
            r'<meta name="description" content="Listen to .*? on Spotify\. Song · ([^·"]+)',
            page_response.text,
        )
        if match:
            artist = match.group(1).strip()

    query = " ".join(part for part in (artist, title, "audio") if part)
    if not query.strip():
        raise ValueError("Spotify qo'shiq nomini aniqlab bo'lmadi")

    return f"ytsearch1:{query}"


def normalize_info(info):
    if info.get("_type") == "playlist" and info.get("entries"):
        return info["entries"][0]

    return info


def friendly_error_message(exc, url):
    text = str(exc)
    lower_text = text.lower()

    if is_instagram_url(url) and (
        "log in" in lower_text
        or "login" in lower_text
        or "cookies" in lower_text
        or "private" in lower_text
    ):
        return (
            "Instagram bu link uchun login so'rayapti.\n\n"
            "Ayniqsa story, private profil yoki yosh cheklovi bor kontentda cookies kerak bo'ladi.\n"
            "Brauzerdan cookies export qilib loyiha papkasiga `cookies.txt` nomi bilan qo'ying, "
            "keyin botni qayta ishga tushiring.\n\n"
            "Oddiy public Reel/Post linklar odatda cookiesiz ham ishlaydi."
        )

    if is_youtube_url(url) and (
        "cookies are no longer valid" in lower_text
        or "cookies have been rotated" in lower_text
        or "provided youtube account cookies are no longer valid" in lower_text
    ):
        return (
            "YouTube cookies eskirgan yoki browser tomonidan rotate qilingan.\n\n"
            "Yangi `cookies.txt` export qiling, uni base64 qilib Railway Variables ichidagi "
            "`YTDLP_COOKIES_B64` qiymatini yangilang, keyin redeploy qiling."
        )

    if is_youtube_url(url) and (
        "sign in to confirm" in lower_text
        or "not a bot" in lower_text
        or "cookies" in lower_text
    ):
        return (
            "YouTube server IP'ni bot deb tekshiryapti.\n\n"
            "Bu hosting/containerlarda ko'p uchraydi. YouTube yuklash ishlashi uchun "
            "`cookies.txt` kerak bo'ladi yoki botni boshqa server/IP'da ishga tushirish kerak.\n\n"
            "Brauzerdan YouTube cookies export qilib serverga `cookies.txt` sifatida joylang "
            "yoki hostingda `YTDLP_COOKIE_FILE` env variable bilan cookie fayl yo'lini ko'rsating."
        )

    return f"Xatolik: {text}"


def download_and_send(chat_id, url, mode):
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    download_dir = DOWNLOADS_DIR / uuid4().hex
    download_dir.mkdir(exist_ok=True)

    try:
        if is_spotify_url(url):
            if mode != "audio":
                raise ValueError("Spotify linklar faqat Audio MP3 qilib yuboriladi")
            url = get_spotify_search_query(url)

        info, prepared_filename = extract_info_with_fallback(url, download_dir, mode)

        if mode == "audio":
            filename = prepared_filename.with_suffix(".mp3")
            if not filename.exists():
                filename = find_downloaded_file(download_dir, ".mp3")

            if not filename:
                raise FileNotFoundError("MP3 fayl topilmadi")

            with filename.open("rb") as audio:
                bot.send_audio(
                    chat_id,
                    audio,
                    title=clean_audio_title(info),
                    performer=clean_audio_performer(info),
                )
        else:
            filename = prepared_filename.with_suffix(".mp4")
            if not filename.exists():
                filename = find_downloaded_file(download_dir)

            if not filename:
                raise FileNotFoundError("Video fayl topilmadi")

            with filename.open("rb") as video:
                bot.send_video(
                    chat_id,
                    video,
                    caption=info.get("title") or "",
                    supports_streaming=True,
                )

    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        if DOWNLOADS_DIR.exists() and not any(DOWNLOADS_DIR.iterdir()):
            DOWNLOADS_DIR.rmdir()


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "Salom!\n\n"
        "Link yuboring, yuklab beraman:\n"
        "- YouTube\n"
        "- Spotify\n"
        "- TikTok\n"
        "- Instagram\n"
        "- Twitter/X\n\n"
        "Audio MP3 yoki Video MP4 - tanlov sizniki.",
    )

@bot.message_handler(func=lambda message: True)
def handle(message):
    url = message.text.strip()
    request_id = uuid4().hex[:12]
    PENDING_DOWNLOADS[request_id] = url

    keyboard = types.InlineKeyboardMarkup()
    if is_spotify_url(url):
        keyboard.add(types.InlineKeyboardButton("Audio MP3", callback_data=f"download:audio:{request_id}"))
    else:
        keyboard.add(
            types.InlineKeyboardButton("Audio MP3", callback_data=f"download:audio:{request_id}"),
            types.InlineKeyboardButton("Video MP4", callback_data=f"download:video:{request_id}"),
        )

    bot.reply_to(message, "Qaysi formatda yuboray?", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith("download:"))
def download_callback(call):
    _, mode, request_id = call.data.split(":", 2)
    url = PENDING_DOWNLOADS.pop(request_id, None)

    if not url:
        bot.answer_callback_query(call.id, "Bu link eskirgan. Qayta yuboring.")
        return

    bot.answer_callback_query(call.id, "Yuklanmoqda...")
    bot.send_message(call.message.chat.id, "Yuklanmoqda...")

    try:
        download_and_send(call.message.chat.id, url, mode)
    except Exception as exc:
        bot.send_message(call.message.chat.id, friendly_error_message(exc, url))


if __name__ == "__main__":
    cleanup_downloads_dir()
    bot.polling()
