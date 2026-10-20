#!/usr/bin/env bash
# Build Orbix Mobile & Laptop interfaces - all Orbix inside APK
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Orbix Mobile & Laptop Builder"
echo "    All Orbix inside APK with local database"

# 1. Build web assets with mobile enhancements
echo ""
echo "==> [1/4] Building web with mobile & laptop CSS"
if [ -f "web/index.html" ]; then
  echo "  Web assets present"
  ls -lh web/mobile.css web/laptop.css web/app-mobile.js web/manifest.json web/service-worker.js | awk '{print "  ", $9, $5}'
else
  echo "  ERROR: web/ missing"
  exit 1
fi

# 2. Build mobile PWA (Capacitor web)
echo ""
echo "==> [2/4] Building mobile PWA (dist/)"
mkdir -p mobile/dist
mkdir -p mobile/dist/js mobile/dist/css

# Copy core files
cp mobile/src/index.html mobile/dist/
cp mobile/src/css/*.css mobile/dist/css/ 2>/dev/null || cp mobile/src/css/mobile-app.css mobile/dist/css/
cp mobile/src/js/*.js mobile/dist/js/

# Copy web assets for offline
mkdir -p mobile/dist/static
cp web/styles.css mobile/dist/static/ 2>/dev/null || true
cp web/mobile.css mobile/dist/static/
cp web/laptop.css mobile/dist/static/
cp web/manifest.json mobile/dist/static/ 2>/dev/null || cp web/manifest.json mobile/dist/
cp web/service-worker.js mobile/dist/ 2>/dev/null || true

echo "  Mobile PWA built to mobile/dist/"
ls -lh mobile/dist/index.html | awk '{print "  ", $9, $5}'

# 3. Prepare Python mobile backend
echo ""
echo "==> [3/4] Checking Python mobile backend"
if [ -f "orbix/mobile_db.py" ] && [ -f "orbix/mobile_api.py" ]; then
  echo "  ✓ orbix/mobile_db.py - SQLite for APK"
  echo "  ✓ orbix/mobile_api.py - Offline API"
  python3 -m py_compile orbix/mobile_db.py orbix/mobile_api.py && echo "  ✓ Python syntax OK"
else
  echo "  ERROR: mobile backend missing"
  exit 1
fi

# 4. Build Electron laptop app (if electron installed)
echo ""
echo "==> [4/4] Laptop Electron app"
if [ -d "electron" ]; then
  echo "  Electron config present"
  if command -v npm >/dev/null; then
    echo "  To build laptop installer:"
    echo "    cd electron && npm install && npm run build"
  fi
else
  echo "  No electron dir"
fi

echo ""
echo "==> Build complete!"
echo ""
echo "Mobile APK structure:"
echo "  mobile/src/          - Source (JS engine, SQLite DB, all Orbix logic)"
echo "  mobile/dist/         - Built PWA (offline, all inside)"
echo "  mobile/android/      - Android project with Chaquopy Python (full Python inside APK)"
echo "  mobile/package.json  - Capacitor config"
echo ""
echo "Laptop:"
echo "  electron/            - Desktop app with Python backend, SQLite, tray, 24/7"
echo "  web/                 - Enhanced with mobile.css + laptop.css + PWA"
echo ""
echo "Database inside APK:"
echo "  - SQLite (native) or IndexedDB (web fallback)"
echo "  - Tables: datasets, candles, signals, settings, learning"
echo "  - All Orbix logic in JS (engine.js) + Python (mobile_db.py)"
echo "  - Works 24/7 offline, syncs to Supabase when online (future)"
echo ""
echo "Next: Build APK"
echo "  ./scripts/build_apk.sh"
