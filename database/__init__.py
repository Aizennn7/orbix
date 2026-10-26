"""
Orbix Database - Root Level Configuration
All database config lives at root level, shared by the web and mobile clients

Every public name in `database.config` and `database.connection` is re-exported
here. That is not cosmetic: callers write `from database import X`, and a name
missing from this list raises ImportError at the call site - which both
`orbix/api.py` and `orbix/mobile_api.py` wrap in `except Exception`. The result
is not a crash but a silently degraded response: /api/storage shipped for weeks
with a `database_config_error` field instead of a health block, and the mobile
API ran with `_root_db_config = None`. tests/test_database_exports.py fails the
build if this list drifts from what the code actually imports.
"""
from .config import (
    DatabaseBackend,
    DatabaseConfig,
    get_config,
    get_database_config,
    reload_config,
    set_config,
)
from .connection import (
    check_database_health,
    get_candle_store,
    get_connection,
    get_duckdb_connection,
    get_ledger,
    get_mobile_store,
    get_postgres_connection,
    get_sqlite_connection,
    get_supabase_client,
    init_database,
)

__all__ = [
    # config
    "DatabaseBackend",
    "DatabaseConfig",
    "get_config",
    "get_database_config",
    "reload_config",
    "set_config",
    # connection
    "check_database_health",
    "get_candle_store",
    "get_connection",
    "get_duckdb_connection",
    "get_ledger",
    "get_mobile_store",
    "get_postgres_connection",
    "get_sqlite_connection",
    "get_supabase_client",
    "init_database",
]

__version__ = "0.4.0"
