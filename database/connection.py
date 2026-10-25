"""
Orbix Database Connection - Root Level Factory
Single entry point for all database connections across the web and mobile clients
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Union

from .config import DatabaseConfig, DatabaseBackend, get_config, get_database_config

# Lazy imports - only import when needed
_candle_store_cache = {}
_ledger_cache = {}
_lock = threading.RLock()


def get_connection(backend: Optional[Union[str, DatabaseBackend]] = None, config: Optional[DatabaseConfig] = None):
    """
    Get database connection - root level factory
    Auto-selects backend based on config
    """
    if config is None:
        config = get_config()
    
    if backend is not None:
        if isinstance(backend, str):
            try:
                backend = DatabaseBackend(backend.lower())
            except ValueError:
                backend = config.effective_backend
        # Override config backend
        config.backend = backend
    
    effective = config.effective_backend
    
    if effective == DatabaseBackend.POSTGRES or effective == DatabaseBackend.SUPABASE:
        return get_postgres_connection(config)
    elif effective == DatabaseBackend.DUCKDB:
        return get_duckdb_connection(config)
    else:
        return get_sqlite_connection(config)


def get_sqlite_connection(config: Optional[DatabaseConfig] = None) -> sqlite3.Connection:
    """SQLite connection - default for mobile, laptop, offline"""
    if config is None:
        config = get_config()
    
    db_path = config.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    if config.wal_mode:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
        except Exception:
            pass
    
    return conn


def get_duckdb_connection(config: Optional[DatabaseConfig] = None):
    """DuckDB connection - analytics, web default"""
    if config is None:
        config = get_config()
    
    try:
        import duckdb
    except ImportError:
        # Fallback to SQLite if DuckDB is not available
        print("[DB] DuckDB not available, fallback to SQLite")
        return get_sqlite_connection(config)
    
    db_path = config.duckdb_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = duckdb.connect(str(db_path))
    return conn


def get_postgres_connection(config: Optional[DatabaseConfig] = None):
    """Postgres/Supabase connection"""
    if config is None:
        config = get_config()
    
    if not config.is_postgres and not config.is_supabase:
        # Fallback
        return get_sqlite_connection(config)
    
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        dsn = config.database_url
        if not dsn and config.supabase_url:
            # Build DSN from Supabase URL if needed
            # Supabase URL is https://xxx.supabase.co, need DB URL
            # For now, use supabase client instead
            return get_supabase_client(config)
        
        conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
        return conn
    except ImportError:
        print("[DB] psycopg2 not installed, trying supabase")
        return get_supabase_client(config)
    except Exception as e:
        print(f"[DB] Postgres connection failed: {e}, fallback to SQLite")
        return get_sqlite_connection(config)


def get_supabase_client(config: Optional[DatabaseConfig] = None):
    """Supabase client"""
    if config is None:
        config = get_config()
    
    try:
        from supabase import create_client
        if config.supabase_url and config.supabase_key:
            client = create_client(config.supabase_url, config.supabase_key)
            return client
    except ImportError:
        print("[DB] supabase-py not installed")
    except Exception as e:
        print(f"[DB] Supabase client failed: {e}")
    
    return get_sqlite_connection(config)


def get_candle_store(config: Optional[DatabaseConfig] = None, backend: Optional[str] = None):
    """
    Get candle store - root level factory
    Used by runtime.py, mobile, etc.
    """
    if config is None:
        config = get_config()
    
    if backend:
        try:
            config.backend = DatabaseBackend(backend.lower())
        except ValueError:
            pass
    
    effective = config.effective_backend
    cache_key = f"{effective.value}:{config.data_dir}"
    
    with _lock:
        if cache_key in _candle_store_cache:
            return _candle_store_cache[cache_key]
        
        if effective == DatabaseBackend.SQLITE or config.is_postgres or config.is_supabase:
            # For SQLite backend, use MobileCandleStore or SQLite version
            # Check if mobile mode
            if os.environ.get("ORBIX_MOBILE") or "mobile" in str(config.data_dir):
                from orbix.mobile_db import MobileCandleStore
                store = MobileCandleStore(config.mobile_candles_db if hasattr(config, 'mobile_candles_db') else config.sqlite_candles_path)
            else:
                # Use regular CandleStore but with SQLite fallback
                try:
                    from orbix.candle_store import CandleStore
                    # If DuckDB not available, CandleStore will fallback internally or we use mobile
                    store = CandleStore(config.duckdb_path)
                except Exception:
                    from orbix.mobile_db import MobileCandleStore
                    store = MobileCandleStore(config.sqlite_candles_path)
        else:
            # DuckDB default
            try:
                from orbix.candle_store import CandleStore
                store = CandleStore(config.duckdb_path)
            except ImportError:
                from orbix.mobile_db import MobileCandleStore
                store = MobileCandleStore(config.sqlite_candles_path)
        
        _candle_store_cache[cache_key] = store
        return store


def get_ledger(config: Optional[DatabaseConfig] = None, backend: Optional[str] = None):
    """Get ledger - root level factory"""
    if config is None:
        config = get_config()
    
    if backend:
        try:
            config.backend = DatabaseBackend(backend.lower())
        except ValueError:
            pass
    
    cache_key = f"ledger:{config.data_dir}"
    
    with _lock:
        if cache_key in _ledger_cache:
            return _ledger_cache[cache_key]
        
        effective = config.effective_backend
        
        if effective == DatabaseBackend.SQLITE or "mobile" in str(config.data_dir) or os.environ.get("ORBIX_MOBILE"):
            from orbix.mobile_db import MobileLedger
            ledger = MobileLedger(config.mobile_ledger_db if hasattr(config, 'mobile_ledger_db') else config.sqlite_path)
        else:
            try:
                from orbix.ledger import PaperLedger
                ledger = PaperLedger(config.sqlite_path)
            except Exception:
                from orbix.mobile_db import MobileLedger
                ledger = MobileLedger(config.sqlite_path)
        
        _ledger_cache[cache_key] = ledger
        return ledger


def get_mobile_store(config: Optional[DatabaseConfig] = None):
    """Get the mobile client stores."""
    if config is None:
        config = get_config()
    
    from orbix.mobile_db import get_mobile_store as _get_mobile
    data_dir = config.mobile_data_dir if hasattr(config, 'mobile_data_dir') else config.data_dir / "mobile"
    return _get_mobile(data_dir)


def init_database(config: Optional[DatabaseConfig] = None) -> dict:
    """
    Initialize database at root level - creates all tables
    Called on startup, idempotent
    """
    if config is None:
        config = get_config()
    
    result = {
        "backend": config.effective_backend.value,
        "data_dir": str(config.data_dir),
        "status": "ok",
        "tables": []
    }
    
    try:
        # Ensure data dir exists
        config.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize SQLite (always, as fallback)
        conn = get_sqlite_connection(config)
        with conn:
            # Read schema
            schema_path = Path(__file__).parent / "sqlite_schema.sql"
            if schema_path.exists():
                schema = schema_path.read_text()
                conn.executescript(schema)
                result["tables"].append("sqlite: from schema.sql")
            else:
                # Create minimal tables
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS datasets (
                        dataset_id TEXT PRIMARY KEY,
                        pair TEXT NOT NULL,
                        candle_count INTEGER DEFAULT 0,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
                result["tables"].append("sqlite: minimal")
        
        # Initialize DuckDB if backend is duckdb
        if config.effective_backend == DatabaseBackend.DUCKDB:
            try:
                duck_conn = get_duckdb_connection(config)
                # DuckDB init is in CandleStore
                result["tables"].append("duckdb: via CandleStore")
            except Exception as e:
                result["tables"].append(f"duckdb: failed {e}")
        
        # For Postgres/Supabase, tables created via schema.sql in dashboard
        if config.is_postgres or config.is_supabase:
            result["tables"].append("postgres/supabase: use database/schema.sql in dashboard")
            result["supabase_url"] = bool(config.supabase_url)
        
        # Mobile DB
        try:
            candle_store, ledger = get_mobile_store(config)
            result["tables"].append("mobile: SQLite")
        except Exception as e:
            result["tables"].append(f"mobile: failed {e}")
        
    except Exception as e:
        result["status"] = f"error: {e}"
    
    return result


def check_database_health(config: Optional[DatabaseConfig] = None) -> dict:
    """Health check for all database backends - root level"""
    if config is None:
        config = get_config()
    
    health = {
        "backend": config.effective_backend.value,
        "data_dir": str(config.data_dir),
        "data_dir_exists": config.data_dir.exists(),
        "sqlite_exists": config.sqlite_path.exists(),
        "duckdb_exists": config.duckdb_path.exists(),
        "mobile_dir_exists": config.mobile_data_dir.exists() if hasattr(config, 'mobile_data_dir') else False,
        "supabase_configured": bool(config.supabase_url and config.supabase_key),
        "postgres_configured": bool(config.database_url),
    }
    
    # Try connections
    try:
        conn = get_sqlite_connection(config)
        cur = conn.execute("SELECT 1")
        health["sqlite_ok"] = True
        conn.close()
    except Exception as e:
        health["sqlite_ok"] = False
        health["sqlite_error"] = str(e)
    
    try:
        if config.effective_backend == DatabaseBackend.DUCKDB:
            import duckdb
            conn = duckdb.connect(str(config.duckdb_path))
            conn.execute("SELECT 1")
            health["duckdb_ok"] = True
            conn.close()
    except Exception as e:
        health["duckdb_ok"] = False
        health["duckdb_error"] = str(e)
    
    return health
