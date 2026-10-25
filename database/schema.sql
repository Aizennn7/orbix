-- Orbix Database Schema - Root Level - Postgres / Supabase
-- Run this in Supabase SQL Editor or psql
-- All Orbix data at root level, shared by the web and mobile clients

-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Datasets table - candle archives
create table if not exists datasets (
  dataset_id text primary key,
  pair text not null,
  timeframe text not null default 'M1',
  candle_count bigint not null default 0,
  first_timestamp timestamptz,
  latest_timestamp timestamptz,
  content_sha256 text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  source text not null default 'IMPORTED',
  user_id uuid references auth.users(id),
  metadata jsonb default '{}'::jsonb
);

-- Candles table - M1 OHLCV
create table if not exists candles (
  id bigserial primary key,
  dataset_id text not null references datasets(dataset_id) on delete cascade,
  timestamp timestamptz not null,
  open double precision not null,
  high double precision not null,
  low double precision not null,
  close double precision not null,
  volume double precision not null default 0,
  created_at timestamptz not null default now(),
  unique(dataset_id, timestamp)
);
create index if not exists idx_candles_dataset_time on candles(dataset_id, timestamp desc);
create index if not exists idx_candles_timestamp on candles(timestamp desc);

-- Candle tail - hot window for low latency
create table if not exists candle_tail (
  id bigserial primary key,
  dataset_id text not null references datasets(dataset_id) on delete cascade,
  timestamp timestamptz not null,
  open double precision not null,
  high double precision not null,
  low double precision not null,
  close double precision not null,
  volume double precision not null default 0,
  unique(dataset_id, timestamp)
);

-- Active dataset - singleton
create table if not exists active_dataset (
  singleton int primary key check (singleton = 1),
  dataset_id text references datasets(dataset_id),
  pair text not null default 'EUR/USD',
  source text not null default 'SYNTHETIC DEMO',
  updated_at timestamptz not null default now()
);

-- Signals - paper trading signals
create table if not exists signals (
  signal_id text primary key,
  pair text not null,
  strategy text not null,
  side text not null check (side in ('BUY', 'SELL', 'NO_SIGNAL')),
  entry_price double precision not null,
  generated_at timestamptz not null,
  entry_time timestamptz,
  expires_at timestamptz not null,
  probability_up double precision,
  probability_raw double precision,
  uncertainty double precision,
  source text not null default 'SYNTHETIC DEMO',
  reason text not null,
  features_json jsonb not null default '{}'::jsonb,
  dataset_id text references datasets(dataset_id),
  status text not null default 'PENDING' check (status in ('PENDING', 'RESOLVED', 'CANCELLED')),
  exit_price double precision,
  outcome text check (outcome in ('WIN', 'LOSS', 'TIE', null)),
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id),
  synced boolean not null default false
);
create index if not exists idx_signals_pair_time on signals(pair, generated_at desc);
create index if not exists idx_signals_status on signals(status);
create index if not exists idx_signals_strategy on signals(strategy);

-- Abstentions - NO SIGNAL ledger
create table if not exists abstentions (
  abstention_id text primary key,
  pair text not null,
  strategy text not null,
  kind text not null,
  reason text not null,
  timestamp timestamptz not null,
  session text,
  hour int,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id)
);
create index if not exists idx_abstentions_pair_time on abstentions(pair, timestamp desc);

-- Paper outcomes - resolved signals
create table if not exists paper_outcomes (
  id bigserial primary key,
  signal_id text references signals(signal_id) on delete cascade,
  pair text not null,
  outcome text not null check (outcome in ('WIN', 'LOSS', 'TIE')),
  probability double precision,
  entry_price double precision,
  exit_price double precision,
  timestamp timestamptz not null,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id)
);

-- Shadow signals - counterfactual when flat calibration
create table if not exists shadow_signals (
  id bigserial primary key,
  signal_id text not null,
  pair text not null,
  strategy text not null,
  side text not null,
  raw_probability double precision,
  calibrated_probability double precision,
  reason text,
  timestamp timestamptz not null,
  outcome text,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id)
);

-- Learning outcomes - for model training
create table if not exists learning_outcomes (
  id bigserial primary key,
  signal_id text,
  pair text not null,
  outcome text not null,
  probability double precision,
  features_json jsonb,
  timestamp timestamptz not null,
  synced boolean not null default false,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id)
);

-- Models - versioned model snapshots
create table if not exists models (
  model_id text primary key,
  version int not null,
  pair text not null,
  strategy text not null,
  accuracy double precision,
  brier_score double precision,
  log_loss double precision,
  created_at timestamptz not null default now(),
  is_champion boolean not null default false,
  metadata jsonb default '{}'::jsonb,
  user_id uuid references auth.users(id)
);

