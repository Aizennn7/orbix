"""
Orbix Database Connection - Root Level Factory
Single entry point for all database connections across the web and mobile clients
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Union

from .config import DatabaseConfig, DatabaseBackend, get_config, get_database_config

logger = logging.getLogger(__name__)

# Lazy imports - only import when needed
_candle_store_cache = {}
_ledger_cache = {}
_lock = threading.RLock()


def _backend_is_explicit(config: DatabaseConfig) -> bool:
    """True when the operator named a backend instead of leaving it on ``auto``.

    The distinction decides whether a missing driver may fall back. Under
    ``auto`` the fallback chain *is* the feature: pick the best backend that
    this machine can actually open. But when someone wrote
    ``DATABASE_BACKEND=postgres``, silently handing them a local SQLite file
    means two servers believe they share state when they do not. That is the
    same failure mode as a swallowed ImportError in the Docker build, so it
    raises instead.
    """
    return config.backend != DatabaseBackend.AUTO


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
        # DuckDB is a declared hard dependency (pyproject.toml and
        # requirements.txt), so its absence means a broken install rather than a
        # platform that legitimately lacks it. Same rule as the Postgres and
        # Supabase paths: an explicit request fails loudly, auto-detection warns
        # and falls back.
        if _backend_is_explicit(config):
            raise RuntimeError(
                "DATABASE_BACKEND=duckdb was requested but duckdb is not "
                "installed. Refusing to fall back to SQLite, because the candle "
                "store would silently change engine. Reinstall dependencies "
                "with: pip install -r requirements.txt"
            ) from None
        logger.warning(
            "[DB] DuckDB not available (auto backend); falling back to SQLite. "
            "Reinstall dependencies with: pip install -r requirements.txt"
        )
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
    
    explicit = _backend_is_explicit(config)
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
        if explicit:
            raise RuntimeError(
                "DATABASE_BACKEND=postgres was requested but psycopg2 is not "
                "installed. Refusing to fall back to a local file, because two "
                "servers would then believe they share state when they do not. "
                "Install it with: pip install '.[postgres]'"
            ) from None
        logger.warning(
            "[DB] psycopg2 not installed (auto backend); trying supabase. "
            "Install with: pip install '.[postgres]'"
        )
        return get_supabase_client(config)
    except Exception as e:
        if explicit:
            raise RuntimeError(
                f"DATABASE_BACKEND=postgres was requested but the connection "
                f"failed: {e}. Refusing to fall back to a local file. Check "
                "DATABASE_URL and that the server is reachable."
            ) from e
        logger.warning("[DB] Postgres connection failed: %s; falling back to SQLite", e)
        return get_sqlite_connection(config)


def get_supabase_client(config: Optional[DatabaseConfig] = None):
    """Supabase client"""
    if config is None:
        config = get_config()
    
    explicit = _backend_is_explicit(config)
    try:
        from supabase import create_client
        if config.supabase_url and config.supabase_key:
            client = create_client(config.supabase_url, config.supabase_key)
            return client
        if explicit:
            raise RuntimeError(
                "DATABASE_BACKEND=supabase was requested but SUPABASE_URL / "
                "SUPABASE_ANON_KEY are not set. Refusing to fall back to a "
                "local file, because cloud sync would silently not happen."
            )
    except ImportError:
        if explicit:
            raise RuntimeError(
                "DATABASE_BACKEND=supabase was requested but supabase-py is not "
                "installed. Refusing to fall back to a local file. Install it "
                "with: pip install '.[supabase]'"
            ) from None
        logger.warning(
            "[DB] supabase-py not installed (auto backend); using a local file. "
            "Install with: pip install '.[supabase]'"
        )
    except RuntimeError:
        raise
    except Exception as e:
        if explicit:
            raise RuntimeError(
                f"DATABASE_BACKEND=supabase was requested but the client failed "
                f"to initialise: {e}. Refusing to fall back to a local file."
            ) from e
        logger.warning("[DB] Supabase client failed: %s; using a local file", e)
    
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
    # Key on the files that will actually be opened, not on data_dir. Keying on
    # data_dir let the key and the target diverge: two configs with different
    # data_dir values but stale derived paths produced two cache entries bound
    # to the same physical database.
    cache_key = "{}:{}:{}:{}".format(
        effective.value,
        config.duckdb_path,
        config.sqlite_candles_path,
        config.mobile_candles_db,
    )
    
    with _lock:
        cached = _candle_store_cache.get(cache_key)
        if cached is not None:
            stale = getattr(cached, "closed", False)
            if not stale:
                # Nor one whose database file has disappeared.
                #
                # CandleStore opens a single DuckDB connection in __init__ and
                # reuses it at every one of its query sites, so it cannot
                # re-create its directory on demand the way the SQLite stores
                # do. If the data directory is removed at runtime - a volume
                # remount, a cleanup job - the open handle keeps writing to an
                # unlinked inode. Nothing raises, and every candle written after
                # that point is silently discarded when the process exits. That
                # is worse than the SQLite failure mode, because it looks like
                # success. Rebuilding is the only route back to durable writes.
                db_file = getattr(cached, "database", None)
                if db_file is not None and not Path(db_file).exists():
                    logger.warning(
                        "[DB] candle store file %s disappeared; closing the stale "
                        "handle and rebuilding. Candles written to it since the "
                        "directory vanished are lost.",
                        db_file,
                    )
                    stale = True
            if stale:
                # Never hand back a closed or orphaned store: the caller would
                # get a dead DuckDB connection, or one writing to nothing. Drop
                # the entry and fall through to rebuild it.
                closer = getattr(cached, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        logger.warning(
                            "[DB] failed to close a stale candle store", exc_info=True
                        )
                _candle_store_cache.pop(cache_key, None)
            else:
                return cached
        
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
    
    # Key on the resolved files, for the same reason as get_candle_store().
    cache_key = "ledger:{}:{}".format(config.sqlite_path, config.mobile_ledger_db)
    
    with _lock:
        cached = _ledger_cache.get(cache_key)
        if cached is not None:
            # Same guard as get_candle_store(): a closed ledger is rebuilt
            # rather than returned. Ledgers have no close() today, so this is
            # defensive - getattr keeps it correct if one is added later.
            if getattr(cached, "closed", False):
                _ledger_cache.pop(cache_key, None)
            else:
                return cached
        
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
