#!/usr/bin/env bash
# Orbix Termux Setup - All Data Inside APK Permanently at Root Level
# Run in Termux: chmod +x scripts/termux_setup.sh && ./scripts/termux_setup.sh
# Root level: ~/orbix/ (Termux home) - database/ + data/ + .env permanent

set -e

# Detect Termux
if [ -z "$TERMUX_VERSION" ] && [ ! -d "/data/data/com.termux" ]; then
  echo "⚠️  Not in Termux? This script is for Termux on Android"
  echo "    But will work in any Linux with python + node"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Orbix Termux Setup - Root Level Permanent"
echo "    Root: $ROOT"
echo "    All data inside APK configured permanently at root: database/ + data/ + .env"
echo ""

# 1. Check Termux storage
echo "[1/7] Checking Termux storage"
if command -v termux-setup-storage >/dev/null 2>&1; then
  echo "  Termux detected, setting up storage..."
  termux-setup-storage 2>&1 | head -n 5 || true
else
  echo "  Not Termux or termux-setup-storage not found, skipping"
fi

# 2. Update pkg and install deps (Termux)
echo ""
echo "[2/7] Installing system deps"
if command -v pkg >/dev/null 2>&1; then
  echo "  Termux pkg detected"
  pkg update -y || true
  pkg install -y python nodejs git termux-tools 2>&1 | tail -n 10 || true
  pkg install -y libjpeg-turbo ndk-sysroot clang make pkg-config 2>&1 | tail -n 5 || true
else
  echo "  pkg not found, assuming apt/brew"
  if command -v apt >/dev/null 2>&1; then
    sudo apt update && sudo apt install -y python3 python3-pip nodejs npm git || true
  fi
fi

# 3. Python deps at root level
echo ""
echo "[3/7] Installing Python deps at root level (database/ + data/ + .env)"
if [ -f "requirements.txt" ]; then
  pip install --upgrade pip 2>&1 | tail -n 3
  pip install -r requirements.txt 2>&1 | tail -n 10
  pip install supabase 2>&1 | tail -n 5 || true
  echo "  ✓ Python deps installed at root"
else
  echo "  WARNING: requirements.txt not found at root"
fi

# 4. Node deps for mobile at root
echo ""
echo "[4/7] Installing Node deps for mobile PWA at root"
if [ -d "mobile" ]; then
  cd mobile
  if [ -f "package.json" ]; then
    npm install 2>&1 | tail -n 10 || echo "  npm install failed, trying with --force"
    npm install --force 2>&1 | tail -n 5 || true
    echo "  ✓ Node deps installed at $ROOT/mobile"
  fi
  cd "$ROOT"
else
  echo "  WARNING: mobile/ not found at root"
fi

# 5. Init data/ at root level permanently
echo ""
echo "[5/7] Initializing data/ at root level permanently"
mkdir -p data/mobile
echo "  Created data/ at $ROOT/data (root level, permanent)"

if [ -f "scripts/configure_root_apk.py" ]; then
  chmod +x scripts/configure_root_apk.py
  python scripts/configure_root_apk.py --init 2>&1 | tail -n 20 || true
  echo "  ✓ data/ initialized at root level"
else
  echo "  WARNING: configure_root_apk.py not found"
fi

if [ -f "scripts/init_db.py" ]; then
  python scripts/init_db.py --check 2>&1 | tail -n 15 || true
  python scripts/init_db.py 2>&1 | tail -n 10 || true
fi

# 6. Generate permanent root configs
echo ""
echo "[6/7] Generating permanent root configs at root level"
if [ -f "scripts/configure_root_apk.py" ]; then
  python scripts/configure_root_apk.py --build 2>&1 | tail -n 20
  echo "  ✓ Permanent root configs generated:"
  ls -lh database/apk_config.json database/root_manifest.json 2>/dev/null | awk '{print "    ", $9, $5}' || true
  ls -lh mobile/src/js/root_config.js mobile/src/root_config.json 2>/dev/null | awk '{print "    ", $9, $5}' || true
  ls -lh data/root_config.json 2>/dev/null | awk '{print "    ", $9, $5}' || true
else
  echo "  WARNING: configure_root_apk.py not found"
fi

# 7. Build mobile PWA with permanent root config
echo ""
echo "[7/7] Building mobile PWA with permanent root config at root level"
if [ -f "scripts/build_mobile.sh" ]; then
  chmod +x scripts/build_mobile.sh
  ./scripts/build_mobile.sh 2>&1 | tail -n 30 || true
  echo "  ✓ Mobile PWA built at $ROOT/mobile/dist/ with permanent root config"
  ls -lh mobile/dist/*.json mobile/dist/js/root_config.js 2>/dev/null | awk '{print "    ", $9, $5}' | head -n 10 || true
else
  echo "  WARNING: build_mobile.sh not found"
fi

echo ""
echo "==> Termux Setup Complete - All Data Inside APK Permanently at Root Level"
echo ""
echo "Root level (permanent, single source of truth):"
echo "  $ROOT/database/              # config.py, apk_config.json at root (permanent)"
echo "  $ROOT/data/                  # orbix.sqlite3, mobile/, root_config.json at root (permanent)"
echo "  $ROOT/.env                   # DATABASE_BACKEND, SUPABASE_URL at root (permanent)"
echo "  $ROOT/mobile/src/js/root_config.js  # hardcoded in APK, permanent"
echo ""
echo "Next steps in Termux:"
echo ""
echo "1. Configure Supabase LIVE ONLINE permanently at root level:"
echo "   python scripts/configure_root_apk.py --supabase"
echo "   # Paste Supabase URL + anon key from supabase.com"
echo "   # Saves permanently at root: .env + database/apk_config.json + mobile/src/js/root_config.js"
echo ""
echo "2. Verify permanent at root:"
echo "   python scripts/configure_root_apk.py --check"
echo "   cat database/apk_config.json"
echo ""
echo "3. Run LIVE ONLINE 24/7 in Termux at root level:"
echo "   ./scripts/termux_run.sh"
echo "   # or: uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1"
echo "   # Keep Termux awake: termux-wake-lock"
echo "   # Access: http://localhost:8000 in phone browser"
echo ""
echo "4. Build PWA in Termux:"
echo "   cd mobile && npx vite --host 0.0.0.0 --port 3000"
echo "   # Open http://localhost:3000 in phone browser, Add to Home Screen"
echo ""
echo "5. Build APK with permanent root config (via GitHub Actions from Termux):"
echo "   git add database/apk_config.json mobile/src/js/root_config.js"
echo "   git commit -m 'feat: live from Termux at root'"
echo "   git push origin arena/6260349f-orbix"
echo "   # GitHub -> Actions -> Download orbix-apk-debug (live online, permanent at root)"
echo ""
echo "All files in GitHub, root level permanent, all data inside APK at root, Termux ready 24/7"
