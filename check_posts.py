"""
Instagram new-posts monitor — powered by the Apify Instagram Post Scraper API
(https://apify.com/apify/instagram-post-scraper), instead of scraping directly.

Reports every post published on the FULL PREVIOUS CALENDAR DAY, Astana time
(00:00–23:59, UTC+5) — regardless of exactly when the script happens to run.
Sends each such post as a photo + link to Telegram, and logs it into the
ig_posts history table (used to avoid re-sending a post already reported).

Designed to run once per day as a scheduled GitHub Actions workflow.
"""

import os
import time
import traceback
from datetime import datetime, timedelta, timezone, time as dtime

import requests

from common import supabase, send_telegram_text, send_telegram_photo

PAUSE_BETWEEN_ACCOUNTS = int(os.environ.get("PAUSE_BETWEEN_ACCOUNTS", "1"))
RESULTS_PER_ACCOUNT = int(os.environ.get("RESULTS_PER_ACCOUNT", "1"))

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
APIFY_ACTOR = "apify~instagram-post-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"

ASTANA_TZ = timezone(timedelta(hours=5))


def get_yesterday_window():
    """Full previous calendar day in Astana time, as (start, end) datetimes."""
    now_local = datetime.now(ASTANA_TZ)
    yesterday_date = (now_local - timedelta(days=1)).date()
    start = datetime.combine(yesterday_date, dtime.min, tzinfo=ASTANA_TZ)
    end = datetime.combine(yesterday_date, dtime.max, tzinfo=ASTANA_TZ)
    return start, end


def parse_post_date(raw: str):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


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


def get_existing_shortcodes(username: str) -> set:
    res = supabase.table("ig_posts").select("shortcode").eq("username", username).execute()
    return {row["shortcode"] for row in (res.data or [])}


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


def fetch_posts_from_apify(username: str):
    """Calls Apify's Instagram Post Scraper synchronously and returns a list
    of posts (newest first), normalized to our own dict shape."""
    resp = requests.post(
        APIFY_URL,
        params={"token": APIFY_TOKEN},
        json={"username": [username], "resultsLimit": RESULTS_PER_ACCOUNT},
        timeout=60,
    )
    resp.raise_for_status()
    items = resp.json()

    posts = []
    for item in items:
        shortcode = item.get("shortCode") or item.get("shortcode")
        if not shortcode:
            continue
        posts.append(
            {
                "shortcode": shortcode,
                "url": item.get("url") or f"https://www.instagram.com/p/{shortcode}/",
                "image_url": item.get("displayUrl") or item.get("thumbnailSrc") or "",
                "date": item.get("timestamp") or "",
                "caption": (item.get("caption") or "").strip()[:300],
            }
        )

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def check_account(account: dict, window_start, window_end):
    username = account["username"]
    try:
        posts = fetch_posts_from_apify(username)

        if not posts:
            mark_checked(username)
            return []

        # Keep last_post_id/date updated to the newest post overall — used
        # by weekly_summary.py to flag accounts that have gone quiet.
        update_last_post(username, posts[0]["shortcode"], posts[0]["date"])

        existing = get_existing_shortcodes(username)
        new_posts = []
        for post in posts:
            post_dt = parse_post_date(post["date"])
            if not post_dt:
                continue
            post_dt_local = post_dt.astimezone(ASTANA_TZ)
            if window_start <= post_dt_local <= window_end and post["shortcode"] not in existing:
                new_posts.append(post)

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

    window_start, window_end = get_yesterday_window()
    print(f"Checking posts published between {window_start} and {window_end} (Astana time)")

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
        new_posts = check_account(account, window_start, window_end)

        for post in new_posts:
            log_post(username, post)
            caption_snippet = f"\n{post['caption'][:150]}" if post["caption"] else ""
            text = f"\U0001F4F8 @{username}\n{post['url']}{caption_snippet}"
            if post["image_url"]:
                resp = send_telegram_photo(post["image_url"], text)
                if resp.status_code != 200:
                    send_telegram_text(text)
            else:
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
        send_telegram_text(f"Постов за {window_start.date()} нет ни у одного аккаунта.")

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