"""
Second bot: turns this morning's competitor posts into draft creative
ideas for SlimWay, using only posts already collected by the monitoring
bot (reads ig_posts — never touches ig_accounts or the monitoring logic).

Pipeline per qualifying post (currently 100% FREE — no paid API calls;
common.py also has OpenAI-based paid equivalents ready to swap in later
if the free quality isn't good enough once the logic is proven out):
  1. Reference image -> Pollinations vision (free) -> a detailed, structured
     image-generation prompt, following strict SlimWay brand rules (colors,
     layout, fonts, no fake logos, equipment must NOT be AI-drawn, Kazakh/
     Central Asian model adaptation, contacts in a fixed format, prices in
     tenge).
  2. That prompt -> Pollinations image generation (free, no key) -> a rough
     draft image.
  3. Pollinations text (free) -> a ready-to-post Russian caption, following
     SlimWay's terminology rules (no "жиросжигание"/"тренировка"/100%
     guarantees).
  4. All three (draft image, full prompt text, caption) go to the Telegram
     group via a SEPARATE bot (TELEGRAM_BOT_TOKEN_2), so it's visibly a
     different "voice" from the monitoring bot.

"Отзывы" (review-category posts) are skipped — this is for content/creative
inspiration, not testimonial reposting. Only posts with an actual image are
used (reels' video thumbnail counts as an image).

Designed to run ~1–1.5h after check_posts, once per day, as a scheduled job.
"""

import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from common import supabase, send_telegram_text, send_telegram_photo, ask_pollinations_text, ask_pollinations_vision, TELEGRAM_BOT_TOKEN_2

ASTANA_TZ = timezone(timedelta(hours=5))

MAX_POSTS_PER_RUN = 6  # safety cap — don't flood the group in one go

DESIGN_PROMPT_TEMPLATE = """Ты — ассистент по дизайну для бренда SlimWay (фитнес-студия,
Астана, Казахстан). Я прикладываю референс поста. Проанализируй
его и составь детальный промпт для ИИ-генерации изображения,
строго соблюдая следующие правила:

## РАЗМЕР
Холст: 342.19 × 452.97 мм.

## ЦВЕТА (строго, без исключений)
Разрешены только:
- #02BDB6 (бирюзовый) — основной акцентный
- #263CD9 (тёмно-синий) — дополнительный
- Белый (#FFFFFF) и чёрный (#000000)
Любой синий/голубой/циан/зелёный/фиолетовый цвет с референса
заменяется строго на #02BDB6 или #263CD9 — не на похожий оттенок.
Тени/градиенты — только тинты/шейды этих же двух цветов.

## КОМПОЗИЦИЯ И ПОЗИЦИИ
Расположение всех блоков (заголовок, карточки, фото, кнопки,
плашки) — точно как на референсе: те же пропорции, тот же порядок,
та же визуальная иерархия. Не менять баланс композиции, не
переставлять блоки, не менять размер элементов относительно друг
друга без явного запроса.

## ШРИФТЫ
Начертание шрифта — максимально близкое к референсу (жирность,
геометрия, стиль). Ничего не придумывать от себя.

## ТЕКСТ И ЯЗЫК
Весь текст — только на русском. Перевод маркетинговый, не
дословный (передаём смысл и посыл, а не слово в слово). Если
перевод длиннее оригинала — адаптируй вёрстку, не сжимай шрифт
до нечитаемого размера.

## ЛОГОТИПЫ
Любой чужой логотип (текст или графический знак) с референса —
полностью удаляется. Место под ним не закрашивается и не
заполняется ничем новым — фон остаётся как на референсе.
Логотип SlimWay НЕ создаётся, НЕ имитируется — место под него
специально не резервируется, пользователь добавит его сам.

## НАЗВАНИЯ БРЕНДОВ-КОНКУРЕНТОВ
Если в тексте референса встречается название конкурента
(текстом, не как логотип) — заменяется на "SlimWay".

## АППАРАТЫ (устройства)
Если на референсе есть фитнес-аппарат — используется ТОЛЬКО
мой оригинальный файл (не генерируется похожий через ИИ). Файлы:
Vacuactiv.png, infrastep.png, infrashape.png, rollshape.png —
сопоставляй по названию/виду аппарата на референсе. (Примечание:
эта версия промпта генерируется без реальной подстановки файла —
в тексте промпта явно указывается, какой файл нужно наложить
вручную/отдельным шагом постобработки.)

## МОДЕЛИ / ЛЮДИ
Если на референсе есть модель (девушка/человек) — адаптировать
внешность под казахскую/центральноазиатскую этническую внешность,
сохраняя позу, ракурс, одежду и композицию как на референсе.

## КОНТАКТЫ (если есть на референсе)
Заменяются строго в том же месте, где были на референсе:
- Адрес: ул. Ахмета Байтурсынова, 2, НП14, ЖК Qazyna
- Телефон: 8 701 490 60 90
- Сайт: slimway.com.kz
Если контактов на референсе нет — не добавлять самостоятельно.

## ВАЛЮТА
Любая цена — только в тенге (₸).

## ЧТО НЕ ДЕЛАТЬ
- Не добавлять никаких элементов/декора, которых нет на
  референсе или в моём задании
- Не улучшать композицию "от себя"
- Итоговый макет не должен быть точной копией референса 1:1 —
  структура сохраняется, детали (текст/цвета/модели) адаптируются

## ФОРМАТ ВЫВОДА
Выдай готовый промпт для ИИ-генератора изображений, разбитый по
пронумерованным блокам композиции (как в самом референсе, сверху
вниз), с указанием точных цветов, текста и позиций для каждого
элемента. В конце — отдельный блок "ЦВЕТА", "ШРИФТ", "КАЧЕСТВО".
Выведи ТОЛЬКО готовый промпт, без вступлений и пояснений от себя."""

