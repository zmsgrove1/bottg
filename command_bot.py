"""
Polls Telegram for new messages sent to the bot and processes account
management commands:

  /add username        — start monitoring this account
  /remove username      — stop monitoring and delete its saved state
  /pause username       — temporarily stop checking (keeps saved state)
  /resume username      — resume checking a paused account
  /list                 — list all accounts and their status

Designed to run every few minutes as a scheduled GitHub Actions workflow
(there is no always-on server, so replies land within that polling window,
not instantly).
"""

import re
import requests

from common import supabase, TELEGRAM_BOT_TOKEN, send_telegram_text

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,60}$")


def get_offset() -> int:
    res = supabase.table("bot_state").select("value").eq("key", "last_update_id").single().execute()
    return int(res.data["value"]) if res.data else 0


def set_offset(update_id: int):
    supabase.table("bot_state").update({"value": str(update_id)}).eq(
        "key", "last_update_id"
    ).execute()


def get_updates(offset: int):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset + 1, "timeout": 0}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def clean_username(raw: str) -> str:
    return raw.strip().lstrip("@")


def handle_add(username: str, chat_id: str):
    if not USERNAME_RE.match(username):
        send_telegram_text(f"Некорректный юзернейм: {username}", chat_id=chat_id)
        return
    existing = supabase.table("ig_accounts").select("username").eq("username", username).execute()
    if existing.data:
        supabase.table("ig_accounts").update({"active": True}).eq("username", username).execute()
        send_telegram_text(f"@{username} уже был в списке — включил обратно.", chat_id=chat_id)
    else:
        supabase.table("ig_accounts").insert({"username": username, "active": True}).execute()
        send_telegram_text(f"✅ Добавил @{username} в список отслеживания.", chat_id=chat_id)


def handle_remove(username: str, chat_id: str):
    res = supabase.table("ig_accounts").delete().eq("username", username).execute()
    if res.data:
        send_telegram_text(f"🗑 Удалил @{username} из списка.", chat_id=chat_id)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id)


def handle_pause(username: str, chat_id: str):
    res = supabase.table("ig_accounts").update({"active": False}).eq("username", username).execute()
    if res.data:
        send_telegram_text(f"⏸ @{username} поставлен на паузу.", chat_id=chat_id)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id)


def handle_resume(username: str, chat_id: str):
    res = supabase.table("ig_accounts").update({"active": True}).eq("username", username).execute()
    if res.data:
        send_telegram_text(f"▶️ @{username} снова активен.", chat_id=chat_id)
    else:
        send_telegram_text(f"@{username} не найден в списке.", chat_id=chat_id)


def handle_list(chat_id: str):
    res = supabase.table("ig_accounts").select("username, active, last_error").execute()
    accounts = res.data or []
    if not accounts:
        send_telegram_text("Список пуст.", chat_id=chat_id)
        return
    lines = ["📋 Список аккаунтов:\n"]
    for a in sorted(accounts, key=lambda x: x["username"]):
        status = "🟢" if a["active"] else "⏸"
        err = " ⚠️" if a.get("last_error") else ""
        lines.append(f"{status} @{a['username']}{err}")
    send_telegram_text("\n".join(lines), chat_id=chat_id)


def process_message(text: str, chat_id: str):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return
    command = parts[0].lower().split("@")[0]  # strip @botname if present
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
            "Команды:\n"
            "/add username — добавить аккаунт\n"
            "/remove username — удалить аккаунт\n"
            "/pause username — поставить на паузу\n"
            "/resume username — снять с паузы\n"
            "/list — показать все аккаунты",
            chat_id=chat_id,
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
