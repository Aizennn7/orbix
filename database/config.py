"""
Orbix Database Config - Root Level
Central configuration for all database backends
Supports: SQLite (default, mobile, laptop), DuckDB (analytics), Postgres/Supabase (cloud)

All config via .env at root level:
- DATABASE_BACKEND: sqlite, duckdb, postgres, supabase, auto
- DATABASE_URL: postgres://... or supabase URL
- ORBIX_DATA_DIR: base data directory (default ./data at root)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]

# Paths derived from `data_dir` in DatabaseConfig.__post_init__.
# set_data_dir() clears these so they are recomputed against the new directory.
_DERIVED_PATH_FIELDS = (
    "sqlite_path",
    "sqlite_candles_path",
    "duckdb_path",
    "mobile_data_dir",
    "mobile_candles_db",
    "mobile_ledger_db",
)


class DatabaseBackend(str, Enum):
    """Supported database backends"""
    SQLITE = "sqlite"           # Portable default - works everywhere, no server
    DUCKDB = "duckdb"           # Analytics, fast for candles, web default
    POSTGRES = "postgres"       # Production, Supabase compatible
    SUPABASE = "supabase"       # Supabase cloud (Postgres + Auth + Realtime)
    AUTO = "auto"               # Auto-detect based on env


@dataclass
class DatabaseConfig:
    """Root level database configuration - single source of truth"""
    
    # Backend selection
    backend: DatabaseBackend = DatabaseBackend.AUTO
    
    # Base data directory - at root level
    data_dir: Path = ROOT / "data"
    
    # SQLite - default for mobile, laptop, offline
    sqlite_path: Path = None  # Will be data_dir / "orbix.sqlite3"
    sqlite_candles_path: Path = None  # For mobile unified
    
    # DuckDB - analytics, web
    duckdb_path: Path = None  # Will be data_dir / "candles.duckdb"
    
    # Postgres / Supabase
    database_url: Optional[str] = None  # postgres://user:pass@host:port/db
    supabase_url: Optional[str] = None  # https://xxx.supabase.co
    supabase_key: Optional[str] = None  # anon or service key
    supabase_service_key: Optional[str] = None
    
    # Mobile client specific
    mobile_data_dir: Path = None  # Will be data_dir / "mobile" or app private
    mobile_candles_db: Path = None
    mobile_ledger_db: Path = None
    
    # Shared settings
    auto_migrate: bool = True
    wal_mode: bool = True  # SQLite WAL for concurrency
    pool_size: int = 5
    
    def __post_init__(self):
        # Resolve data_dir
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir).expanduser()
        self.data_dir = self.data_dir.resolve() if self.data_dir.is_absolute() else (ROOT / self.data_dir).resolve()
        
        # Default paths at root level.
        #
        # These are derived from data_dir ONCE, here. Assigning to
        # `config.data_dir` later does NOT recompute them, and the stores would
        # keep opening the old files. To point a config at a different
        # directory, build a new one via
        # `get_database_config({**os.environ, "ORBIX_DATA_DIR": str(path)})`
        # or call `set_data_dir(path)`.
        if self.sqlite_path is None:
            self.sqlite_path = self.data_dir / "orbix.sqlite3"
        if self.sqlite_candles_path is None:
            self.sqlite_candles_path = self.data_dir / "candles.sqlite3"
        if self.duckdb_path is None:
            self.duckdb_path = self.data_dir / "candles.duckdb"
        if self.mobile_data_dir is None:
            self.mobile_data_dir = self.data_dir / "mobile"
        if self.mobile_candles_db is None:
            self.mobile_candles_db = self.mobile_data_dir / "mobile_candles.db"
        if self.mobile_ledger_db is None:
            self.mobile_ledger_db = self.mobile_data_dir / "mobile_ledger.db"
        
        # Ensure data_dir exists.
        #
        # Deliberate side effect: constructing a config creates its directories.
        # This is impure - merely reading configuration touches the filesystem -
        # but every consumer (DuckDB, SQLite, the mobile stores, the CSV upload
        # spool) needs the directory to exist before it opens a handle, and they
        # are called from enough places that pushing mkdir to each caller is how
        # one ends up with a store that fails only on a fresh volume. Kept here
        # so the invariant holds by construction. set_data_dir() re-runs it for
        # the same reason.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.mobile_data_dir.mkdir(parents=True, exist_ok=True)

    def set_data_dir(self, data_dir) -> "DatabaseConfig":
        """Repoint this config at a different data directory.

        Every path derived from `data_dir` is recomputed. Plain assignment
        (`config.data_dir = path`) does NOT do this and silently leaves the
        stores pointing at the previous directory, so always use this method.
        """
        self.data_dir = data_dir
        for field in _DERIVED_PATH_FIELDS:
            setattr(self, field, None)
        self.__post_init__()
        return self

    @property
    def is_postgres(self) -> bool:
        return self.backend in (DatabaseBackend.POSTGRES, DatabaseBackend.SUPABASE) or bool(self.database_url and "postgres" in self.database_url)
    
    @property
    def is_supabase(self) -> bool:
        return self.backend == DatabaseBackend.SUPABASE or bool(self.supabase_url)
    
    @property
    def is_sqlite(self) -> bool:
        return self.backend == DatabaseBackend.SQLITE
    
    @property
    def is_duckdb(self) -> bool:
        return self.backend == DatabaseBackend.DUCKDB
    
    @property
    def effective_backend(self) -> DatabaseBackend:
        """Resolve AUTO to actual backend"""
        if self.backend != DatabaseBackend.AUTO:
            return self.backend
        
        # Auto-detect priority: Supabase > Postgres > DuckDB > SQLite
        if self.supabase_url and self.supabase_key:
            return DatabaseBackend.SUPABASE
        if self.database_url:
            return DatabaseBackend.POSTGRES
        if os.environ.get("ORBIX_USE_DUCKDB", "").lower() in ("1", "true", "yes"):
            return DatabaseBackend.DUCKDB
        if os.environ.get("ORBIX_MOBILE", "").lower() in ("1", "true", "yes"):
            return DatabaseBackend.SQLITE
        
        # Default: DuckDB for web, SQLite for mobile
        return DatabaseBackend.DUCKDB
    
    def to_dict(self) -> dict:
        return {
            "backend": self.effective_backend.value,
            "configured_backend": self.backend.value,
            "data_dir": str(self.data_dir),
            "sqlite_path": str(self.sqlite_path),
            "duckdb_path": str(self.duckdb_path),
            "mobile_data_dir": str(self.mobile_data_dir),
            "is_postgres": self.is_postgres,
            "is_supabase": self.is_supabase,
            "supabase_configured": bool(self.supabase_url and self.supabase_key),
            "database_url_configured": bool(self.database_url),
        }


def get_database_config(env: Optional[dict] = None) -> DatabaseConfig:
    """
    Get database config from root level .env
    Single source of truth for all Orbix database connections
    """
    if env is None:
        env = os.environ
    
    # Backend from env
    # Every other value read below is stripped, and this one has to be too. A
    # blank "DATABASE_BACKEND=" line is the normal way to comment a value out in
    # a .env file, and a stray trailing space is a typo with no intent behind it;
    # neither should be mistaken for a backend name and abort startup.
    backend_str = (
        env.get("DATABASE_BACKEND", env.get("ORBIX_DATABASE_BACKEND", "auto"))
        .strip()
        .lower()
    ) or "auto"
    try:
        backend = DatabaseBackend(backend_str)
    except ValueError:
        # Only a value that was actually set can be wrong; an unset or blank
        # variable already resolved to "auto" above and is not an error.
        #
        # This used to fall back to AUTO silently. That turns a typo into a
        # different database: DATABASE_BACKEND=postgress starts a local SQLite
        # file while the operator believes two servers are sharing Postgres
        # state. Same class as the swallowed ImportError in the Docker build and
        # the missing re-export in database/__init__.py - the process looks
        # healthy and is quietly wrong. Fail at configuration time instead,
        # where the message can name the valid choices.
        valid = ", ".join(b.value for b in DatabaseBackend)
        raise RuntimeError(
            f"DATABASE_BACKEND={backend_str!r} is not a recognised backend. "
            f"Valid values: {valid}. Remove the variable to auto-detect."
        ) from None
    
    # Data dir - root level
    data_dir_str = env.get("ORBIX_DATA_DIR", env.get("DATABASE_DATA_DIR", "")).strip()
    data_dir = Path(data_dir_str).expanduser() if data_dir_str else ROOT / "data"
    
    # Mobile data dir
    mobile_dir_str = env.get("ORBIX_MOBILE_DATA_DIR", env.get("MOBILE_DATA_DIR", "")).strip()
    mobile_data_dir = Path(mobile_dir_str).expanduser() if mobile_dir_str else None
    
    # Database URLs
    database_url = env.get("DATABASE_URL", env.get("POSTGRES_URL", "")).strip() or None
    supabase_url = env.get("SUPABASE_URL", "").strip() or None
    supabase_key = env.get("SUPABASE_ANON_KEY", env.get("SUPABASE_KEY", "")).strip() or None
    supabase_service = env.get("SUPABASE_SERVICE_KEY", "").strip() or None
    
    # If DATABASE_URL looks like supabase, treat as supabase
    if database_url and "supabase" in database_url and not supabase_url:
        supabase_url = database_url
        database_url = None
    
    config = DatabaseConfig(
        backend=backend,
        data_dir=data_dir,
        database_url=database_url,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        supabase_service_key=supabase_service,
        mobile_data_dir=mobile_data_dir,
        auto_migrate=env.get("DATABASE_AUTO_MIGRATE", "true").lower() not in ("0", "false", "no"),
        wal_mode=env.get("DATABASE_WAL_MODE", "true").lower() not in ("0", "false", "no"),
    )
    
    return config


# Global singleton - root level config
_config: Optional[DatabaseConfig] = None

def get_config() -> DatabaseConfig:
    global _config
    if _config is None:
        _config = get_database_config()
    return _config

def set_config(config: DatabaseConfig) -> None:
    global _config
    _config = config

def reload_config() -> DatabaseConfig:
    global _config
    _config = get_database_config()
    return _config
