#!/usr/bin/env bash
# Orbix Termux Run - LIVE ONLINE 24/7 at Root Level Permanently
# Run in Termux: chmod +x scripts/termux_run.sh && ./scripts/termux_run.sh
# All data inside APK configured permanently at root level: database/ + data/ + .env

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Orbix LIVE ONLINE in Termux - Root Level Permanent"
echo "    Root: $ROOT"
echo "    All data inside APK configured permanently at root: database/ + data/ + .env"
echo "    Termux: $HOME (Android phone, 24/7)"
echo ""

# Check if in Termux
if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
  echo "✓ Termux detected"
  # Wake lock to keep running 24/7
  if command -v termux-wake-lock >/dev/null 2>&1; then
    echo "  Acquiring wake lock (keeps Termux running 24/7)..."
    termux-wake-lock 2>&1 | head -n 3 || true
  fi
  if command -v termux-wake-unlock >/dev/null 2>&1; then
    echo "  To release wake lock later: termux-wake-unlock"
  fi
else
  echo "○ Not Termux, running as normal Linux"
fi

# Check root config
echo ""
echo "[1/3] Checking permanent root config at root level"
if [ -f "scripts/configure_root_apk.py" ]; then
  python scripts/configure_root_apk.py --check 2>&1 | tail -n 30 || true
else
  echo "  configure_root_apk.py not found"
fi

# Check data at root
echo ""
echo "[2/3] Data at root level (permanent)"
ls -lh data/ 2>/dev/null | awk '{print "  ", $9, $5}' | head -n 10 || echo "  data/ not found at root, run termux_setup.sh"
ls -lh database/apk_config.json mobile/src/js/root_config.js 2>/dev/null | awk '{print "  ", $9, $5}' | head -n 5 || true

if [ -f "database/apk_config.json" ]; then
  echo "  Permanent APK config at root:"
  cat database/apk_config.json | python -m json.tool 2>/dev/null | head -n 15 || cat database/apk_config.json | head -n 10
fi

# Run server
echo ""
echo "[3/3] Starting Orbix LIVE ONLINE at root level in Termux"
echo "  All data inside APK permanently at root: database/ + data/ + .env"
echo "  Server: http://0.0.0.0:8000 (accessible at http://localhost:8000 in phone browser)"
echo "  PWA: http://localhost:3000 if vite running"
echo "  API: http://localhost:8000/api/database/config"
echo "  API: http://localhost:8000/api/database/apk-config (permanent at root)"
echo ""
echo "  Keep Termux running 24/7:"
echo "  - termux-wake-lock (in this script)"
echo "  - Disable battery optimization for Termux in Android Settings -> Apps -> Termux -> Battery -> Unrestricted"
echo "  - Keep Termux notification"
echo ""
echo "  To run in background in Termux:"
echo "  - Use tmux: pkg install tmux, tmux new -s orbix, run this script, detach Ctrl+B D"
echo "  - Or: nohup ./scripts/termux_run.sh &"
echo "  - Or: nohup uvicorn orbix.api:app --host 0.0.0.0 --port 8000 &"
echo ""
echo "  To stop: Ctrl+C, then termux-wake-unlock"
echo ""

# Check if port 8000 busy
if command -v lsof >/dev/null 2>&1; then
  if lsof -i :8000 >/dev/null 2>&1; then
    echo "⚠️  Port 8000 busy, trying to kill..."
    lsof -ti :8000 | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
fi

# Start server
echo "==> Starting uvicorn at root level (permanent)..."
echo "    $ROOT - database/ + data/ + .env at root"
echo ""

# Use python from Termux or system
if command -v uvicorn >/dev/null 2>&1; then
  uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1
else
  python -m uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1
fi
