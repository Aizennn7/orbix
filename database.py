"""
Orbix Database - Root Level Shortcut
This file at root level allows: import database
Or: from database import get_database_config

Canonical location is database/ folder at root, this is shortcut
"""
from database import *  # noqa: F401,F403
from database.config import get_database_config, DatabaseConfig, DatabaseBackend
from database.connection import (
    get_connection,
    get_candle_store,
    get_ledger,
    get_mobile_store,
    init_database,
    check_database_health,
)

__all__ = [
    "DatabaseConfig",
    "DatabaseBackend",
    "get_database_config",
    "get_connection",
    "get_candle_store",
    "get_ledger",
    "get_mobile_store",
    "init_database",
    "check_database_health",
]
