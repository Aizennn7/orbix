#!/usr/bin/env python3
"""
Configure ALL Data Inside APK Permanently at Root Level

This script makes the APK's data configuration permanent at root level.
When you build APK, it embeds root-level config so APK is live online forever.

Root Level Structure (single source of truth):
  database/                    <- ROOT LEVEL DB config (single source)
    config.py                  <- Backend detection (auto > supabase > sqlite)
    schema.sql                 <- Supabase tables (run once in Supabase)
    apk_config.json            <- PERMANENT APK config (generated from root .env)
    root_manifest.json         <- Manifest of all data inside APK
  
  data/                        <- ROOT LEVEL DATA (all Orbix data)
    orbix.sqlite3              <- Main DB (offline fallback, bundled in APK)
    mobile/                    <- Mobile datasets
      datasets.json
      candles.json
      signals.json
    root_config.json           <- Root config cache for APK
    apk_build_info.json        <- Build info

  mobile/                      <- APK source, reads from ROOT
    src/
      js/
        root_config.js         <- GENERATED from root .env (permanent in APK)
      root_config.json         <- GENERATED from root .env (bundled in dist)
    public/
      root_config.json         <- For PWA
    dist/                      <- Built PWA, contains root config permanently

Usage:
  python scripts/configure_root_apk.py --check          # Check current root config
  python scripts/configure_root_apk.py --init           # Init data/ at root + default DB
  python scripts/configure_root_apk.py --supabase       # Interactive Supabase setup + make permanent
  python scripts/configure_root_apk.py --url https://xxx.supabase.co --anon-key eyJ...  # Set permanent
  python scripts/configure_root_apk.py --build          # Configure for APK build (generates all files)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env at root if exists
def load_root_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return {}
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def save_root_env(updates: dict):
    env_path = ROOT / ".env"
    env_example = ROOT / ".env.example"
    
    if env_path.exists():
        content = env_path.read_text()
    elif env_example.exists():
        content = env_example.read_text()
    else:
        content = ""
    
    # Update
    lines = content.splitlines()
    new_lines = []
    found_keys = set()
    
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=")[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                found_keys.add(k)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    for k, v in updates.items():
        if k not in found_keys:
            new_lines.append(f"{k}={v}")
    
    env_path.write_text("\n".join(new_lines) + "\n")
    print(f"✓ Updated {env_path} at root level (permanent)")

def get_root_config():
    from database.config import get_database_config
    return get_database_config()

def check_status():
    print("="*70)
    print("ORBIX ROOT LEVEL CONFIG - Permanent APK Data")
    print("="*70)
    print(f"Root: {ROOT}")
    print()
    
    # Check database/ at root
    print("[1] database/ at root level (single source of truth)")
    db_dir = ROOT / "database"
    if db_dir.exists():
        print(f"  ✓ {db_dir} exists")
        for f in ["config.py", "connection.py", "schema.sql", "sqlite_schema.sql", "apk_config.json", "root_manifest.json"]:
            fp = db_dir / f
            print(f"    {'✓' if fp.exists() else '○'} {f} {'(permanent APK config)' if 'apk_config' in f else ''}")
    else:
        print(f"  ✗ {db_dir} missing")
    
    print()
    print("[2] data/ at root level (all Orbix data)")
    data_dir = ROOT / "data"
    if data_dir.exists():
        print(f"  ✓ {data_dir} exists")
        for sub in ["orbix.sqlite3", "candles.duckdb", "mobile", "root_config.json", "apk_build_info.json"]:
            fp = data_dir / sub
            exists = fp.exists()
            size = f"{fp.stat().st_size/1024:.1f}KB" if exists and fp.is_file() else ""
            print(f"    {'✓' if exists else '○'} {sub} {size}")
        if (data_dir / "mobile").exists():
            for f in (data_dir / "mobile").glob("*"):
                print(f"      - mobile/{f.name} {f.stat().st_size} bytes")
    else:
        print(f"  ○ {data_dir} not exists - run --init")
    
    print()
    print("[3] .env at root level (permanent)")
    env_path = ROOT / ".env"
    env = load_root_env()
    if env_path.exists():
        print(f"  ✓ {env_path} exists")
        print(f"    DATABASE_BACKEND={env.get('DATABASE_BACKEND', 'not set')}")
        print(f"    SUPABASE_URL={'SET '+env.get('SUPABASE_URL','')[:30]+'...' if env.get('SUPABASE_URL') else 'NOT SET (offline)'}")
        print(f"    SUPABASE_ANON_KEY={'SET' if env.get('SUPABASE_ANON_KEY') else 'NOT SET'}")
        print(f"    ORBIX_DATA_DIR={env.get('ORBIX_DATA_DIR', './data at root')}")
    else:
        print(f"  ○ {env_path} not exists - using auto detection")
    
    print()
    print("[4] Mobile APK permanent config")
    mobile_config_js = ROOT / "mobile" / "src" / "js" / "root_config.js"
    mobile_config_json = ROOT / "mobile" / "src" / "root_config.json"
    public_config = ROOT / "mobile" / "public" / "root_config.json"
    apk_config = ROOT / "database" / "apk_config.json"
    
    for p, desc in [
        (mobile_config_js, "mobile/src/js/root_config.js (hardcoded in APK JS, permanent)"),
        (mobile_config_json, "mobile/src/root_config.json (bundled in dist)"),
        (public_config, "mobile/public/root_config.json (PWA)"),
        (apk_config, "database/apk_config.json (root level permanent)"),
    ]:
        print(f"  {'✓' if p.exists() else '○'} {p.relative_to(ROOT)} - {desc}")
        if p.exists() and p.suffix == ".json":
            try:
                data = json.loads(p.read_text())
                print(f"      backend: {data.get('backend', data.get('DATABASE_BACKEND', 'unknown'))}, live: {data.get('is_live', data.get('live', False))}")
            except:
                pass
    
    print()
    print("[5] Database health at root")
    try:
        cfg = get_root_config()
        print(f"  Backend: {cfg.backend.value} -> effective: {cfg.effective_backend.value}")
        print(f"  Data dir: {cfg.data_dir} (at root)")
        print(f"  Is Supabase (live): {cfg.is_supabase}")
        print(f"  Is SQLite (offline): {cfg.effective_backend.value == 'sqlite'}")
        from database.connection import check_database_health
        health = check_database_health(cfg)
        for k, v in health.items():
            if k in ["backend", "supabase_configured", "sqlite_ok", "data_dir_exists"]:
                print(f"    {k}: {v}")
        
        if cfg.is_supabase:
            print()
            print("  ✓ LIVE and ONLINE - Supabase configured permanently at root")
            print("    APK will be live online 24/7 when built")
        else:
            print()
            print("  ○ OFFLINE - SQLite at root (local only)")
            print("    Run --supabase to make LIVE and ONLINE permanently at root")
    except Exception as e:
        print(f"  Error: {e}")
    
    print()
    print("="*70)

def init_data_at_root():
    print("==> Initializing data/ at root level (permanent)")
    data_dir = ROOT / "data"
    mobile_dir = data_dir / "mobile"
    
    data_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"  ✓ Created {data_dir} at root")
    print(f"  ✓ Created {mobile_dir} at root")
    
    # Create initial DB if not exists
    try:
        from scripts.init_db import main as init_db_main
        # Call init_db.py logic
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "init_db.py")], capture_output=True, text=True, cwd=str(ROOT))
        print(result.stdout[-500:] if result.stdout else "")
        if result.stderr:
            print(result.stderr[-500:])
    except Exception as e:
        print(f"  Init DB: {e}")
        # Create empty sqlite file
        sqlite_path = data_dir / "orbix.sqlite3"
        if not sqlite_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(sqlite_path))
            conn.execute("CREATE TABLE IF NOT EXISTS datasets (dataset_id TEXT PRIMARY KEY, pair TEXT, candle_count INTEGER, updated_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS candles (id INTEGER PRIMARY KEY, dataset_id TEXT, timestamp TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
            conn.execute("CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, pair TEXT, strategy TEXT, side TEXT, entry_price REAL, generated_at TEXT, status TEXT)")
            conn.commit()
            conn.close()
            print(f"  ✓ Created empty {sqlite_path}")
    
    # Create root_config.json at data/
    root_config_path = data_dir / "root_config.json"
    env = load_root_env()
    cfg_data = {
        "created_at": datetime.utcnow().isoformat(),
        "location": "data/ at root level - single source of truth",
        "database_backend": env.get("DATABASE_BACKEND", "auto"),
        "supabase_url": env.get("SUPABASE_URL", ""),
        "supabase_configured": bool(env.get("SUPABASE_URL")),
        "is_live": bool(env.get("SUPABASE_URL")),
        "data_dir": str(data_dir),
        "note": "All data inside APK configured permanently at root level"
    }
    root_config_path.write_text(json.dumps(cfg_data, indent=2))
    print(f"  ✓ Created {root_config_path} at root")
    
    # Create apk_build_info.json
    build_info_path = data_dir / "apk_build_info.json"
    build_info = {
        "build_at": datetime.utcnow().isoformat(),
        "root": str(ROOT),
        "database_dir": str(ROOT / "database"),
        "data_dir": str(data_dir),
        "mobile_dir": str(ROOT / "mobile"),
        "backend": env.get("DATABASE_BACKEND", "auto"),
        "is_live": bool(env.get("SUPABASE_URL")),
        "note": "This file is bundled into APK permanently from root level"
    }
    build_info_path.write_text(json.dumps(build_info, indent=2))
    print(f"  ✓ Created {build_info_path} at root")
    
    print()
    print("✓ data/ initialized at root level permanently")

def configure_supabase_permanent(url: str, anon_key: str, service_key: str = ""):
    print("==> Configuring Supabase PERMANENTLY at root level")
    print(f"  URL: {url}")
    print(f"  Anon Key: {'*'*20}...{anon_key[-10:] if len(anon_key)>10 else anon_key}")
    
    # 1. Save to root .env permanently
    updates = {
        "DATABASE_BACKEND": "supabase",
        "SUPABASE_URL": url,
        "SUPABASE_ANON_KEY": anon_key,
    }
    if service_key:
        updates["SUPABASE_SERVICE_KEY"] = service_key
    
    save_root_env(updates)
    
    # 2. Generate permanent APK configs at root level
    generate_apk_configs(url, anon_key, service_key)
    
    print()
    print("✓ Supabase configured PERMANENTLY at root level")
    print("  - .env at root (permanent)")
    print("  - database/apk_config.json at root (permanent)")
    print("  - mobile/src/js/root_config.js (hardcoded in APK, permanent)")
    print("  - mobile/src/root_config.json + mobile/public/root_config.json (bundled in dist)")
    print("  - data/root_config.json at root (permanent)")
    print()
    print("Next: Build APK -> it will be LIVE ONLINE 24/7 permanently")
    print("  ./scripts/build_mobile.sh")
    print("  git push -> GitHub Actions -> Download APK (live)")

def generate_apk_configs(supabase_url: str = "", anon_key: str = "", service_key: str = ""):
    print("==> Generating APK permanent config from root level")
    
    # Load from env if not provided
    env = load_root_env()
    if not supabase_url:
        supabase_url = env.get("SUPABASE_URL", "")
    if not anon_key:
        anon_key = env.get("SUPABASE_ANON_KEY", "")
    if not service_key:
        service_key = env.get("SUPABASE_SERVICE_KEY", "")
    
    backend = env.get("DATABASE_BACKEND", "supabase" if supabase_url else "sqlite")
    is_live = bool(supabase_url and anon_key)
    
    # Common config data - permanent at root
    config_data = {
        "generated_at": datetime.utcnow().isoformat(),
        "location": "ROOT LEVEL - database/ + data/ + .env - single source of truth",
        "backend": backend,
        "DATABASE_BACKEND": backend,
        "is_live": is_live,
        "live": is_live,
        "supabase_url": supabase_url,
        "SUPABASE_URL": supabase_url,
        "supabase_anon_key": anon_key,
        "SUPABASE_ANON_KEY": anon_key,
        "supabase_service_key": service_key,
        "SUPABASE_SERVICE_KEY": service_key,
        "supabase_configured": is_live,
        "data_dir": "./data at root",
        "database_dir": "./database at root",
        "note": "All data inside APK configured permanently at root level - bundled into APK",
        "root_level": True
    }
    
    # 1. database/apk_config.json at root level - PERMANENT
    apk_config_path = ROOT / "database" / "apk_config.json"
    apk_config_path.write_text(json.dumps(config_data, indent=2))
    print(f"  ✓ {apk_config_path.relative_to(ROOT)} at root (permanent)")
    
    # 2. database/root_manifest.json at root - manifest of all data inside APK
    manifest_path = ROOT / "database" / "root_manifest.json"
    manifest = {
        "generated_at": datetime.utcnow().isoformat(),
        "root_level": True,
        "location": "database/ at root level - single source of truth",
        "apk_data": {
            "config_files": [
                "database/apk_config.json (permanent root config)",
                "database/schema.sql (Supabase tables)",
                "data/root_config.json (root cache)",
                "mobile/src/js/root_config.js (hardcoded in JS)",
                "mobile/src/root_config.json (bundled in dist)",
                "mobile/public/root_config.json (PWA)"
            ],
            "data_files": [
                "data/orbix.sqlite3 (offline fallback, bundled)",
                "data/mobile/datasets.json",
                "data/mobile/candles.json",
                "data/mobile/signals.json",
                "data/apk_build_info.json"
            ],
            "js_engine": [
                "mobile/src/js/engine.js (5 strategies, all Orbix logic)",
                "mobile/src/js/db.js (SQLite inside APK)",
                "mobile/src/js/live_db.js (Supabase Realtime sync)",
                "mobile/src/js/app.js (main app)"
            ]
        },
        "backend": backend,
        "is_live": is_live,
        "note": "All data inside APK configured permanently at root level"
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  ✓ {manifest_path.relative_to(ROOT)} at root (manifest)")
    
    # 3. mobile/src/js/root_config.js - HARDCODED in APK JS, permanent
    js_config_path = ROOT / "mobile" / "src" / "js" / "root_config.js"
    js_config_path.parent.mkdir(parents=True, exist_ok=True)
    js_content = f"""/**
 * Orbix Root Config - PERMANENT at Root Level
 * Generated from root .env - bundled into APK permanently
 * All data inside APK configured at root level: database/ + data/ + .env
 * Generated at: {datetime.utcnow().isoformat()}
 * Location: database/apk_config.json at root (single source of truth)
 */

