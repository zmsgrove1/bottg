"""
Content-mix summary: reads all posts logged so far in ig_posts and reports,
per account AND overall across all accounts, which content type dominates —
regular posts, reels, campaign/promo posts, or review/testimonial posts.

Category detection ("campaign" / "review") is a rough, keyword-based guess
(Turkish keywords in the caption) — not a real classifier, just a useful
signal. Content type (post vs reel) comes from Apify's own data.

Designed to run every ~3 days as a scheduled GitHub Actions workflow.
"""

from collections import Counter, defaultdict

from common import supabase, send_telegram_text

CATEGORY_LABELS = {
    "campaign": "🏷 Акции",
    "review": "💬 Отзывы",
    "general": "📄 Обычные посты",
}
CONTENT_TYPE_LABELS = {
    "reel": "🎬 Рилсы",
    "post": "🖼 Посты",
}


def fetch_all_posts():
    res = supabase.table("ig_posts").select("username, content_type, category").execute()
    return res.data or []


def format_breakdown(counter: Counter, labels: dict) -> str:
    total = sum(counter.values())
    if total == 0:
        return "нет данных"
    parts = []
    for key, label in labels.items():
        count = counter.get(key, 0)
        if count:
            pct = round(100 * count / total)
            parts.append(f"{label}: {count} ({pct}%)")
    return ", ".join(parts) if parts else "нет данных"


def main():
    posts = fetch_all_posts()

    if not posts:
        send_telegram_text("Пока нет ни одного сохранённого поста для анализа контента.")
        return

    overall_type = Counter()
    overall_category = Counter()
    by_account_type = defaultdict(Counter)
    by_account_category = defaultdict(Counter)

    for p in posts:
        username = p["username"]
        content_type = p.get("content_type") or "post"
        category = p.get("category") or "general"
        overall_type[content_type] += 1
        overall_category[category] += 1
        by_account_type[username][content_type] += 1
        by_account_category[username][category] += 1

    lines = ["📊 Сводка по типам контента (накопительно за всё время)\n"]
    lines.append("Всего по всем аккаунтам:")
    lines.append(f"  Формат: {format_breakdown(overall_type, CONTENT_TYPE_LABELS)}")
    lines.append(f"  Категория: {format_breakdown(overall_category, CATEGORY_LABELS)}")
    lines.append("")
    lines.append("По аккаунтам:")

    for username in sorted(by_account_type.keys()):
        type_str = format_breakdown(by_account_type[username], CONTENT_TYPE_LABELS)
        cat_str = format_breakdown(by_account_category[username], CATEGORY_LABELS)
        lines.append(f"\n@{username}")
        lines.append(f"  Формат: {type_str}")
        lines.append(f"  Категория: {cat_str}")

    # Telegram messages are capped at 4096 chars — split into chunks if needed.
    text = "\n".join(lines)
    limit = 3800
    for i in range(0, len(text), limit):
        send_telegram_text(text[i:i + limit])


if __name__ == "__main__":
    main()