"""
Orbix Database - Root Level Re-export
This file re-exports from database/ at root level for easy import
All database config lives at root level, shared by web, mobile, laptop, APK

Usage:
    from orbix.database import get_database_config, get_candle_store, get_ledger
    # or
    from database import get_database_config

Both work, but database/ at root is canonical
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure root database/ is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from database import (
        DatabaseConfig,
        DatabaseBackend,
        get_database_config,
        get_connection,
        get_candle_store,
        get_ledger,
        get_mobile_store,
    )
    from database.config import get_config, set_config, reload_config
    from database.connection import (
        init_database,
        check_database_health,
        get_sqlite_connection,
        get_duckdb_connection,
        get_postgres_connection,
        get_supabase_client,
    )
except ImportError as e:
    # Fallback if database/ not found (should not happen)
    print(f"[orbix.database] Failed to import from root database/: {e}")
    # Provide minimal fallback
    from pathlib import Path as _Path
    ROOT = _Path(__file__).resolve().parents[1]
    
    class DatabaseBackend:
        SQLITE = "sqlite"
        DUCKDB = "duckdb"
        POSTGRES = "postgres"
        SUPABASE = "supabase"
        AUTO = "auto"
    
    def get_database_config(*args, **kwargs):
        from database.config import get_database_config as _get
        return _get(*args, **kwargs)
    
    def get_candle_store(*args, **kwargs):
        from database.connection import get_candle_store as _get
        return _get(*args, **kwargs)
    
    def get_ledger(*args, **kwargs):
        from database.connection import get_ledger as _get
        return _get(*args, **kwargs)

__all__ = [
    "DatabaseConfig",
    "DatabaseBackend",
    "get_database_config",
    "get_config",
    "set_config",
    "reload_config",
    "get_connection",
    "get_candle_store",
    "get_ledger",
    "get_mobile_store",
    "init_database",
    "check_database_health",
    "get_sqlite_connection",
    "get_duckdb_connection",
    "get_postgres_connection",
    "get_supabase_client",
]
