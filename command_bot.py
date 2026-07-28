"""
Handles Telegram account-management via a persistent reply keyboard, plus
inline (in-message) keyboards for picking an account, a content category,
a manual job to run, or a list filter — by tapping instead of typing.
Called from app.py's /telegram-webhook route on every incoming
message/button tap — no polling delay.

Reply-keyboard buttons (always visible at the bottom of the chat):
  📋 Список          — list of accounts + status + last post date (with
                       inline filter buttons: all / errors only / paused only)
  🕒 История          — tap an account from an inline list to see its recent posts
  📊 По категориям     — tap a content category to see recent matching posts
  ▶️ Запустить проверку — tap which job to run right now (runs in a background
                       thread; results arrive as separate messages when done)
  ⏸ Пауза всем / ▶️ Включить всех — mass pause/resume, with a confirm step
  ➕ Добавить / ➖ Удалить / ⏸ Пауза / ▶️ Включить
                       — still ask for a typed username (can't pick a new
                       account to add from an existing list)

Slash commands (/add username, etc.) still work too, for convenience.
"""

import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from common import supabase, TELEGRAM_BOT_TOKEN, send_telegram_text, ask_gpt
import check_posts
import weekly_summary
import content_summary

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,60}$")
ASTANA_TZ = timezone(timedelta(hours=5))

MAIN_KEYBOARD = {
    "keyboard": [
        ["📋 Список", "🕒 История"],
        ["📊 По категориям"],
        ["▶️ Запустить проверку", "💬 Спросить GPT"],
        ["⏸ Пауза всем", "▶️ Включить всех"],
        ["➕ Добавить", "➖ Удалить"],
        ["⏸ Пауза", "▶️ Включить"],
    ],
    "resize_keyboard": True,
}

BUTTON_TO_ACTION = {
    "➕ Добавить": ("add", "Напишите юзернейм аккаунта для добавления (без @):"),
    "➖ Удалить": ("remove", "Напишите юзернейм аккаунта для удаления:"),
    "⏸ Пауза": ("pause", "Напишите юзернейм аккаунта для паузы:"),
    "▶️ Включить": ("resume", "Напишите юзернейм аккаунта для включения:"),
    "💬 Спросить GPT": ("ask_gpt", "Задайте вопрос для GPT:"),
}

CATEGORY_LABELS = {
    "type:post": "🖼 Посты",
    "type:reel": "🎬 Рилсы",
    "category:campaign": "🏷 Акции",
    "category:review": "💬 Отзывы",
    "category:general": "📄 Обычные (без категории)",
}

RUN_ACTIONS = {
    "check_posts": ("📸 Проверка постов за вчера", check_posts.main),
    "weekly_summary": ("📅 Сводка молчунов", weekly_summary.main),
    "content_summary": ("📊 Сводка по контенту", content_summary.main),
}


def format_astana(raw: str) -> str:
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ASTANA_TZ)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return raw


def get_pending(chat_id: str):
    res = supabase.table("bot_state").select("value").eq("key", f"pending:{chat_id}").execute()
    return res.data[0]["value"] if res.data else None


def set_pending(chat_id: str, action: str):
    supabase.table("bot_state").upsert(
        {"key": f"pending:{chat_id}", "value": action}
    ).execute()


def clear_pending(chat_id: str):
    supabase.table("bot_state").delete().eq("key", f"pending:{chat_id}").execute()


def clean_username(raw: str) -> str:
    return raw.strip().lstrip("@")


def answer_callback(callback_query_id: str, text: str = None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    try:
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"answerCallbackQuery failed: {e}")


# ---------------------------------------------------------------------------
# Plain account-management actions