// Permanent root level config - hardcoded in APK, not localStorage
export const ROOT_CONFIG = {{
  generated_at: "{datetime.utcnow().isoformat()}",
  location: "ROOT LEVEL - database/ + data/ + .env",
  backend: "{backend}",
  DATABASE_BACKEND: "{backend}",
  is_live: {str(is_live).lower()},
  live: {str(is_live).lower()},
  supabase_url: "{supabase_url}",
  SUPABASE_URL: "{supabase_url}",
  supabase_anon_key: "{anon_key}",
  SUPABASE_ANON_KEY: "{anon_key}",
  supabase_service_key: "{service_key}",
  SUPABASE_SERVICE_KEY: "{service_key}",
  supabase_configured: {str(is_live).lower()},
  data_dir: "./data at root",
  database_dir: "./database at root",
  root_level: true,
  note: "All data inside APK configured permanently at root level"
}};

// For backward compatibility
export const SUPABASE_URL = ROOT_CONFIG.supabase_url;
export const SUPABASE_ANON_KEY = ROOT_CONFIG.supabase_anon_key;
export const DATABASE_BACKEND = ROOT_CONFIG.backend;
export const IS_LIVE = ROOT_CONFIG.is_live;

// Make available globally for APK
if (typeof window !== 'undefined') {{
  window.ORBIX_ROOT_CONFIG = ROOT_CONFIG;
  window.ORBIX_IS_LIVE = ROOT_CONFIG.is_live;
  console.log('[Orbix Root] Permanent config from root level:', ROOT_CONFIG.backend, ROOT_CONFIG.is_live ? 'LIVE ONLINE' : 'OFFLINE');
}}

