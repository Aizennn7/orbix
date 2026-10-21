#!/usr/bin/env bash
# Build Orbix Mobile & Laptop interfaces - all Orbix inside APK
# Database PERMANENTLY at root level: database/ + data/ + .env
# All data inside APK configured permanently at root level
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Orbix Mobile & Laptop Builder - ROOT LEVEL PERMANENT"
echo "    All data inside APK configured permanently at root level"
echo "    Root: database/ + data/ + .env (single source of truth)"

# 0. Configure root APK data permanently at root level
echo ""
echo "==> [0/6] Configuring ALL data inside APK permanently at root level"
if [ -f "scripts/configure_root_apk.py" ]; then
  python3 scripts/configure_root_apk.py --build 2>&1 | tail -n 20
  echo "  ✓ Root APK config generated permanently at root level"
  ls -lh database/apk_config.json database/root_manifest.json 2>/dev/null | awk '{print "    ", $9, $5}' || true
  ls -lh mobile/src/js/root_config.js mobile/src/root_config.json data/root_config.json 2>/dev/null | awk '{print "    ", $9, $5}' || true
else
  echo "  WARNING: configure_root_apk.py not found, using legacy"
fi

# 1. Check web assets with mobile enhancements
echo ""
echo "==> [1/6] Checking web with mobile & laptop CSS"
if [ -f "web/index.html" ]; then
  echo "  Web assets present"
  ls -lh web/mobile.css web/laptop.css web/app-mobile.js web/manifest.json web/service-worker.js 2>/dev/null | awk '{print "  ", $9, $5}' || true
else
  echo "  ERROR: web/ missing"
  exit 1
fi

# 2. Check root database config - PERMANENT
echo ""
echo "==> [2/6] Checking root database config PERMANENTLY at root level"
if [ -d "database" ]; then
  echo "  ✓ database/ at root level (single source of truth, permanent)"
  ls -lh database/ | awk '{print "  ", $9, $5}' | head -n 15
  if [ -f "database/apk_config.json" ]; then
    echo "  ✓ database/apk_config.json - PERMANENT APK config at root level"
    cat database/apk_config.json | head -n 10 | awk '{print "    ", $0}'
  fi
else
  echo "  WARNING: database/ not found at root"
fi
if [ -d "data" ]; then
  echo "  ✓ data/ at root level (all Orbix data, permanent)"
  ls -lh data/ | awk '{print "  ", $9, $5}' | head -n 10
  if [ -f "data/root_config.json" ]; then
    echo "  ✓ data/root_config.json - root cache, permanent"
  fi
else
  echo "  ○ data/ not yet at root, will be created"
  mkdir -p data/mobile
fi
if [ -f ".env.example" ]; then
  echo "  ✓ .env.example has DATABASE section at root"
  grep -n "DATABASE_" .env.example | head -n 5 | awk '{print "    ", $0}'
fi
if [ -f ".env" ]; then
  echo "  ✓ .env at root level (permanent, contains SUPABASE_URL for live)"
  grep -E "DATABASE_BACKEND|SUPABASE_URL|SUPABASE_ANON" .env | awk '{print "    ", $0}' | head -n 5
else
  echo "  ○ .env not set at root - will be offline, set SUPABASE_URL for live permanent"
fi

# 3. Build mobile PWA (Capacitor web) with permanent root config
echo ""
echo "==> [3/6] Building mobile PWA (dist/) with PERMANENT root config"
mkdir -p mobile/dist
mkdir -p mobile/dist/js mobile/dist/css mobile/dist/static
mkdir -p mobile/public
mkdir -p mobile/src/js

# Ensure root_config.js exists (permanent)
if [ ! -f "mobile/src/js/root_config.js" ]; then
  echo "  Generating root_config.js from root .env (permanent)"
  python3 scripts/configure_root_apk.py --build 2>&1 | tail -n 5
fi

# Try Vite build if available
if [ -d "mobile/node_modules" ] && command -v npx >/dev/null; then
  echo "  Running Vite build with permanent root config..."
  (cd mobile && npx vite build 2>&1 | tail -n 20) || echo "  Vite build failed, using manual copy"