def handle_add(username: str, chat_id: str):
    if not USERNAME_RE.match(username):
        send_telegram_text(f"Некорректный юзернейм: {username}", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return
    existing = supabase.table("ig_accounts").select("username").eq("username", username).execute()
    if existing.data:
        supabase.table("ig_accounts").update({"active": True}).eq("username", username).execute()
        send_telegram_text(f"@{username} уже был в списке — включил обратно.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
    else:
        supabase.table("ig_accounts").insert({"username": username, "active": True}).execute()
        send_telegram_text(f"✅ Добавил @{username} в список отслеживания.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_remove(username: str, chat_id: str):
    res = supabase.table("ig_accounts").delete().eq("username", username).execute()
    if res.data:
        send_telegram_text(f"🗑 Удалил @{username} из списка.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_pause(username: str, chat_id: str):
    res = supabase.table("ig_accounts").update({"active": False}).eq("username", username).execute()
    if res.data:
        send_telegram_text(f"⏸ @{username} поставлен на паузу.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_resume(username: str, chat_id: str):
    res = supabase.table("ig_accounts").update({"active": True}).eq("username", username).execute()
    if res.data:
        send_telegram_text(f"▶️ @{username} снова активен.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_list(chat_id: str, filter_key: str = "all"):
    res = supabase.table("ig_accounts").select(
        "username, active, last_error, last_post_date"
    ).execute()
    accounts = res.data or []

    if filter_key == "errors":
        accounts = [a for a in accounts if a.get("last_error")]
    elif filter_key == "paused":
        accounts = [a for a in accounts if not a["active"]]

    filter_buttons = {
        "inline_keyboard": [[
            {"text": "Все", "callback_data": "listf:all"},
            {"text": "⚠️ Ошибки", "callback_data": "listf:errors"},
            {"text": "⏸ Пауза", "callback_data": "listf:paused"},
        ]]
    }

    if not accounts:
        send_telegram_text("Ничего не найдено по этому фильтру.", chat_id=chat_id, reply_markup=filter_buttons)
        return

    title = {"all": "📋 Все аккаунты", "errors": "⚠️ Аккаунты с ошибками", "paused": "⏸ Аккаунты на паузе"}[filter_key]
    lines = [f"{title}:\n"]
    for a in sorted(accounts, key=lambda x: x["username"]):
        status = "🟢" if a["active"] else "⏸"
        err = " ⚠️" if a.get("last_error") else ""
        last_post = format_astana(a.get("last_post_date"))
        lines.append(f"{status} @{a['username']}{err} — послед. пост: {last_post}")
    send_telegram_text("\n".join(lines), chat_id=chat_id, reply_markup=filter_buttons)


def handle_pause_all(chat_id: str):
    supabase.table("ig_accounts").update({"active": False}).neq("username", "").execute()
    send_telegram_text("⏸ Все аккаунты поставлены на паузу.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_resume_all(chat_id: str):
    supabase.table("ig_accounts").update({"active": True}).neq("username", "").execute()
    send_telegram_text("▶️ Все аккаунты снова активны.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# Inline-keyboard pickers: account history, content categories, run-now

def send_account_picker(chat_id: str):
    posts_res = supabase.table("ig_posts").select("username").execute()
    counts = Counter(row["username"] for row in (posts_res.data or []))

    accounts_res = supabase.table("ig_accounts").select("username").execute()
    usernames = sorted(a["username"] for a in (accounts_res.data or []))

    if not usernames:
        send_telegram_text("Список аккаунтов пуст.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return

    buttons = [
        [{"text": f"@{u} ({counts.get(u, 0)})", "callback_data": f"hist:{u}"}]
        for u in usernames
    ]
    send_telegram_text(
        "Выберите аккаунт, чтобы посмотреть его последние посты:",
        chat_id=chat_id,
        reply_markup={"inline_keyboard": buttons},
    )


def send_category_picker(chat_id: str):
    res = supabase.table("ig_posts").select("content_type, category").execute()
    rows = res.data or []
    type_counts = Counter((r.get("content_type") or "post") for r in rows)
    cat_counts = Counter((r.get("category") or "general") for r in rows)

    def btn(key, count_source, count_key):
        label = CATEGORY_LABELS[key]
        count = count_source.get(count_key, 0)
        return [{"text": f"{label} ({count})", "callback_data": f"cat:{key}"}]

    buttons = [
        btn("type:post", type_counts, "post"),
        btn("type:reel", type_counts, "reel"),
        btn("category:campaign", cat_counts, "campaign"),
        btn("category:review", cat_counts, "review"),
        btn("category:general", cat_counts, "general"),
    ]
    send_telegram_text(
        "Выберите категорию контента:",
        chat_id=chat_id,
        reply_markup={"inline_keyboard": buttons},
    )


def send_run_picker(chat_id: str):
    buttons = [[{"text": label, "callback_data": f"run:{key}"}] for key, (label, _) in RUN_ACTIONS.items()]
    send_telegram_text(
        "Что запустить прямо сейчас? Результат придёт отдельным сообщением.",
        chat_id=chat_id,
        reply_markup={"inline_keyboard": buttons},
    )


def send_pause_all_confirm(chat_id: str):
    send_telegram_text(
        "Точно поставить ВСЕ аккаунты на паузу?",
        chat_id=chat_id,
        reply_markup={"inline_keyboard": [
            [{"text": "Да, всем паузу", "callback_data": "pauseall:yes"}],
            [{"text": "Отмена", "callback_data": "pauseall:no"}],
        ]},
    )


def send_resume_all_confirm(chat_id: str):
    send_telegram_text(
        "Точно включить ВСЕ аккаунты?",
        chat_id=chat_id,
        reply_markup={"inline_keyboard": [
            [{"text": "Да, включить всех", "callback_data": "resumeall:yes"}],
            [{"text": "Отмена", "callback_data": "resumeall:no"}],
        ]},
    )


def handle_history(username: str, chat_id: str):
    res = (
        supabase.table("ig_posts")
        .select("url, post_date, content_type, category")
        .eq("username", username)
        .order("post_date", desc=True)
        .limit(10)
        .execute()
    )
    posts = res.data or []
    if not posts:
        send_telegram_text(
            f"У @{username} пока нет сохранённых постов в истории.",
            chat_id=chat_id,
            reply_markup=MAIN_KEYBOARD,
        )
        return

    lines = [f"🕒 Последние посты @{username}:\n"]
    for p in posts:
        when = format_astana(p.get("post_date"))
        tag = "🎬" if p.get("content_type") == "reel" else "🖼"
        lines.append(f"{tag} {when} — {p['url']}")
    send_telegram_text("\n".join(lines), chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def handle_category(key: str, chat_id: str):
    kind, value = key.split(":", 1)
    column = "content_type" if kind == "type" else "category"

    res = (
        supabase.table("ig_posts")
        .select("username, url, post_date")
        .eq(column, value)
        .order("post_date", desc=True)
        .limit(15)
        .execute()
    )
    posts = res.data or []
    label = CATEGORY_LABELS.get(key, key)

    if not posts:
        send_telegram_text(f"{label}: постов пока нет.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return

    lines = [f"{label} — последние {len(posts)}:\n"]
    for p in posts:
        when = format_astana(p.get("post_date"))
        lines.append(f"@{p['username']} — {when} — {p['url']}")
    send_telegram_text("\n".join(lines), chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def run_job_in_background(key: str, chat_id: str):
    label, func = RUN_ACTIONS[key]

    def wrapper():
        try:
            func()
        except Exception as e:
            send_telegram_text(
                f"❌ Ошибка при выполнении «{label}»: {type(e).__name__}: {e}",
                chat_id=chat_id,
                reply_markup=MAIN_KEYBOARD,
            )

    threading.Thread(target=wrapper, daemon=True).start()
    send_telegram_text(
        f"⏳ Запустил: {label}. Результат придёт отдельным сообщением через несколько минут.",
        chat_id=chat_id,
        reply_markup=MAIN_KEYBOARD,
    )


# ---------------------------------------------------------------------------
# Entry points called from app.py

def process_message(text: str, chat_id: str):
    text = text.strip()
    if not text:
        return

    pending = get_pending(chat_id)
    if pending:
        clear_pending(chat_id)
        if pending == "ask_gpt":
            answer = ask_gpt(text)
            send_telegram_text(f"💬 GPT:\n{answer}", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
            return
        username = clean_username(text)
        {
            "add": handle_add,
            "remove": handle_remove,
            "pause": handle_pause,
            "resume": handle_resume,
        }[pending](username, chat_id)
        return

    if text in BUTTON_TO_ACTION:
        action, prompt = BUTTON_TO_ACTION[text]
        set_pending(chat_id, action)
        send_telegram_text(prompt, chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return

    if text == "📋 Список":
        handle_list(chat_id, "all")
        return
    if text == "🕒 История":
        send_account_picker(chat_id)
        return
    if text == "📊 По категориям":
        send_category_picker(chat_id)
        return
    if text == "▶️ Запустить проверку":
        send_run_picker(chat_id)
        return
    if text == "⏸ Пауза всем":
        send_pause_all_confirm(chat_id)
        return
    if text == "▶️ Включить всех":
        send_resume_all_confirm(chat_id)
        return

    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]
    arg = clean_username(parts[1]) if len(parts) > 1 else ""

    if command == "/add" and arg:
        handle_add(arg, chat_id)
    elif command == "/remove" and arg:
        handle_remove(arg, chat_id)
    elif command == "/pause" and arg:
        handle_pause(arg, chat_id)
    elif command == "/resume" and arg:
        handle_resume(arg, chat_id)
    elif command == "/list":
        handle_list(chat_id, "all")
    elif command in ("/start", "/help"):
        send_telegram_text(
            "Выберите действие кнопкой ниже, или используйте команды:\n"
            "/add username, /remove username, /pause username, /resume username, /list",
            chat_id=chat_id,
            reply_markup=MAIN_KEYBOARD,
        )


def process_callback(data: str, chat_id: str, callback_query_id: str):
    answer_callback(callback_query_id)

    if data.startswith("hist:"):
        handle_history(data.split(":", 1)[1], chat_id)
    elif data.startswith("cat:"):
        handle_category(data.split(":", 1)[1], chat_id)
    elif data.startswith("listf:"):
        handle_list(chat_id, data.split(":", 1)[1])
    elif data.startswith("run:"):
        key = data.split(":", 1)[1]
        if key in RUN_ACTIONS:
            run_job_in_background(key, chat_id)
    elif data == "pauseall:yes":
        handle_pause_all(chat_id)
    elif data == "pauseall:no":
        send_telegram_text("Отменено.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
    elif data == "resumeall:yes":
        handle_resume_all(chat_id)
    elif data == "resumeall:no":
        send_telegram_text("Отменено.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)