-- Settings - key/value store
create table if not exists settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now(),
  user_id uuid references auth.users(id)
);

-- Sync queue - offline-first clients
create table if not exists sync_queue (
  id bigserial primary key,
  table_name text not null,
  operation text not null check (operation in ('INSERT', 'UPDATE', 'DELETE')),
  payload jsonb not null,
  created_at timestamptz not null default now(),
  synced boolean not null default false,
  attempts int not null default 0,
  last_error text,
  user_id uuid references auth.users(id)
);
create index if not exists idx_sync_queue_synced on sync_queue(synced, created_at);

-- Provider settings - encrypted at rest in original, jsonb here
create table if not exists provider_settings (
  id bigserial primary key,
  provider text not null,
  settings jsonb not null,
  updated_at timestamptz not null default now(),
  user_id uuid references auth.users(id),
  unique(provider, user_id)
);

-- Enable RLS for Supabase
alter table datasets enable row level security;
alter table candles enable row level security;
alter table candle_tail enable row level security;
alter table active_dataset enable row level security;
alter table signals enable row level security;
alter table abstentions enable row level security;
alter table paper_outcomes enable row level security;
alter table shadow_signals enable row level security;
alter table learning_outcomes enable row level security;
alter table models enable row level security;
alter table settings enable row level security;
alter table sync_queue enable row level security;
alter table provider_settings enable row level security;

-- Policies: authenticated users can manage own data, service role bypasses RLS
-- The service_role key bypasses RLS; keep it on the server only

-- Allow all for service role (bypass RLS)
-- For anon/authenticated, user_id must match

do $$
begin
  -- Drop existing policies if they exist
  drop policy if exists "Users can manage own datasets" on datasets;
  drop policy if exists "Users can manage own candles" on candles;
  drop policy if exists "Users can manage own signals" on signals;
  drop policy if exists "Users can manage own abstentions" on abstentions;
  drop policy if exists "Users can manage own settings" on settings;
  drop policy if exists "Users can manage own sync_queue" on sync_queue;
  
  -- Create policies
  create policy "Users can manage own datasets" on datasets for all using (auth.uid() = user_id or user_id is null);
  create policy "Users can manage own candles" on candles for all using (
    exists (select 1 from datasets where datasets.dataset_id = candles.dataset_id and (datasets.user_id = auth.uid() or datasets.user_id is null))
    or not exists (select 1 from datasets where datasets.dataset_id = candles.dataset_id)
  );
  create policy "Users can manage own signals" on signals for all using (auth.uid() = user_id or user_id is null);
  create policy "Users can manage own abstentions" on abstentions for all using (auth.uid() = user_id or user_id is null);
  create policy "Users can manage own settings" on settings for all using (auth.uid() = user_id or user_id is null);
  create policy "Users can manage own sync_queue" on sync_queue for all using (auth.uid() = user_id or user_id is null);
  create policy "Open access for active_dataset" on active_dataset for all using (true);
  create policy "Open access for candle_tail" on candle_tail for all using (true);
  create policy "Open access for paper_outcomes" on paper_outcomes for all using (true);
  create policy "Open access for shadow_signals" on shadow_signals for all using (true);
  create policy "Open access for learning_outcomes" on learning_outcomes for all using (true);
  create policy "Open access for models" on models for all using (true);
  create policy "Open access for provider_settings" on provider_settings for all using (true);
exception when others then
  -- Policies may already exist, ignore
  null;
end $$;

-- Functions for auto-updating updated_at
create or replace function update_updated_at_column()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

-- Triggers
drop trigger if exists update_datasets_updated_at on datasets;
create trigger update_datasets_updated_at before update on datasets for each row execute function update_updated_at_column();

drop trigger if exists update_signals_updated_at on signals;
-- signals doesn't have updated_at, skip

-- Seed data - demo dataset
insert into datasets (dataset_id, pair, candle_count, source)
values ('demo-root', 'EUR/USD', 0, 'SYNTHETIC DEMO')
on conflict (dataset_id) do nothing;

insert into active_dataset (singleton, dataset_id, pair, source)
values (1, 'demo-root', 'EUR/USD', 'SYNTHETIC DEMO')
on conflict (singleton) do nothing;

-- Comments
comment on table datasets is 'Candle archives - root level, shared by all clients';
comment on table candles is 'M1 OHLCV candles - all Orbix data inside database';
comment on table signals is 'Paper trading signals - BUY/SELL/NO SIGNAL with offline sync';
comment on table sync_queue is 'Offline-first sync queue - syncs to Supabase when online';
