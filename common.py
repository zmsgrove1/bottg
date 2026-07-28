"""Shared helpers used by check_posts.py, weekly_summary.py, command_bot.py,
content_summary.py, and design_bot.py."""

import json as _json
import os
import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Optional second bot (e.g. a separate "content idea" bot posting to the
# same group) — falls back to the main bot's token if not configured.
TELEGRAM_BOT_TOKEN_2 = os.environ.get("TELEGRAM_BOT_TOKEN_2", TELEGRAM_BOT_TOKEN)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def send_telegram_text(text: str, chat_id: str = None, reply_markup: dict = None, bot_token: str = None):
    token = bot_token or TELEGRAM_BOT_TOKEN
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        data["reply_markup"] = _json.dumps(reply_markup)
    resp = requests.post(url, data=data, timeout=30)
    if resp.status_code != 200:
        print(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")
    return resp


def send_telegram_photo(photo_url: str, caption: str, chat_id: str = None, bot_token: str = None):
    token = bot_token or TELEGRAM_BOT_TOKEN
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
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
        if "MYMEMORY WARNING" in translated.upper():
            print(f"Translation quota/warning: {translated}")
            return ""
        return translated
    except Exception as e:
        print(f"Translation error: {e}")
        return ""


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")


def ask_gpt(question: str, system: str = None, max_tokens: int = 800) -> str:
    """Calls the OpenAI chat completions API (text-only). Returns a
    user-facing error string (not an exception) on failure."""
    if not OPENAI_API_KEY:
        return "GPT не настроен — не хватает OPENAI_API_KEY."
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": question})
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": OPENAI_MODEL, "messages": messages, "max_tokens": max_tokens},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"OpenAI API error: {resp.status_code} {resp.text}")
            return f"Ошибка GPT API: {resp.status_code}"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"OpenAI call failed: {e}")
        return f"Не получилось получить ответ от GPT: {e}"


def ask_gpt_vision(image_url: str, instructions: str, max_tokens: int = 1500) -> str:
    """Sends a reference image + instructions to a vision-capable OpenAI
    model and returns the text response. PAID (OpenAI) — kept here for
    later, once the free pipeline below is proven out."""
    if not OPENAI_API_KEY:
        return "GPT не настроен — не хватает OPENAI_API_KEY."
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instructions},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "max_tokens": max_tokens,
            },
            timeout=90,
        )
        if resp.status_code != 200:
            print(f"OpenAI vision API error: {resp.status_code} {resp.text}")
            return f"Ошибка GPT vision API: {resp.status_code}"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"OpenAI vision call failed: {e}")
        return f"Не получилось проанализировать картинку: {e}"


# ---------------------------------------------------------------------------
# FREE alternative via Pollinations (no API key). This is what design_bot.py
# uses right now, since the plan is: build the free version first, switch
# specific steps to the paid OpenAI functions above later if quality needs it.

POLLINATIONS_TEXT_URL = "https://text.pollinations.ai/openai"


def ask_pollinations_text(prompt: str, system: str = None, model: str = "openai", max_tokens: int = 800) -> str:
    """Free text generation via Pollinations (OpenAI-compatible endpoint,
    no key needed). Returns '' on failure so callers can fall back cleanly."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = requests.post(
            POLLINATIONS_TEXT_URL,
            json={"model": model, "messages": messages, "max_tokens": max_tokens},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"Pollinations text error: {resp.status_code} {resp.text}")
            return ""
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Pollinations text call failed: {e}")
        return ""


def ask_pollinations_vision(image_url: str, instructions: str, model: str = "openai-large", max_tokens: int = 1500) -> str:
    """Free vision (image analysis) via Pollinations — same endpoint as
    ask_pollinations_text, just with an image_url content part added.
    'openai-large' is Pollinations' more capable vision model."""
    try:
        resp = requests.post(
            POLLINATIONS_TEXT_URL,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instructions},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "max_tokens": max_tokens,
            },
            timeout=90,
        )
        if resp.status_code != 200:
            print(f"Pollinations vision error: {resp.status_code} {resp.text}")
            return ""
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Pollinations vision call failed: {e}")
        return ""