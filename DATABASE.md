# Orbix Database — Root Level Configuration

**All database configuration at root level — single source of truth for the web and mobile clients**

## 📁 Root Level Structure

```
orbix/ (project root)
├── database/                  # <-- ROOT LEVEL DATABASE CONFIG (NEW)
│   ├── __init__.py            # Main exports: get_database_config, get_connection
│   ├── config.py              # Central config - reads .env at root
│   ├── connection.py          # Connection factory - auto backend
│   ├── schema.sql             # Postgres/Supabase schema (root)
│   ├── sqlite_schema.sql      # SQLite schema (mobile, laptop, offline)
│   └── README.md
│
├── data/                      # <-- ROOT LEVEL DATA (default)
│   ├── orbix.sqlite3          # SQLite - ledger, settings
│   ├── candles.duckdb         # DuckDB - candles (web)
│   ├── candles.sqlite3        # SQLite fallback
│   ├── mobile/                # Mobile client data
│   │   ├── mobile_candles.db  # Mobile candle cache
│   │   └── mobile_ledger.db   # Mobile signal cache
│   └── models/                # Trained models
│
├── .env                       # <-- ROOT LEVEL ENV (DATABASE_*)
├── docker-compose.yml         # <-- ROOT LEVEL - uses DATABASE_BACKEND
├── orbix/
│   ├── database.py            # Re-exports from database/ for easy import
│   ├── runtime.py             # NOW USES database/ factory (root)
│   ├── mobile_db.py           # Uses root config
│   └── mobile_api.py          # Uses root config
└── scripts/
    └── init_db.py             # Init DB at root level
```

## ⚙️ Configuration at Root (.env)

All DB config in root `.env` — shared by the web and mobile clients, and Docker:

```bash
# === DATABASE AT ROOT LEVEL ===
ORBIX_DATA_DIR=./data                    # Root data dir
DATABASE_BACKEND=auto                    # auto, sqlite, duckdb, postgres, supabase

# SQLite (default, works everywhere)
DATABASE_WAL_MODE=true
DATABASE_AUTO_MIGRATE=true

# DuckDB (web analytics)
# Uses data/candles.duckdb

# Postgres (production)
DATABASE_URL=postgres://orbix:orbix@localhost:5432/orbix

# Supabase (cloud, free 500MB) - optional sync
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
```

## 🔄 Backends - Auto Detect at Root

`DATABASE_BACKEND=auto` (default) auto-detects:

1. **Supabase** if `SUPABASE_URL` + `SUPABASE_KEY` set → cloud sync
2. **Postgres** if `DATABASE_URL` set → production
3. **DuckDB** if `ORBIX_USE_DUCKDB=1` → web analytics
4. **SQLite** if `ORBIX_MOBILE=1` → mobile client
5. **DuckDB** default for web, SQLite fallback

## 🚀 Usage - Root Factory

```python
# Single import, works everywhere
from database import get_database_config, get_candle_store, get_ledger, init_database

# Config from root .env
config = get_database_config()
print(config.data_dir)  # /home/user/orbix/data (root)
print(config.effective_backend)  # auto-detected

# Connections - auto backend
from database import get_connection
conn = get_connection()  # SQLite, DuckDB, or Postgres

# Stores - root factory
candle_store = get_candle_store()  # Uses root config
ledger = get_ledger()

# Mobile client - same root config
from database import get_mobile_store
candle_store, ledger = get_mobile_store()  # data/mobile/ at root

# Health & Init at root
from database.connection import check_database_health, init_database

health = check_database_health()
# {'sqlite_exists': True, 'duckdb_exists': False, 'supabase_configured': False, ...}

init = init_database()  # Creates tables at root data/
```

## 📱 Mobile Client Uses Root Config

`orbix/mobile_db.py` now reads root `database/`:

```python
from database import get_database_config
config = get_database_config()
# config.mobile_candles_db = data/mobile/mobile_candles.db
# Override the location with ORBIX_MOBILE_DATA_DIR
```

The browser client (`mobile/src/js/db.js`) caches the same entities in IndexedDB.

## 💻 Docker at Root

`docker-compose.yml` at root uses `DATABASE_BACKEND`:

```yaml
services:
  orbix:
    environment:
      ORBIX_DATA_DIR: /data
      DATABASE_BACKEND: ${DATABASE_BACKEND:-auto}
      DATABASE_URL: ${DATABASE_URL:-}
      SUPABASE_URL: ${SUPABASE_URL:-}
    volumes:
      - orbix-data:/data

  postgres:  # optional, profile db
    image: postgres:15-alpine
    volumes:
      - ./database/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
```

Run:
```bash
docker compose up -d                    # SQLite/DuckDB at root
docker compose --profile db up -d       # With Postgres at root
DATABASE_URL=postgres://... docker compose up -d  # Force Postgres
```

## 🛠️ Init at Root

```bash
python scripts/init_db.py --check      # Health check
python scripts/init_db.py              # Init tables at root data/
python scripts/init_db.py --backend sqlite  # Force SQLite at root
python scripts/init_db.py --reset      # Delete data/ and re-init (danger)
```

## 📊 Schema at Root

- `database/schema.sql` — Postgres/Supabase (run in Supabase dashboard)
- `database/sqlite_schema.sql` — SQLite (auto-applied)

Same tables: `datasets`, `candles`, `signals`, `abstentions`, `settings`, `sync_queue`, etc.

## 🔗 Why Root Level?

1. **Single source of truth** — `.env` at root, `database/` at root, `data/` at root
2. **Shared by all** — web, mobile PWA, Electron and Docker all use the same root config
3. **Easy to configure** — set `DATABASE_URL` once at root, all apps use it
4. **Offline first** — SQLite at root works everywhere, no native libs
5. **Cloud ready** — set `SUPABASE_URL` at root and clients sync when online

## ✅ Migration Done

- `orbix/runtime.py` now uses `database/` factory (root)
- `orbix/mobile_db.py` uses root config
- `orbix/mobile_api.py` uses root config
- `orbix/database.py` re-exports from root `database/`
- `docker-compose.yml` uses root `DATABASE_BACKEND`
- `.env.example` has DATABASE section at root

All new code should use `from database import ...` (root level).