CAPTION_SYSTEM_PROMPT = """Ты — копирайтер бренда SlimWay (премиум-студия коррекции фигуры,
Астана, ЖК Qazyna). Пиши только на русском.

Строгая терминология:
- "сеанс", НИКОГДА не "тренировка"
- НИКОГДА не используй слова "жиросжигание", "фитнес" в контексте услуг,
  и не давай гарантий 100% результата
- Позиционирование: технологичная альтернатива фитнесу, биохакинг,
  коррекция тела (не медицина)
- Цены в тексте не называть, если явно не попросили
- Тон — уверенный, без воды, коротко

Напиши готовую подпись для Instagram-поста (2-4 предложения + 3-5
хэштегов), опираясь на присланный референс поста конкурента (тема/повод),
но полностью адаптированную под SlimWay."""


def get_today_start_utc():
    now_local = datetime.now(ASTANA_TZ)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start_local.astimezone(timezone.utc)


def get_candidate_posts():
    """Posts discovered by the monitoring bot today (Astana time), excluding
    reviews, that actually have an image."""
    today_start_utc = get_today_start_utc()
    res = (
        supabase.table("ig_posts")
        .select("username, url, image_url, caption, post_date, content_type, category, discovered_at")
        .neq("category", "review")
        .gte("discovered_at", today_start_utc.isoformat())
        .execute()
    )
    posts = [p for p in (res.data or []) if p.get("image_url")]
    return posts[:MAX_POSTS_PER_RUN]


def generate_draft_image_url(prompt: str) -> str:
    encoded = urllib.parse.quote(prompt[:1800])
    return f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1430&model=flux&nologo=true"


def process_post(post: dict):
    username = post["username"]
    ref_image = post["image_url"]

    design_prompt = ask_pollinations_vision(ref_image, DESIGN_PROMPT_TEMPLATE)
    if not design_prompt:
        design_prompt = (
            "(бесплатный анализ картинки сейчас не сработал — попробуйте ещё раз позже, "
            "или подключите платный GPT-vision как запасной вариант)"
        )

    caption_source = post.get("caption") or "(без исходного текста)"
    caption = ask_pollinations_text(
        f"Референс-пост (оригинальный текст, может быть на другом языке):\n{caption_source}",
        system=CAPTION_SYSTEM_PROMPT,
        max_tokens=400,
    )
    if not caption:
        caption = "(бесплатная генерация подписи сейчас не сработала — попробуйте ещё раз позже)"

    draft_image_url = generate_draft_image_url(design_prompt)

    intro = f"💡 Идея по мотивам @{username} ({post.get('content_type', 'post')})\nРеференс: {post['url']}"
    send_telegram_text(intro, bot_token=TELEGRAM_BOT_TOKEN_2)

    resp = send_telegram_photo(
        draft_image_url,
        "🎨 Черновой вариант (Pollinations, не финал)",
        bot_token=TELEGRAM_BOT_TOKEN_2,
    )
    if resp.status_code != 200:
        send_telegram_text(
            "(не получилось прислать черновую картинку — вот только промпт ниже)",
            bot_token=TELEGRAM_BOT_TOKEN_2,
        )

    # Prompt text may be long — Telegram caps messages at 4096 chars.
    prompt_msg = f"📋 Промпт для генератора:\n\n{design_prompt}"
    for i in range(0, len(prompt_msg), 3800):
        send_telegram_text(prompt_msg[i:i + 3800], bot_token=TELEGRAM_BOT_TOKEN_2)

    send_telegram_text(f"✍️ Черновая подпись к посту:\n\n{caption}", bot_token=TELEGRAM_BOT_TOKEN_2)


def main():
    posts = get_candidate_posts()

    if not posts:
        send_telegram_text(
            "Сегодня утром нет подходящих постов (без учёта отзывов) для идей.",
            bot_token=TELEGRAM_BOT_TOKEN_2,
        )
        return

    for post in posts:
        try:
            process_post(post)
        except Exception as e:
            send_telegram_text(
                f"❌ Не получилось обработать пост @{post['username']}: {type(e).__name__}: {e}",
                bot_token=TELEGRAM_BOT_TOKEN_2,
            )
        time.sleep(2)


if __name__ == "__main__":
    main()