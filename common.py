"""Shared helpers used by check_posts.py, weekly_summary.py, and command_bot.py."""

import os
import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def send_telegram_text(text: str, chat_id: str = None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id or TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")
    return resp


def send_telegram_photo(photo_url: str, caption: str, chat_id: str = None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id or TELEGRAM_CHAT_ID,
            "photo": photo_url,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Telegram sendPhoto failed: {resp.status_code} {resp.text}")
    return resp


MYMEMORY_EMAIL = os.environ.get("MYMEMORY_EMAIL")


def translate_text(text: str, source: str = "tr", target: str = "ru") -> str:
    """Free translation via MyMemory (no API key needed). Without an email,
    the anonymous quota is ~5000 words/day; passing an email raises it to
    ~10000 words/day — still free, no signup required.
    Returns an empty string if translation fails — caller should fall back
    to showing the original text rather than breaking the whole message."""
    if not text or not text.strip():
        return ""
    try:
        params = {"q": text[:490], "langpair": f"{source}|{target}"}
        if MYMEMORY_EMAIL:
            params["de"] = MYMEMORY_EMAIL
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params=params,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"Translation failed: {resp.status_code} {resp.text}")
            return ""
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        # MyMemory sometimes returns an error string as "translated" text
        if "MYMEMORY WARNING" in translated.upper():
            print(f"Translation quota/warning: {translated}")
            return ""
        return translated
    except Exception as e:
        print(f"Translation error: {e}")
        return ""