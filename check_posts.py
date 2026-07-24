"""
Instagram new-posts monitor.
Checks a list of Instagram accounts (stored in Supabase) for posts newer
than the last seen post. Sends each new post as a photo + link to Telegram,
and logs it into the ig_posts history table.

Designed to run once per day as a scheduled GitHub Actions workflow.
"""

import os
import time
import traceback
from datetime import datetime, timezone

import instaloader

from common import supabase, send_telegram_text, send_telegram_photo

PAUSE_BETWEEN_ACCOUNTS = int(os.environ.get("PAUSE_BETWEEN_ACCOUNTS", "15"))
MAX_POSTS_PER_ACCOUNT = 20


class FailFastRateController(instaloader.RateController):
    """Instaloader's default behavior on a 429 is to sleep until the rate
    limit window resets — sometimes 30+ minutes — and then retry. That can
    balloon a single account into a half-hour hang. Instead, refuse to wait
    more than a few seconds; raise so the caller can skip this account and
    move on, rather than silently sleeping the whole job."""

    MAX_SLEEP_SECONDS = 10

    def sleep(self, secs: float):
        if secs > self.MAX_SLEEP_SECONDS:
            raise instaloader.exceptions.ConnectionException(
                f"Rate-limited; would need to wait {secs:.0f}s — skipping instead of waiting"
            )
        time.sleep(secs)


L = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
    quiet=True,
    request_timeout=15,          # default is 300s — too long if IG hangs a request
    max_connection_attempts=1,   # don't internally retry a stuck/rate-limited account
    rate_controller=lambda ctx: FailFastRateController(ctx),
)

IG_SESSION_USERNAME = os.environ.get("IG_SESSION_USERNAME")
IG_SESSION_FILE = os.environ.get("IG_SESSION_FILE")
if IG_SESSION_USERNAME and IG_SESSION_FILE:
    try:
        L.load_session_from_file(IG_SESSION_USERNAME, filename=IG_SESSION_FILE)
    except Exception as e:
        print(f"Could not load IG session, continuing anonymously: {e}")


def get_accounts():
    res = supabase.table("ig_accounts").select("*").eq("active", True).execute()
    return res.data


def update_last_post(username: str, post_id: str, post_date: str):
    supabase.table("ig_accounts").update(
        {"last_post_id": post_id, "last_post_date": post_date}
    ).eq("username", username).execute()


def mark_checked(username: str, error: str = None):
    supabase.table("ig_accounts").update(
        {"last_error": error[:500] if error else None,
         "last_checked_at": datetime.now(timezone.utc).isoformat()}
    ).eq("username", username).execute()


def log_post(username: str, post: dict):
    try:
        supabase.table("ig_posts").upsert(
            {
                "username": username,
                "shortcode": post["shortcode"],
                "url": post["url"],
                "image_url": post["image_url"],
                "caption": post["caption"],
                "post_date": post["date"],
            },
            on_conflict="username,shortcode",
        ).execute()
    except Exception as e:
        print(f"Could not log post history for {username}/{post['shortcode']}: {e}")


def check_account(account: dict):
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
                break

            if str(post.mediaid) == last_post_id:
                break

            new_posts.append(
                {
                    "shortcode": post.shortcode,
                    "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "image_url": post.url,
                    "date": post.date_utc.isoformat(),
                    "caption": (post.caption or "").strip()[:300],
                }
            )

            if i >= MAX_POSTS_PER_ACCOUNT - 1:
                break

        if newest_id:
            update_last_post(username, newest_id, newest_date)

        mark_checked(username)
        return new_posts

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"Error checking {username}: {err}")
        mark_checked(username, error=err)
        return []


def main():
    accounts = get_accounts()
    if not accounts:
        send_telegram_text("Список аккаунтов пуст — нечего проверять.")
        return

    total_new = 0
    failed = []
    skipped_out_of_time = []
    start_time = time.monotonic()
    MAX_TOTAL_SECONDS = 20 * 60  # safety cap: never let the whole run exceed ~20 min

    for idx, account in enumerate(accounts):
        if time.monotonic() - start_time > MAX_TOTAL_SECONDS:
            skipped_out_of_time.extend(a["username"] for a in accounts[idx:])
            break

        username = account["username"]
        new_posts = check_account(account)

        for post in new_posts:
            log_post(username, post)
            caption_snippet = f"\n{post['caption'][:150]}" if post["caption"] else ""
            text = f"\U0001F4F8 @{username}\n{post['url']}{caption_snippet}"
            resp = send_telegram_photo(post["image_url"], text)
            if resp.status_code != 200:
                send_telegram_text(text)
            total_new += 1
            time.sleep(1)

        refreshed = supabase.table("ig_accounts").select("last_error").eq(
            "username", username
        ).single().execute()
        if refreshed.data and refreshed.data.get("last_error"):
            failed.append(username)

        if idx < len(accounts) - 1:
            time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    if total_new == 0:
        send_telegram_text("Новых постов за последние 24 часа нет.")

    if failed:
        send_telegram_text(
            "\u26A0\uFE0F Не удалось проверить: " + ", ".join(f"@{u}" for u in failed)
        )

    if skipped_out_of_time:
        send_telegram_text(
            "\u23F1 Прогон превысил лимит времени, не успели проверить: "
            + ", ".join(f"@{u}" for u in skipped_out_of_time)
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_text = traceback.format_exc()
        print(err_text)
        try:
            send_telegram_text(f"\u274C Скрипт упал с ошибкой:\n{err_text[:1000]}")
        except Exception:
            pass
        raise