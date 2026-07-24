"""
Weekly summary: reports accounts that haven't posted in 7+ days.
Designed to run once a week (e.g. Monday morning) as a GitHub Actions workflow.
"""

from datetime import datetime, timezone, timedelta

from common import supabase, send_telegram_text

STALE_DAYS = 7


def main():
    res = supabase.table("ig_accounts").select("*").eq("active", True).execute()
    accounts = res.data or []

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    stale = []
    never_posted = []

    for a in accounts:
        last_post_date = a.get("last_post_date")
        if not last_post_date:
            never_posted.append(a["username"])
            continue
        try:
            dt = datetime.fromisoformat(last_post_date.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt < cutoff:
            days_ago = (datetime.now(timezone.utc) - dt).days
            stale.append((a["username"], days_ago))

    if not stale and not never_posted:
        send_telegram_text(
            f"✅ Еженедельная проверка: все аккаунты постили за последние {STALE_DAYS} дней."
        )
        return

    lines = [f"📋 Еженедельная сводка — нет новых постов {STALE_DAYS}+ дней:\n"]
    for username, days_ago in sorted(stale, key=lambda x: -x[1]):
        lines.append(f"@{username} — {days_ago} дн. назад")

    if never_posted:
        lines.append("\nЕщё не проверялись ни разу (первый прогон не завершён?):")
        for username in never_posted:
            lines.append(f"@{username}")

    send_telegram_text("\n".join(lines))


if __name__ == "__main__":
    main()
