#!/usr/bin/env python3
"""
Setup Supabase at Root Level - Interactive
Configures database at root level for live and online (not offline)
All config in the root .env, shared by the web and mobile clients

Usage:
  python scripts/setup_supabase.py              # interactive
  python scripts/setup_supabase.py --check      # check current config
  python scripts/setup_supabase.py --url https://xxx.supabase.co --anon-key eyJ... --service-key eyJ...
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.config import get_database_config


def check_current():
    print("==> Current Root Database Config")
    cfg = get_database_config()
    print(f"  Backend: {cfg.backend.value} -> effective: {cfg.effective_backend.value}")
    print(f"  Data dir: {cfg.data_dir} (at root)")
    print(f"  SQLite: {cfg.sqlite_path} (exists: {cfg.sqlite_path.exists()})")
    print(f"  DuckDB: {cfg.duckdb_path} (exists: {cfg.duckdb_path.exists()})")
    print(f"  Mobile: {cfg.mobile_data_dir} (exists: {cfg.mobile_data_dir.exists()})")
    print(f"  Supabase URL: {cfg.supabase_url or 'NOT SET'}")
    print(f"  Supabase Anon Key: {'SET' if cfg.supabase_key else 'NOT SET'}")
    print(f"  Supabase Service Key: {'SET' if cfg.supabase_service_key else 'NOT SET'}")
    print(f"  Postgres URL: {'SET' if cfg.database_url else 'NOT SET'}")
    print()
    
    from database.connection import check_database_health
    health = check_database_health(cfg)
    print("Health:")
    for k, v in health.items():
        print(f"  {k}: {v}")
    
    print()
    if cfg.is_supabase:
        print("✓ LIVE and ONLINE - Supabase configured at root")
        print("  The mobile client syncs to Supabase when online, with a local DB as fallback")
    elif cfg.is_postgres:
        print("✓ LIVE and ONLINE - Postgres configured at root")
    else:
        print("○ OFFLINE - SQLite/DuckDB at root (local only)")
        print("  To make LIVE and ONLINE, set SUPABASE_URL at root .env")
    
    return cfg


def setup_interactive():
    print("==> Setup Supabase at Root Level - LIVE and ONLINE")
    print("    All config at root level: database/ + data/ + .env")
    print()
    print("Steps:")
    print("1. Go to supabase.com -> New Project -> Wait 2 mins")
    print("2. Go to Project Settings -> API -> Copy URL + anon key + service_role key")
    print("3. Go to SQL Editor -> New Query -> Copy database/schema.sql -> Run")
    print()
    
    url = input("Supabase URL (https://xxx.supabase.co) [skip]: ").strip()
    if not url:
        print("Skipped Supabase setup, staying offline")
        return
    
    anon_key = input("Supabase Anon Key (eyJ...) [skip]: ").strip()
    service_key = input("Supabase Service Role Key (eyJ...) [skip, optional]: ").strip()
    
    if not anon_key:
        print("Anon key required for Supabase")
        return
    
    # Write to root .env
    env_path = ROOT / ".env"
    env_example = ROOT / ".env.example"
    
    # Read existing .env or .env.example
    if env_path.exists():
        content = env_path.read_text()
    elif env_example.exists():
        content = env_example.read_text()
    else:
        content = ""
    
    # Update or add Supabase config at root
    lines = content.splitlines()
    new_lines = []
    found_backend = False
    found_url = False
    found_anon = False
    found_service = False
    
    for line in lines:
        if line.startswith("DATABASE_BACKEND="):
            new_lines.append("DATABASE_BACKEND=supabase")
            found_backend = True
        elif line.startswith("SUPABASE_URL="):
            new_lines.append(f"SUPABASE_URL={url}")
            found_url = True
        elif line.startswith("SUPABASE_ANON_KEY="):
            new_lines.append(f"SUPABASE_ANON_KEY={anon_key}")
            found_anon = True
        elif line.startswith("SUPABASE_SERVICE_KEY="):
            if service_key:
                new_lines.append(f"SUPABASE_SERVICE_KEY={service_key}")
                found_service = True
            else:
                new_lines.append(line)
                found_service = True
        else:
            new_lines.append(line)
    
    if not found_backend:
        new_lines.append("DATABASE_BACKEND=supabase")
    if not found_url:
        new_lines.append(f"SUPABASE_URL={url}")
    if not found_anon:
        new_lines.append(f"SUPABASE_ANON_KEY={anon_key}")
    if not found_service and service_key:
        new_lines.append(f"SUPABASE_SERVICE_KEY={service_key}")
    
    env_path.write_text("\n".join(new_lines) + "\n")
    print(f"\n✓ Written to {env_path} at root level")
    print("  DATABASE_BACKEND=supabase")
    print(f"  SUPABASE_URL={url}")
    print(f"  SUPABASE_ANON_KEY={'*'*20}...{anon_key[-10:] if len(anon_key) > 10 else anon_key}")
    
    # Reload config and check
    print("\n==> Checking new config at root")
    # Clear env cache
    os.environ["DATABASE_BACKEND"] = "supabase"
    os.environ["SUPABASE_URL"] = url
    os.environ["SUPABASE_ANON_KEY"] = anon_key
    if service_key:
        os.environ["SUPABASE_SERVICE_KEY"] = service_key
    
    # Need to reload module
    import importlib
    import database.config
    importlib.reload(database.config)
    from database.config import get_database_config
    cfg = get_database_config()
    print(f"  Backend: {cfg.effective_backend.value}")
    print(f"  Supabase configured: {cfg.is_supabase}")
    
    if cfg.is_supabase:
        print("\n✓ LIVE and ONLINE configured at root level!")
        print("  Next:")
        print("  1. python scripts/init_db.py --check")
        print("  2. uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1")
        print("  3. Open forwarded port 8000 -> should show live online, not offline DB")
        print("  4. For the mobile PWA: ./scripts/build_mobile.sh")
    else:
        print("\n○ Still offline, check .env at root")


def setup_from_args(url: str, anon_key: str, service_key: str = ""):
    print(f"==> Setup Supabase from args at root level")
    print(f"  URL: {url}")
    
    env_path = ROOT / ".env"
    env_example = ROOT / ".env.example"
    
    if env_path.exists():
        content = env_path.read_text()
    elif env_example.exists():
        content = env_example.read_text()
    else:
        content = ""
    
    # Simple append or replace
    if "SUPABASE_URL=" in content:
        # Replace existing
        import re
        content = re.sub(r"SUPABASE_URL=.*", f"SUPABASE_URL={url}", content)
        content = re.sub(r"SUPABASE_ANON_KEY=.*", f"SUPABASE_ANON_KEY={anon_key}", content)
        content = re.sub(r"DATABASE_BACKEND=.*", "DATABASE_BACKEND=supabase", content)
        if service_key:
            if "SUPABASE_SERVICE_KEY=" in content:
                content = re.sub(r"SUPABASE_SERVICE_KEY=.*", f"SUPABASE_SERVICE_KEY={service_key}", content)
            else:
                content += f"\nSUPABASE_SERVICE_KEY={service_key}\n"
    else:
        content += f"\nDATABASE_BACKEND=supabase\nSUPABASE_URL={url}\nSUPABASE_ANON_KEY={anon_key}\n"
        if service_key:
            content += f"SUPABASE_SERVICE_KEY={service_key}\n"
    
    env_path.write_text(content)
    print(f"✓ Written to {env_path} at root")
    print("  Run: python scripts/init_db.py --check")


def main():
    parser = argparse.ArgumentParser(description="Setup Supabase at root level - live and online")
    parser.add_argument("--check", action="store_true", help="Check current root DB config")
    parser.add_argument("--url", type=str, help="Supabase URL")
    parser.add_argument("--anon-key", type=str, help="Supabase Anon Key")
    parser.add_argument("--service-key", type=str, default="", help="Supabase Service Role Key")
    args = parser.parse_args()
    
    if args.check:
        check_current()
        return 0
    
    if args.url and args.anon_key:
        setup_from_args(args.url, args.anon_key, args.service_key)
        return 0
    
    # Interactive
    check_current()
    print()
    setup_interactive()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