export default ROOT_CONFIG;
"""
    js_config_path.write_text(js_content)
    print(f"  ✓ {js_config_path.relative_to(ROOT)} (hardcoded in APK, permanent)")
    
    # 4. mobile/src/root_config.json - bundled in dist
    json_config_path = ROOT / "mobile" / "src" / "root_config.json"
    json_config_path.write_text(json.dumps(config_data, indent=2))
    print(f"  ✓ {json_config_path.relative_to(ROOT)} (bundled in dist)")
    
    # 5. mobile/public/root_config.json - for PWA
    public_config_path = ROOT / "mobile" / "public" / "root_config.json"
    public_config_path.parent.mkdir(parents=True, exist_ok=True)
    public_config_path.write_text(json.dumps(config_data, indent=2))
    print(f"  ✓ {public_config_path.relative_to(ROOT)} (PWA)")
    
    # 6. data/root_config.json at root level - permanent cache
    data_root_config = ROOT / "data" / "root_config.json"
    data_root_config.parent.mkdir(parents=True, exist_ok=True)
    data_root_config.write_text(json.dumps(config_data, indent=2))
    print(f"  ✓ {data_root_config.relative_to(ROOT)} at root (permanent cache)")
    
    # 7. data/apk_build_info.json
    build_info_path = ROOT / "data" / "apk_build_info.json"
    build_info = {
        "build_at": datetime.utcnow().isoformat(),
        "generated_from": "scripts/configure_root_apk.py at root",
        "root": str(ROOT),
        "backend": backend,
        "is_live": is_live,
        "supabase_url": supabase_url,
        "supabase_configured": is_live,
        "files_generated": [
            "database/apk_config.json",
            "database/root_manifest.json",
            "mobile/src/js/root_config.js",
            "mobile/src/root_config.json",
            "mobile/public/root_config.json",
            "data/root_config.json"
        ],
        "note": "All data inside APK configured permanently at root level - this file bundled into APK"
    }
    build_info_path.write_text(json.dumps(build_info, indent=2))
    print(f"  ✓ {build_info_path.relative_to(ROOT)} at root")
    
    # 8. Ensure data/mobile/ has initial data
    mobile_data_dir = ROOT / "data" / "mobile"
    mobile_data_dir.mkdir(parents=True, exist_ok=True)
    
    # Create datasets.json if not exists
    datasets_path = mobile_data_dir / "datasets.json"
    if not datasets_path.exists():
        datasets_path.write_text(json.dumps([{
            "dataset_id": "demo-root",
            "pair": "EUR/USD",
            "candle_count": 200,
            "created_at": datetime.utcnow().isoformat(),
            "location": "data/mobile/ at root level"
        }], indent=2))
        print(f"  ✓ {datasets_path.relative_to(ROOT)} (initial)")
    
    print()
    print(f"✓ All APK configs generated PERMANENTLY at root level")
    print(f"  Backend: {backend}, Live: {is_live}")
    if is_live:
        print(f"  ✓ APK will be LIVE ONLINE 24/7 when built (Supabase at root)")
    else:
        print(f"  ○ APK will be OFFLINE (SQLite at root) - set Supabase URL for live")

def main():
    parser = argparse.ArgumentParser(description="Configure ALL data inside APK permanently at root level")
    parser.add_argument("--check", action="store_true", help="Check root level config")
    parser.add_argument("--init", action="store_true", help="Init data/ at root level")
    parser.add_argument("--supabase", action="store_true", help="Interactive Supabase setup, permanent at root")
    parser.add_argument("--url", type=str, help="Supabase URL (permanent at root)")
    parser.add_argument("--anon-key", type=str, help="Supabase Anon Key (permanent at root)")
    parser.add_argument("--service-key", type=str, default="", help="Supabase Service Key")
    parser.add_argument("--build", action="store_true", help="Generate all APK configs from root .env (for build)")
    args = parser.parse_args()
    
    if args.check:
        check_status()
        return 0
    
    if args.init:
        init_data_at_root()
        generate_apk_configs()
        check_status()
        return 0
    
    if args.supabase:
        print("==> Configure Supabase PERMANENTLY at root level")
        print("    All data inside APK will be configured at root level permanently")
        print()
        print("Steps:")
        print("1. supabase.com -> New Project -> Wait 2 mins")
        print("2. SQL Editor -> New Query -> Copy database/schema.sql at root -> Run")
        print("3. Project Settings -> API -> Copy URL + anon key")
        print()
        url = input("Supabase URL (https://xxx.supabase.co): ").strip()
        if not url:
            print("URL required")
            return 1
        anon_key = input("Supabase Anon Key (eyJ...): ").strip()
        if not anon_key:
            print("Anon key required")
            return 1
        service_key = input("Service Role Key (optional, eyJ...): ").strip()
        
        configure_supabase_permanent(url, anon_key, service_key)
        check_status()
        return 0
    
    if args.url and args.anon_key:
        configure_supabase_permanent(args.url, args.anon_key, args.service_key)
        check_status()
        return 0
    
    if args.build:
        print("==> Building APK permanent config from root .env")
        generate_apk_configs()
        print()
        print("✓ Ready for APK build - all data configured permanently at root")
        return 0
    
    # Default: check + build
    check_status()
    print()
    print("Generating APK configs from current root .env...")
    generate_apk_configs()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
