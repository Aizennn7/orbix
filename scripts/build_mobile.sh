#!/usr/bin/env bash
# Build Orbix Mobile & Laptop interfaces - all Orbix inside APK
# Database at root level: database/ + data/ + .env
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Orbix Mobile & Laptop Builder"
echo "    All Orbix inside APK with local database at root level"

# 1. Check web assets with mobile enhancements
echo ""
echo "==> [1/5] Checking web with mobile & laptop CSS"
if [ -f "web/index.html" ]; then
  echo "  Web assets present"
  ls -lh web/mobile.css web/laptop.css web/app-mobile.js web/manifest.json web/service-worker.js 2>/dev/null | awk '{print "  ", $9, $5}' || true
else
  echo "  ERROR: web/ missing"
  exit 1
fi

# 2. Check root database config
echo ""
echo "==> [2/5] Checking root database config"
if [ -d "database" ]; then
  echo "  ✓ database/ at root level"
  ls -lh database/ | awk '{print "  ", $9, $5}' | head -n 10
else
  echo "  WARNING: database/ not found at root"
fi
if [ -f "database.py" ] || [ -f "orbix/database.py" ]; then
  echo "  ✓ database.py root shortcut exists"
fi
if [ -f ".env.example" ]; then
  echo "  ✓ .env.example has DATABASE section at root"
  grep -n "DATABASE_" .env.example | head -n 5 | awk '{print "    ", $0}'
fi

# 3. Build mobile PWA (Capacitor web)
echo ""
echo "==> [3/5] Building mobile PWA (dist/)"
mkdir -p mobile/dist
mkdir -p mobile/dist/js mobile/dist/css mobile/dist/static

# Try Vite build if available
if [ -d "mobile/node_modules" ] && command -v npx >/dev/null; then
  echo "  Running Vite build..."
  (cd mobile && npx vite build 2>&1 | tail -n 20) || echo "  Vite build failed, using manual copy"
else
  echo "  Vite not available, using manual copy"
  cp mobile/src/index.html mobile/dist/ 2>/dev/null || true
  mkdir -p mobile/dist/css mobile/dist/js
  cp mobile/src/css/*.css mobile/dist/css/ 2>/dev/null || cp mobile/src/css/mobile-app.css mobile/dist/css/ 2>/dev/null || true
  cp mobile/src/js/*.js mobile/dist/js/ 2>/dev/null || true
fi

# Always copy root-level assets after Vite build (Vite emptyOutDir deletes them)
echo "  Copying root-level assets to dist/"
cp web/icon-*.png mobile/dist/ 2>/dev/null || true
cp web/manifest.json mobile/dist/ 2>/dev/null || true
cp web/service-worker.js mobile/dist/ 2>/dev/null || true
cp web/mobile.css mobile/dist/static/ 2>/dev/null || true
cp web/laptop.css mobile/dist/static/ 2>/dev/null || true
cp web/app-mobile.js mobile/dist/static/ 2>/dev/null || true
cp web/styles.css mobile/dist/static/ 2>/dev/null || true
mkdir -p mobile/dist/static
cp web/icon-*.png mobile/dist/static/ 2>/dev/null || true
cp web/manifest.json mobile/dist/static/ 2>/dev/null || true

# Also ensure mobile src css/js are in dist for fallback
mkdir -p mobile/dist/css mobile/dist/js
cp mobile/src/css/*.css mobile/dist/css/ 2>/dev/null || true
cp mobile/src/js/*.js mobile/dist/js/ 2>/dev/null || true

echo "  Mobile PWA built to mobile/dist/"
ls -lh mobile/dist/ | awk '{print "  ", $9, $5}' | head -n 20

# 4. Prepare Python mobile backend + root DB
echo ""
echo "==> [4/5] Checking Python mobile backend + root DB"
if [ -f "orbix/mobile_db.py" ] && [ -f "orbix/mobile_api.py" ]; then
  echo "  ✓ orbix/mobile_db.py - SQLite for APK (uses root database/)"
  echo "  ✓ orbix/mobile_api.py - Offline API (uses root database/)"
  python3 -m py_compile orbix/mobile_db.py orbix/mobile_api.py database/config.py database/connection.py && echo "  ✓ Python syntax OK"
  python3 scripts/init_db.py --check 2>&1 | tail -n 10 | awk '{print "    ", $0}'
else
  echo "  ERROR: mobile backend missing"
  exit 1
fi

# 5. Laptop Electron app
echo ""
echo "==> [5/5] Laptop Electron app"
if [ -d "electron" ]; then
  echo "  Electron config present at root"
  if command -v npm >/dev/null; then
    echo "  To build laptop installer:"
    echo "    cd electron && npm install && npm run build"
  fi
fi

echo ""
echo "==> Build complete! Database at root level"
echo ""
echo "Root level database:"
echo "  database/              # config.py, connection.py, schema.sql at root"
echo "  data/                  # orbix.sqlite3, mobile/ at root"
echo "  .env                   # DATABASE_BACKEND, SUPABASE_URL at root"
echo "  DATABASE.md            # guide"
echo ""
echo "Mobile APK structure:"
echo "  mobile/src/          - Source (JS engine, SQLite DB, all Orbix logic)"
echo "  mobile/dist/         - Built PWA (offline, all inside, root DB)"
echo "  mobile/android-chaquopy/ - Android with Chaquopy Python (full Python inside APK)"
echo "  mobile/package.json  - Capacitor config"
echo ""
echo "Laptop:"
echo "  electron/            - Desktop app with Python backend, SQLite at root, tray, 24/7"
echo "  web/                 - Enhanced with mobile.css + laptop.css + PWA"
echo ""
echo "Database inside APK (root level):"
echo "  - SQLite (native) or IndexedDB (web fallback)"
echo "  - Tables: datasets, candles, signals, settings, learning"
echo "  - All Orbix logic in JS (engine.js) + Python (mobile_db.py) using root database/"
echo "  - Works 24/7 offline, syncs to Supabase when online (SUPABASE_URL at root .env)"
echo ""
echo "Next: Build APK"
echo "  ./scripts/build_apk.sh"
echo "  OR via GitHub Actions (auto builds APK, no Android Studio needed):"
echo "  - Push to arena/cba3a92e-orbix -> Actions -> Download orbix-apk-debug"
