# Orbix Database — Root Level Configuration

**All database config at root level, shared by web, mobile, laptop, APK**

## Location

```
orbix/ (root)
├── database/              # <-- Root level database config
│   ├── __init__.py        # Exports get_database_config, get_connection, etc.
│   ├── config.py          # Central config - single source of truth
│   ├── connection.py      # Connection factory - auto-selects backend
│   ├── schema.sql         # Postgres/Supabase schema (root level)
│   ├── sqlite_schema.sql  # SQLite schema (mobile, laptop, offline)
│   └── README.md          # This file
├── data/                  # <-- Root level data directory (default)
│   ├── orbix.sqlite3      # SQLite - paper ledger, settings
│   ├── candles.duckdb     # DuckDB - candle archive (web default)
│   ├── candles.sqlite3    # SQLite candles (fallback, mobile)
│   ├── mobile/            # Mobile APK data
│   │   ├── mobile_candles.db
│   │   └── mobile_ledger.db
│   └── models/            # Trained models
├── .env                   # Root level env - DATABASE_* config
└── orbix/
    ├── database.py        # Re-exports from database/ for easy import
    ├── mobile_db.py       # Mobile SQLite (uses root config)
    └── runtime.py         # Uses database/ factory
```

## Configuration at Root Level (.env)

```bash
# Database backend - auto-detects if not set
DATABASE_BACKEND=auto          # auto, sqlite, duckdb, postgres, supabase
# ORBIX_DATABASE_BACKEND=auto  # alias

# Data directory - at root level
ORBIX_DATA_DIR=./data          # default, at root
# DATABASE_DATA_DIR=./data     # alias
# ORBIX_MOBILE_DATA_DIR=./data/mobile  # for APK

# SQLite (default, works everywhere)
# No extra config needed - uses ORBIX_DATA_DIR/orbix.sqlite3

# DuckDB (web default, analytics)
# Uses ORBIX_DATA_DIR/candles.duckdb
# ORBIX_USE_DUCKDB=1  # force DuckDB

# Postgres (production)
DATABASE_URL=postgres://user:pass@localhost:5432/orbix
# POSTGRES_URL=...  # alias

# Supabase (cloud, free 500MB)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...  # for APK with RLS bypass

# Optional
DATABASE_AUTO_MIGRATE=true
DATABASE_WAL_MODE=true
```

## Backends

| Backend | When Used | File | Pros |
|---------|-----------|------|------|
| **SQLite** | Mobile APK, laptop, offline, default fallback | `data/orbix.sqlite3` | Works everywhere, no native libs, APK, Electron |
| **DuckDB** | Web default, analytics | `data/candles.duckdb` | Fast for candles, columnar, analytics |
| **Postgres** | Production | `DATABASE_URL` | Production, concurrent |
| **Supabase** | Cloud sync | `SUPABASE_URL` | Postgres + Auth + Realtime + free tier |
| **Auto** | Default | Auto-detect | Supabase > Postgres > DuckDB > SQLite |

Auto-detect priority:
1. If `SUPABASE_URL` + `SUPABASE_KEY` set -> Supabase
2. Else if `DATABASE_URL` set -> Postgres
3. Else if `ORBIX_USE_DUCKDB=1` -> DuckDB
4. Else if `ORBIX_MOBILE=1` or `ORBIX_APK=1` -> SQLite
5. Else -> DuckDB (web) with SQLite fallback

## Usage - Root Level Factory

```python
# Single import, works for all backends
from database import get_database_config, get_connection, get_candle_store, get_ledger

# Get config from root .env
config = get_database_config()
print(config.effective_backend)  # auto-detected
print(config.data_dir)           # root/data

# Get connection - auto-selects backend
conn = get_connection()  # SQLite, DuckDB, or Postgres based on config

# Get candle store - root level factory
candle_store = get_candle_store()  # Uses root config
ledger = get_ledger()

# Mobile - all inside APK
from database import get_mobile_store
candle_store, ledger = get_mobile_store()

# Check health
from database.connection import check_database_health, init_database
health = check_database_health()
print(health)  # sqlite_exists, duckdb_exists, supabase_configured, etc.

init = init_database()  # Creates tables if not exist
```

## For Web (FastAPI)

`orbix/runtime.py` now uses root config:

```python
from database import get_database_config, get_candle_store, get_ledger

config = get_database_config()
self.candle_store = get_candle_store(config)
self.ledger = get_ledger(config)
```

## For Mobile APK

`orbix/mobile_db.py` uses root config:

```python
from database import get_database_config

config = get_database_config()
# config.mobile_candles_db = data/mobile/mobile_candles.db
# All inside APK private storage when ORBIX_MOBILE_DATA_DIR set
```

`mobile/src/js/db.js` — SQLite via Capacitor, same tables as root schema.

## For Laptop (Electron)

Electron main.js sets `ORBIX_MOBILE_DATA_DIR` to app userData:

```js
const DATA_DIR = path.join(app.getPath('userData'), 'orbix_data');
env: { ORBIX_MOBILE_DATA_DIR: DATA_DIR }
```

## Schema - Root Level

- `database/schema.sql` — Postgres/Supabase, run in Supabase SQL Editor
- `database/sqlite_schema.sql` — SQLite, auto-applied on init

Both have same tables: `datasets`, `candles`, `signals`, `abstentions`, `settings`, `sync_queue`, etc.

## Init Database at Root

```bash
# Auto-init on startup (default)
python -m database.config  # prints config
python scripts/init_db.py  # creates tables

# Or via Python
from database import init_database
init_database()

# Docker with Postgres at root
docker compose up -d
# Includes postgres service if you use docker-compose.db.yml
```

## Docker Compose - Root Level DB

`docker-compose.yml` at root now has optional Postgres:

```yaml
services:
  orbix:
    environment:
      DATABASE_BACKEND: auto
      ORBIX_DATA_DIR: /data
    volumes:
      - orbix-data:/data

  postgres:  # optional, for local Supabase-like
    image: postgres:15
    environment:
      POSTGRES_DB: orbix
      POSTGRES_USER: orbix
      POSTGRES_PASSWORD: orbix
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./database/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    ports:
      - "5432:5432"

volumes:
  orbix-data:
  postgres-data:
```

## Migration

Old code used direct paths:

```python
# Old
self.candle_store = CandleStore(self.data_dir / "candles.duckdb")
self.ledger = PaperLedger(self.data_dir / "orbix.sqlite3")
```

New code uses root factory:

```python
# New - root level
from database import get_candle_store, get_ledger, get_database_config

config = get_database_config()  # reads root .env
self.candle_store = get_candle_store(config)
self.ledger = get_ledger(config)
```

All new code should use `database/` factory. Old code still works but will be migrated.

## Testing

```bash
python -m unittest tests.test_mobile_db -v  # uses root config
python scripts/init_db.py --check            # health check
```

## Why Root Level?

1. **Single source of truth** — `.env` at root, `database/` at root, `data/` at root
2. **Shared by all** — web, mobile APK, laptop Electron, Docker all use same config
3. **Easy to configure** — one place to set DATABASE_URL or SUPABASE_URL
4. **Offline first** — SQLite at root works everywhere, no native libs
5. **Cloud ready** — set SUPABASE_URL at root, APK syncs when online
