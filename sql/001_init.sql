-- Coach-Bot Initial Schema (Supabase / Postgres)
-- Run via Supabase SQL Editor

-- =========================================================
-- COACHES
-- =========================================================
create table coaches (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text unique not null,
  telegram_chat_id bigint unique,          -- for admin notifications
  brand_voice text,                         -- "locker, motivierend, du-Form" etc.
  default_currency text default 'EUR',
  is_active boolean default true,
  created_at timestamptz default now()
);

-- =========================================================
-- CUSTOMERS
-- =========================================================
create table customers (
  id uuid primary key default gen_random_uuid(),
  coach_id uuid not null references coaches(id) on delete restrict,
  telegram_chat_id bigint unique not null,
  telegram_username text,
  first_name text not null,
  status text not null default 'intake'
    check (status in ('intake', 'active', 'paused', 'archived')),
  onboarded_at timestamptz,
  created_at timestamptz default now()
);
create index idx_customers_coach on customers(coach_id);

-- =========================================================
-- CUSTOMER PROFILES (intake result + macro targets)
-- =========================================================
create table customer_profiles (
  customer_id uuid primary key references customers(id) on delete cascade,
  age int,
  gender text check (gender in ('m', 'f', 'd')),
  height_cm int,
  weight_start_kg numeric(5,2),
  weight_target_kg numeric(5,2),
  goal text check (goal in ('abnehmen', 'muskelaufbau', 'erhalt', 'ausdauer')),
  experience_level text check (experience_level in ('anfaenger', 'fortgeschritten', 'profi')),
  equipment text check (equipment in ('home_none', 'home_basic', 'gym')),
  allergies text[],
  food_preferences text[],
  -- Macro targets set manually by coach:
  daily_kcal_target int,
  protein_target_g int,
  carbs_target_g int,
  fat_target_g int,
  notes text,
  updated_at timestamptz default now()
);

-- =========================================================
-- FOOD LOGS (text-based, parsed by Claude)
-- =========================================================
create table food_logs (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  logged_at timestamptz default now(),
  meal_type text check (meal_type in ('fruehstueck', 'mittag', 'abend', 'snack')),
  raw_description text not null,           -- "2 Eier, 1 Scheibe Vollkorntoast, 10g Butter"
  parsed_items jsonb,                      -- [{"item":"Ei","qty":2,"kcal":156,"protein":13,...}]
  total_kcal int,
  protein_g numeric(5,1),
  carbs_g numeric(5,1),
  fat_g numeric(5,1)
);
create index idx_food_logs_customer_date on food_logs(customer_id, logged_at desc);

-- =========================================================
-- WEEKLY CHECK-INS
-- =========================================================
create table checkins (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  week_of date not null,                   -- Monday of the week
  weight_kg numeric(5,2),
  waist_cm numeric(5,1),
  hip_cm numeric(5,1),
  energy_rating int check (energy_rating between 1 and 10),
  sleep_rating int check (sleep_rating between 1 and 10),
  mood_rating int check (mood_rating between 1 and 10),
  notes text,
  created_at timestamptz default now(),
  unique(customer_id, week_of)
);
create index idx_checkins_customer_week on checkins(customer_id, week_of desc);

-- =========================================================
-- MESSAGES (chat history for Claude context + coach dashboard)
-- =========================================================
create table messages (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  direction text not null check (direction in ('in', 'out')),
  content text not null,
  agent_name text,                         -- 'intake', 'food_log', 'checkin', 'progress'
  model_used text,                         -- 'claude-haiku-4-5', 'claude-sonnet-4-6'
  tokens_used int,                         -- cost tracking per message
  created_at timestamptz default now()
);
create index idx_messages_customer_time on messages(customer_id, created_at desc);

-- =========================================================
-- SCHEDULED REMINDERS
-- =========================================================
create table scheduled_reminders (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  reminder_type text not null,             -- 'weekly_checkin', 'missed_log', 'motivation'
  scheduled_for timestamptz not null,
  sent_at timestamptz,
  created_at timestamptz default now()
);
create index idx_reminders_pending on scheduled_reminders(scheduled_for) where sent_at is null;

-- =========================================================
-- CONVERSATION STATE (for multi-step flows like intake)
-- =========================================================
create table conversation_states (
  customer_id uuid primary key references customers(id) on delete cascade,
  current_flow text,                       -- 'intake', 'checkin', null = free chat
  current_step text,                       -- 'ask_age', 'ask_weight', etc.
  state_data jsonb default '{}'::jsonb,    -- accumulated answers
  updated_at timestamptz default now()
);
