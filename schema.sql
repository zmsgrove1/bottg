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
