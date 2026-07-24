# Instagram Post Monitor → Telegram

Проверяет список Instagram-аккаунтов и присылает в Telegram фото + ссылку
на новые посты. Плюс: еженедельная сводка "молчунов" и управление списком
аккаунтов прямо командами в Telegram.

Всё работает на **GitHub Actions** (бесплатно, без отдельного сервера) +
**Supabase** (хранение состояния).

## Три независимых workflow'а

| Файл | Что делает | Расписание |
|---|---|---|
| `.github/workflows/check_posts.yml` | Проверяет все активные аккаунты, шлёт новые посты (фото+ссылка) | Раз в сутки, 07:30 по Астане |
| `.github/workflows/weekly_summary.yml` | Список аккаунтов без новых постов 7+ дней | По понедельникам, 11:00 по Астане |
| `.github/workflows/command_bot.yml` | Обрабатывает команды `/add`, `/remove`, `/pause`, `/resume`, `/list` | Раз в 5 минут |

Все три читают одни и те же 4 секрета (`SUPABASE_URL`, `SUPABASE_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) — их уже не нужно настраивать
заново, если репозиторий у вас тот же.

## Команды в Telegram

Напишите боту (в тот же чат, что уже используете):

```
/add username        — добавить аккаунт в отслеживание
/remove username      — удалить аккаунт из списка
/pause username       — временно приостановить проверку (без потери истории)
/resume username       — снова включить проверку
/list                 — список всех аккаунтов и их статус
```

⚠️ Так как это не постоянно висящий сервер, а проверка раз в 5 минут —
ответ на команду приходит не мгновенно, а в течение 5 минут.

## Что изменилось в структуре кода

- `common.py` — общие функции (подключение к Supabase, отправка в Telegram) —
  используется всеми тремя скриптами, чтобы не дублировать код
- `check_posts.py` — основной скрипт проверки
- `weekly_summary.py` — еженедельная сводка
- `command_bot.py` — обработка команд

## Что нужно обновить в Supabase

Так как добавились новые таблицы, выполните в SQL Editor (можно поверх
существующей базы — команды безопасны для повторного запуска):

```sql
create table if not exists ig_posts (
  id bigint generated always as identity primary key,
  username text not null,
  shortcode text not null,
  url text not null,
  image_url text,
  caption text,
  post_date timestamptz,
  discovered_at timestamptz not null default now(),
  unique (username, shortcode)
);

create table if not exists bot_state (
  key text primary key,
  value text
);

insert into bot_state (key, value) values ('last_update_id', '0')
on conflict (key) do nothing;
```

(Полный актуальный `schema.sql` тоже обновлён — там то же самое плюс
исходная таблица `ig_accounts`.)

## Настройка (если разворачиваете с нуля)

### Шаг 1 — Supabase
1. Создать проект на supabase.com
2. Выполнить `schema.sql` в SQL Editor
3. Добавить аккаунты через `insert into ig_accounts (username) values (...)`
   — или потом через команду `/add` в Telegram
4. Взять `Project URL` (`SUPABASE_URL`) и `service_role` key (`SUPABASE_KEY`)
   из Project Settings → API

### Шаг 2 — Telegram-бот
1. `@BotFather` → `/newbot` → получить токен
2. Написать боту любое сообщение
3. Узнать `chat_id` через `https://api.telegram.org/bot<ТОКЕН>/getUpdates`

### Шаг 3 — GitHub
1. Залить всю папку в репозиторий
2. **Settings → Secrets and variables → Actions** — добавить 4 секрета:
   `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. Готово — все три workflow подхватятся автоматически по расписанию,
   либо запускайте вручную кнопкой **Run workflow** во вкладке Actions

⚠️ **Важно:** токен для `git push`, которым заливаете `.github/workflows/`,
должен иметь scope **workflow** (не только `repo`) — иначе GitHub отклонит
пуш файлов внутри этой папки.

## Важно про надёжность Instagram-доступа

Скрипт работает без логина в Instagram (анонимно). При 35-40 аккаунтах раз
в сутки с паузами это обычно стабильно, но Instagram может периодически
ужесточать анонимный доступ. Если начнутся частые ошибки в отчётах — есть
два запасных варианта (логин через отдельный аккаунт, или платный API) —
дайте знать, если до этого дойдёт.

## Первый запуск после обновления

Так как структура аккаунтов не поменялась, `check_posts.yml` продолжит
работать с уже сохранённым состоянием — новые фото начнут приходить сразу,
без повторного "обнуления" точки отсчёта.
