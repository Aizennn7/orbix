#!/usr/bin/env python3
"""
Init Orbix Database at Root Level
Creates all tables for SQLite, DuckDB, checks Postgres/Supabase
All config from root .env

Usage:
  python scripts/init_db.py              # init with auto backend
  python scripts/init_db.py --check      # health check
  python scripts/init_db.py --backend sqlite  # force backend
  python scripts/init_db.py --reset      # WARNING: deletes data/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.config import get_database_config, DatabaseBackend
from database.connection import init_database, check_database_health


def main():
    parser = argparse.ArgumentParser(description="Init Orbix DB at root level")
    parser.add_argument("--check", action="store_true", help="Health check only")
    parser.add_argument("--backend", choices=["auto", "sqlite", "duckdb", "postgres", "supabase"], default="auto", help="Force backend")
    parser.add_argument("--reset", action="store_true", help="Delete data/ and re-init (DANGEROUS)")
    parser.add_argument("--data-dir", type=str, default="", help="Override data dir")
    args = parser.parse_args()

    # Load config from root .env
    import os
    if args.backend != "auto":
        os.environ["DATABASE_BACKEND"] = args.backend
    if args.data_dir:
        os.environ["ORBIX_DATA_DIR"] = args.data_dir

    config = get_database_config()
    
    print(f"==> Orbix Database - Root Level")
    print(f"    Backend: {config.backend.value} -> effective: {config.effective_backend.value}")
    print(f"    Data dir: {config.data_dir}")
    print(f"    SQLite: {config.sqlite_path}")
    print(f"    DuckDB: {config.duckdb_path}")
    print(f"    Mobile: {config.mobile_data_dir}")
    print(f"    Supabase: {'yes' if config.supabase_url else 'no'}")
    print(f"    Postgres: {'yes' if config.database_url else 'no'}")
    print()

    if args.reset:
        import shutil
        print(f"!!! RESET: Deleting {config.data_dir}")
        confirm = input("Type YES to confirm: ")
        if confirm == "YES":
            if config.data_dir.exists():
                shutil.rmtree(config.data_dir)
                print(f"Deleted {config.data_dir}")
            config.data_dir.mkdir(parents=True, exist_ok=True)
            print(f"Recreated {config.data_dir}")
        else:
            print("Aborted")
            return 1

    if args.check:
        print("==> Health check")
        health = check_database_health(config)
        for k, v in health.items():
            print(f"  {k}: {v}")
        
        # Overall
        if health.get("sqlite_ok"):
            print("\n✓ SQLite OK - ready for mobile, laptop, offline")
        if health.get("duckdb_ok"):
            print("✓ DuckDB OK - ready for web analytics")
        if health.get("supabase_configured"):
            print("✓ Supabase configured - cloud sync ready")
        if health.get("postgres_configured"):
            print("✓ Postgres configured")
        
        return 0

    print("==> Initializing database at root level")
    result = init_database(config)
    
    print(f"  Backend: {result['backend']}")
    print(f"  Data dir: {result['data_dir']}")
    print(f"  Status: {result['status']}")
    for t in result.get("tables", []):
        print(f"  - {t}")
    
    print()
    print("==> Done! Database configured at root level")
    print(f"  Data lives in: {config.data_dir}")
    print(f"  Config from: {ROOT / '.env'} (or .env.example)")
    print()
    print("Next:")
    print("  python -m orbix.api  # uses root DB config")
    print("  ./scripts/build_mobile.sh  # mobile uses same root DB")
    print("  docker compose up -d  # Docker uses /data volume at root config")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
