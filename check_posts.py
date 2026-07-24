"""
Instagram new-posts monitor.
Checks a list of Instagram accounts (stored in Supabase) for posts newer
than the last seen post, and sends links for new ones to a Telegram chat.

Designed to run once per day as a Render Cron Job.
"""

import os
import time
import traceback
from datetime import datetime, timezone

import requests
import instaloader
from supabase import create_client

# ---- Config from environment variables ----
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Seconds to pause between accounts — keep this generous to reduce
# the chance of Instagram rate-limiting / temp-blocking the IP.
PAUSE_BETWEEN_ACCOUNTS = int(os.environ.get("PAUSE_BETWEEN_ACCOUNTS", "15"))

# Safety cap: max posts to walk back through per account in one run
MAX_POSTS_PER_ACCOUNT = 20

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

L = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
    quiet=True,
)

# Optional: if Instagram starts blocking anonymous access, you can log in
# with a dedicated account and load a saved session here instead.
# Example (do this once locally, then upload the session file as a Render secret file):
#   L.load_session_from_file("your_ig_username", filename="session-your_ig_username")
IG_SESSION_USERNAME = os.environ.get("IG_SESSION_USERNAME")
IG_SESSION_FILE = os.environ.get("IG_SESSION_FILE")
if IG_SESSION_USERNAME and IG_SESSION_FILE:
    try:
        L.load_session_from_file(IG_SESSION_USERNAME, filename=IG_SESSION_FILE)
    except Exception as e:
        print(f"Could not load IG session, continuing anonymously: {e}")


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Telegram send failed: {resp.status_code} {resp.text}")


def get_accounts():
    res = supabase.table("ig_accounts").select("*").eq("active", True).execute()
    return res.data


def update_last_post(username: str, post_id: str, post_date: str):
    supabase.table("ig_accounts").update(
        {"last_post_id": post_id, "last_post_date": post_date}
    ).eq("username", username).execute()


def mark_error(username: str, message: str):
    supabase.table("ig_accounts").update(
        {"last_error": message[:500], "last_checked_at": datetime.now(timezone.utc).isoformat()}
    ).eq("username", username).execute()


def check_account(account: dict):
    """Returns a list of dicts: {url, date, caption} for posts newer than last_post_id."""
    username = account["username"]
    last_post_id = account.get("last_post_id")
    first_run = last_post_id is None
    new_posts = []

    try:
        profile = instaloader.Profile.from_username(L.context, username)
        posts = profile.get_posts()

        newest_id = None
        newest_date = None

        for i, post in enumerate(posts):
            if i == 0:
                newest_id = str(post.mediaid)
                newest_date = post.date_utc.isoformat()

            if first_run:
                # Just establish a baseline on the very first check —
                # don't dump the account's whole recent history as "new".
                break

            if str(post.mediaid) == last_post_id:
                break

            new_posts.append(
                {
                    "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "date": post.date_utc.isoformat(),
                    "caption": (post.caption or "").strip()[:100],
                }
            )

            if i >= MAX_POSTS_PER_ACCOUNT - 1:
                break

        if newest_id:
            update_last_post(username, newest_id, newest_date)

        # clear any previous error flag on success
        supabase.table("ig_accounts").update(
            {"last_error": None, "last_checked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("username", username).execute()

        return new_posts

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"Error checking {username}: {err}")
        mark_error(username, err)
        return []


def chunk_message(lines, limit=3500):
    """Telegram messages are capped at 4096 chars; batch lines safely under that."""
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def main():
    accounts = get_accounts()
    if not accounts:
        send_telegram("Список аккаунтов пуст — нечего проверять.")
        return

    report_lines = []
    failed = []

    for idx, account in enumerate(accounts):
        new_posts = check_account(account)
        if new_posts:
            for p in new_posts:
                report_lines.append(f"@{account['username']}: {p['url']}")
        if account.get("last_error"):
            failed.append(account["username"])

        if idx < len(accounts) - 1:
            time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    if report_lines:
        header = f"Новые посты за последние 24 часа ({len(report_lines)}):\n"
        for chunk in chunk_message([header] + report_lines):
            send_telegram(chunk)
    else:
        send_telegram("Новых постов за последние 24 часа нет.")

    if failed:
        send_telegram(
            "⚠️ Не удалось проверить: " + ", ".join(f"@{u}" for u in failed)
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_text = traceback.format_exc()
        print(err_text)
        try:
            send_telegram(f"❌ Скрипт упал с ошибкой:\n{err_text[:1000]}")
        except Exception:
            pass
        raise
