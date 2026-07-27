"""
Polls Telegram for new messages sent to the bot and processes account
management via buttons (a persistent reply keyboard) instead of typed
slash commands. Actions that need a username (add/remove/pause/resume)
ask for it as a follow-up message; the bot remembers what it's waiting
for per chat using the bot_state table.

Slash commands (/add username, /remove username, etc.) still work too,
for convenience.

Designed to run every few minutes as a scheduled GitHub Actions workflow
(there is no always-on server, so replies land within that polling window,
not instantly).
"""

import re
from datetime import datetime, timedelta, timezone

import requests

from common import supabase, TELEGRAM_BOT_TOKEN, send_telegram_text

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,60}$")
ASTANA_TZ = timezone(timedelta(hours=5))

MAIN_KEYBOARD = {
    "keyboard": [
        ["📋 Список", "🕒 История"],
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
    "🕒 История": ("history", "Напишите юзернейм аккаунта, чтобы посмотреть его последние посты:"),
}


def format_astana(raw: str) -> str:
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ASTANA_TZ)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return raw


def get_offset() -> int:
    res = supabase.table("bot_state").select("value").eq("key", "last_update_id").single().execute()
    return int(res.data["value"]) if res.data else 0


def set_offset(update_id: int):
    supabase.table("bot_state").update({"value": str(update_id)}).eq(
        "key", "last_update_id"
    ).execute()


def get_pending(chat_id: str):
    res = supabase.table("bot_state").select("value").eq("key", f"pending:{chat_id}").execute()
    return res.data[0]["value"] if res.data else None


def set_pending(chat_id: str, action: str):
    supabase.table("bot_state").upsert(
        {"key": f"pending:{chat_id}", "value": action}
    ).execute()


def clear_pending(chat_id: str):
    supabase.table("bot_state").delete().eq("key", f"pending:{chat_id}").execute()


def get_updates(offset: int):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset + 1, "timeout": 0}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def clean_username(raw: str) -> str:
    return raw.strip().lstrip("@")


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


def handle_list(chat_id: str):
    res = supabase.table("ig_accounts").select(
        "username, active, last_error, last_post_date"
    ).execute()
    accounts = res.data or []
    if not accounts:
        send_telegram_text("Список пуст.", chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return
    lines = ["📋 Список аккаунтов:\n"]
    for a in sorted(accounts, key=lambda x: x["username"]):
        status = "🟢" if a["active"] else "⏸"
        err = " ⚠️" if a.get("last_error") else ""
        last_post = format_astana(a.get("last_post_date"))
        lines.append(f"{status} @{a['username']}{err} — послед. пост: {last_post}")
    send_telegram_text("\n".join(lines), chat_id=chat_id, reply_markup=MAIN_KEYBOARD)


def process_message(text: str, chat_id: str):
    text = text.strip()
    if not text:
        return

    # 1) If we're waiting for a username after a button press, this
    #    message IS that username — act on it and clear the pending state.
    pending = get_pending(chat_id)
    if pending:
        username = clean_username(text)
        clear_pending(chat_id)
        {
            "add": handle_add,
            "remove": handle_remove,
            "pause": handle_pause,
            "resume": handle_resume,
            "history": handle_history,
        }[pending](username, chat_id)
        return

    # 2) Button presses that need a follow-up username.
    if text in BUTTON_TO_ACTION:
        action, prompt = BUTTON_TO_ACTION[text]
        set_pending(chat_id, action)
        send_telegram_text(prompt, chat_id=chat_id, reply_markup=MAIN_KEYBOARD)
        return

    # 3) Button press that needs no argument.
    if text == "📋 Список":
        handle_list(chat_id)
        return

    # 4) Fall back to classic slash commands, for convenience.
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
        handle_list(chat_id)
    elif command in ("/start", "/help"):
        send_telegram_text(
            "Выберите действие кнопкой ниже, или используйте команды:\n"
            "/add username, /remove username, /pause username, /resume username, /list",
            chat_id=chat_id,
            reply_markup=MAIN_KEYBOARD,
        )


def main():
    offset = get_offset()
    updates = get_updates(offset)

    if not updates:
        return

    max_update_id = offset
    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])
        message = update.get("message")
        if not message or "text" not in message:
            continue
        chat_id = str(message["chat"]["id"])
        process_message(message["text"], chat_id)

    set_offset(max_update_id)


if __name__ == "__main__":
    main()