else
  echo "  Vite not available, using manual copy"
  cp mobile/src/index.html mobile/dist/ 2>/dev/null || true
  mkdir -p mobile/dist/css mobile/dist/js
  cp mobile/src/css/*.css mobile/dist/css/ 2>/dev/null || cp mobile/src/css/mobile-app.css mobile/dist/css/ 2>/dev/null || true
  cp mobile/src/js/*.js mobile/dist/js/ 2>/dev/null || true
fi

# Always copy root-level assets after Vite build (Vite emptyOutDir deletes them)
echo "  Copying root-level assets + permanent config to dist/"
cp web/icon-*.png mobile/dist/ 2>/dev/null || true
cp web/manifest.json mobile/dist/ 2>/dev/null || true
cp web/service-worker.js mobile/dist/ 2>/dev/null || true
mkdir -p mobile/dist/static
cp web/icon-*.png mobile/dist/static/ 2>/dev/null || true
cp web/manifest.json mobile/dist/static/ 2>/dev/null || true
cp web/mobile.css mobile/dist/static/ 2>/dev/null || true
cp web/laptop.css mobile/dist/static/ 2>/dev/null || true
cp web/app-mobile.js mobile/dist/static/ 2>/dev/null || true
cp web/styles.css mobile/dist/static/ 2>/dev/null || true

# Copy PERMANENT root config to dist (critical for APK live)
echo "  Copying PERMANENT root config to dist/ for APK"
cp mobile/src/root_config.json mobile/dist/ 2>/dev/null || echo "    ○ mobile/src/root_config.json not found"
cp mobile/public/root_config.json mobile/dist/ 2>/dev/null || echo "    ○ mobile/public/root_config.json not found"
cp database/apk_config.json mobile/dist/ 2>/dev/null || echo "    ○ database/apk_config.json not found"
cp database/root_manifest.json mobile/dist/ 2>/dev/null || true
cp data/root_config.json mobile/dist/ 2>/dev/null || true
cp data/apk_build_info.json mobile/dist/ 2>/dev/null || true

# Also copy data/mobile/ initial data
mkdir -p mobile/dist/data/mobile
cp data/mobile/*.json mobile/dist/data/mobile/ 2>/dev/null || true
cp data/root_config.json mobile/dist/data/ 2>/dev/null || true

# Also ensure mobile src css/js are in dist for fallback
mkdir -p mobile/dist/css mobile/dist/js
cp mobile/src/css/*.css mobile/dist/css/ 2>/dev/null || true
cp mobile/src/js/*.js mobile/dist/js/ 2>/dev/null || true

# Copy root_config.js explicitly to dist/js (permanent)
cp mobile/src/js/root_config.js mobile/dist/js/ 2>/dev/null || true
cp mobile/src/js/root_config.js mobile/dist/assets/ 2>/dev/null || true

echo "  Mobile PWA built to mobile/dist/ with PERMANENT root config"
ls -lh mobile/dist/ | awk '{print "  ", $9, $5}' | head -n 20
echo "  Root configs in dist:"
ls -lh mobile/dist/*.json mobile/dist/js/root_config.js 2>/dev/null | awk '{print "    ", $9, $5}' || true
ls -lh mobile/dist/data/ 2>/dev/null | awk '{print "    ", $9, $5}' || true

# 4. Prepare Python mobile backend + root DB - PERMANENT
echo ""
echo "==> [4/6] Checking Python mobile backend + root DB PERMANENT"
if [ -f "orbix/mobile_db.py" ] && [ -f "orbix/mobile_api.py" ]; then
  echo "  ✓ orbix/mobile_db.py - SQLite for APK (uses root database/, permanent)"
  echo "  ✓ orbix/mobile_api.py - Offline API (uses root database/, permanent)"
  python3 -m py_compile orbix/mobile_db.py orbix/mobile_api.py database/config.py database/connection.py mobile/src/js/root_config.js 2>/dev/null || python3 -m py_compile orbix/mobile_db.py orbix/mobile_api.py database/config.py database/connection.py && echo "  ✓ Python syntax OK"
  python3 scripts/init_db.py --check 2>&1 | tail -n 15 | awk '{print "    ", $0}'
  python3 scripts/configure_root_apk.py --check 2>&1 | tail -n 20 | awk '{print "    ", $0}'
else
  echo "  ERROR: mobile backend missing"
  exit 1
fi

# 5. Laptop Electron app
echo ""
echo "==> [5/6] Laptop Electron app (uses root database/ permanent)"
if [ -d "electron" ]; then
  echo "  Electron config present at root, uses database/ + data/ at root"
  if command -v npm >/dev/null; then
    echo "  To build laptop installer:"
    echo "    cd electron && npm install && npm run build"
  fi
fi

# 6. Verify permanent root config
echo ""
echo "==> [6/6] Verifying PERMANENT root config for APK"
echo "  Root level files (single source of truth, permanent):"
ls -lh database/apk_config.json database/root_manifest.json data/root_config.json data/apk_build_info.json 2>/dev/null | awk '{print "    ", $9, $5}' || echo "    Some files not yet generated"
echo "  Mobile permanent configs:"
ls -lh mobile/src/js/root_config.js mobile/src/root_config.json mobile/public/root_config.json 2>/dev/null | awk '{print "    ", $9, $5}' || echo "    Some not generated"
echo "  Dist contains permanent root config (will be inside APK):"
ls -lh mobile/dist/root_config.json mobile/dist/apk_config.json mobile/dist/js/root_config.js 2>/dev/null | awk '{print "    ", $9, $5}' || echo "    Checking..."
if [ -f "mobile/dist/root_config.json" ]; then
  echo "  ✓ dist/root_config.json exists - APK will have permanent root config"
  cat mobile/dist/root_config.json | grep -E "backend|is_live|supabase" | head -n 5 | awk '{print "      ", $0}'
else
  echo "  ○ dist/root_config.json missing - APK will be offline unless configured"
fi

echo ""
echo "==> Build complete! ALL data inside APK configured PERMANENTLY at root level"
echo ""
echo "Root level database (PERMANENT, single source of truth):"
echo "  database/              # config.py, connection.py, schema.sql at root"
echo "  database/apk_config.json  # PERMANENT APK config at root (generated from .env)"
echo "  database/root_manifest.json # Manifest of all data inside APK"
echo "  data/                  # orbix.sqlite3, mobile/, root_config.json at root (permanent)"
echo "  data/root_config.json  # Root cache, permanent, bundled into APK"
echo "  .env                   # DATABASE_BACKEND, SUPABASE_URL at root (permanent)"
echo "  DATABASE.md + DATABASE_LIVE.md # guides"
echo ""
echo "Mobile APK structure (reads from ROOT permanently):"
echo "  mobile/src/js/root_config.js  # GENERATED from root .env, hardcoded in APK, permanent"
echo "  mobile/src/root_config.json   # GENERATED from root, bundled in dist"
echo "  mobile/public/root_config.json # For PWA, permanent"
echo "  mobile/dist/                  # Built PWA with permanent root config inside"
echo "  mobile/dist/root_config.json  # Inside APK, permanent"
echo "  mobile/dist/data/             # Initial data from root data/, permanent"
echo ""
echo "How it works (PERMANENT at root):"
echo "  1. You set SUPABASE_URL at root .env (permanent)"
echo "  2. scripts/configure_root_apk.py generates all configs at root level (permanent)"
echo "  3. build_mobile.sh copies root configs into dist/ (permanent inside APK)"
echo "  4. APK contains root_config.js hardcoded + root_config.json bundled"
echo "  5. APK reads from ROOT_CONFIG first (permanent), then localStorage, then API"
echo "  6. All data inside APK configured at root level permanently"
echo ""
echo "Next: Build APK (with permanent root config inside)"
echo "  ./scripts/build_apk.sh"
echo "  OR via GitHub Actions (auto builds APK with permanent root config):"
echo "  - Push to branch -> Actions -> Download orbix-apk-debug (live if Supabase at root)"
echo ""
echo "To make LIVE permanently at root:"
echo "  python scripts/configure_root_apk.py --supabase  # interactive"
echo "  OR python scripts/configure_root_apk.py --url https://xxx.supabase.co --anon-key eyJ..."
echo "  Then rebuild APK - will be LIVE ONLINE 24/7 permanently"
