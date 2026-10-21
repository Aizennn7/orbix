"""
Orbix Supabase Client - Optional cloud sync for APK with local DB
Local SQLite is primary, Supabase is secondary sync when online
Free tier: 500MB, enough for M1 candles
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False
    Client = None


class SupabaseSync:
    """Sync local APK SQLite DB to Supabase when online"""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = url or os.environ.get("SUPABASE_URL")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
        self.client: Optional[Client] = None
        self.enabled = False

        if HAS_SUPABASE and self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
                self.enabled = True
            except Exception as e:
                print(f"[Supabase] Failed to init: {e}")
                self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled and self.client is not None

    def ensure_tables(self) -> bool:
        """Ensure Supabase tables exist - run once"""
        if not self.is_enabled():
            return False
        # Tables should be created via Supabase dashboard SQL editor
        # This is just a check
        try:
            self.client.table("candles").select("id").limit(1).execute()
            return True
        except Exception:
            print("[Supabase] Tables not created yet - run SQL in dashboard")
            return False

    def sync_candles(self, candles: List[dict]) -> int:
        """Push local candles to Supabase"""
        if not self.is_enabled() or not candles:
            return 0
        try:
            # Batch insert, ignore duplicates
            result = self.client.table("candles").upsert(candles, on_conflict="dataset_id,timestamp").execute()
            return len(result.data) if result.data else 0
        except Exception as e:
            print(f"[Supabase] Sync candles failed: {e}")
            return 0

    def sync_signals(self, signals: List[dict]) -> int:
        """Push local signals to Supabase"""
        if not self.is_enabled() or not signals:
            return 0
        try:
            result = self.client.table("signals").upsert(signals, on_conflict="signal_id").execute()
            return len(result.data) if result.data else 0
        except Exception as e:
            print(f"[Supabase] Sync signals failed: {e}")
            return 0

    def fetch_candles(self, dataset_id: str, limit: int = 500) -> List[dict]:
        """Fetch from Supabase (for restore)"""
        if not self.is_enabled():
            return []
        try:
            result = self.client.table("candles").select("*").eq("dataset_id", dataset_id).order("timestamp", desc=False).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"[Supabase] Fetch failed: {e}")
            return []


# SQL to create tables in Supabase dashboard (SQL editor)
SUPABASE_SQL = """
-- Run this in Supabase SQL editor to create tables for Orbix APK sync

create table if not exists datasets (
  dataset_id text primary key,
  pair text not null,
  timeframe text default 'M1',
  candle_count int default 0,
  first_timestamp timestamptz,
  latest_timestamp timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  source text default 'mobile-apk',
  user_id uuid references auth.users
);

create table if not exists candles (
  id bigserial primary key,
  dataset_id text not null references datasets(dataset_id) on delete cascade,
  timestamp timestamptz not null,
  open double precision not null,
  high double precision not null,
  low double precision not null,
  close double precision not null,
  volume double precision default 0,
  unique(dataset_id, timestamp)
);
create index if not exists idx_candles_ds_time on candles(dataset_id, timestamp);

create table if not exists signals (
  signal_id text primary key,
  pair text not null,
  strategy text not null,
  side text not null,
  entry_price double precision not null,
  generated_at timestamptz not null,
  expires_at timestamptz not null,
  probability_up double precision,
  reason text,
  status text default 'PENDING',
  outcome text,
  created_at timestamptz default now(),
  user_id uuid references auth.users
);

create table if not exists settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz default now(),
  user_id uuid references auth.users
);

-- Enable RLS
alter table datasets enable row level security;
alter table candles enable row level security;
alter table signals enable row level security;
alter table settings enable row level security;

-- Policies: user can only see own data
create policy "Users can manage own datasets" on datasets for all using (auth.uid() = user_id);
create policy "Users can manage own candles" on candles for all using (
  exists (select 1 from datasets where datasets.dataset_id = candles.dataset_id and datasets.user_id = auth.uid())
);
create policy "Users can manage own signals" on signals for all using (auth.uid() = user_id);
create policy "Users can manage own settings" on settings for all using (auth.uid() = user_id);

-- For service key (bypass RLS) - APK with service key can write all
-- Use anon key with RLS for user-specific, service key for admin sync
"""

def get_sync_client() -> SupabaseSync:
    return SupabaseSync()
