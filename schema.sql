-- Run this once in Supabase → SQL Editor

create table if not exists ig_accounts (
  id bigint generated always as identity primary key,
  username text unique not null,          -- без @, например "slimway.kz"
  active boolean not null default true,    -- false = временно не проверять
  last_post_id text,                       -- заполняется скриптом автоматически
  last_post_date timestamptz,              -- заполняется скриптом автоматически
  last_checked_at timestamptz,             -- заполняется скриптом автоматически
  last_error text,                         -- заполняется скриптом при сбое
  created_at timestamptz not null default now()
);

-- Пример добавления аккаунтов для отслеживания:
-- insert into ig_accounts (username) values
--   ('account_one'),
--   ('account_two'),
--   ('account_three');


-- История всех найденных постов (для аналитики / отчётов на будущее)
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

-- Служебная таблица для бота-командера (/add, /remove, /pause) —
-- хранит смещение (offset) последнего обработанного сообщения Telegram,
-- чтобы не обрабатывать одно и то же сообщение дважды
create table if not exists bot_state (
  key text primary key,
  value text
);

insert into bot_state (key, value) values ('last_update_id', '0')
on conflict (key) do nothing;
