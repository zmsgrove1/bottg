"""
Single Flask app that replaces the GitHub Actions cron jobs, for deploying
to Render as a Web Service. External schedulers (e.g. cron-job.org) call
the /check-posts, /weekly-summary, and /content-summary endpoints on a
schedule; Telegram calls /telegram-webhook instantly on every message
(no more 5-minute polling delay).

Endpoints:
  GET/POST /check-posts?token=...        — run the daily Instagram check
  GET/POST /weekly-summary?token=...      — run the weekly stale-account summary
  GET/POST /content-summary?token=...     — run the content-mix summary
  POST     /telegram-webhook              — Telegram sends updates here instantly
  GET      /                              — health check (also what wakes the
                                             service up from sleep on free tier)
"""

import os
from flask import Flask, request, abort, jsonify

import check_posts
import weekly_summary
import content_summary
import command_bot

app = Flask(__name__)

CRON_SECRET = os.environ.get("CRON_SECRET")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")


def require_cron_secret():
    if not CRON_SECRET:
        # No secret configured — refuse rather than run wide open.
        abort(500, "CRON_SECRET is not configured on the server")
    if request.args.get("token") != CRON_SECRET:
        abort(403)


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "alive"})


@app.route("/check-posts", methods=["GET", "POST"])
def route_check_posts():
    require_cron_secret()
    check_posts.main()
    return jsonify({"status": "ok"})


@app.route("/weekly-summary", methods=["GET", "POST"])
def route_weekly_summary():
    require_cron_secret()
    weekly_summary.main()
    return jsonify({"status": "ok"})


@app.route("/content-summary", methods=["GET", "POST"])
def route_content_summary():
    require_cron_secret()
    content_summary.main()
    return jsonify({"status": "ok"})


@app.route("/telegram-webhook", methods=["POST"])
def route_telegram_webhook():
    # Telegram signs webhook requests with this header if a secret_token
    # was set when calling setWebhook — verify it so randoms can't hit this.
    if TELEGRAM_WEBHOOK_SECRET:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != TELEGRAM_WEBHOOK_SECRET:
            abort(403)

    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message")
    callback = update.get("callback_query")

    if message and "text" in message:
        chat_id = str(message["chat"]["id"])
        command_bot.process_message(message["text"], chat_id)
    elif callback:
        chat_id = str(callback["message"]["chat"]["id"])
        command_bot.process_callback(callback.get("data", ""), chat_id, callback["id"])

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))