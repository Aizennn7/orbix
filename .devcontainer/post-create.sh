#!/usr/bin/env bash
# Orbix Codespace Post-Create - Setup root database + mobile + APK builder
set -e

echo "==> Orbix Codespace Setup - Root Database + APK Builder"
echo "    All config at root level: database/ + data/ + .env"

# 1. Python deps at root
echo ""
echo "==> [1/5] Installing Python deps at root"
pip install -r requirements.txt
pip install supabase

# 2. Node deps for mobile
echo ""
echo "==> [2/5] Installing Node deps for mobile PWA"
cd mobile
npm install
cd ..

# 3. Electron deps for laptop
echo ""
echo "==> [3/5] Installing Electron deps for laptop"
cd electron
npm install || echo "Electron install skipped"
cd ..

# 4. Android SDK setup for APK build
echo ""
echo "==> [4/5] Setting up Android SDK for APK build"
if [ -d "/usr/local/lib/android/sdk" ]; then
  export ANDROID_HOME=/usr/local/lib/android/sdk
  export ANDROID_SDK_ROOT=/usr/local/lib/android/sdk
  echo "ANDROID_HOME=/usr/local/lib/android/sdk" >> ~/.bashrc
  echo "ANDROID_SDK_ROOT=/usr/local/lib/android/sdk" >> ~/.bashrc
  sdkmanager --install "platforms;android-34" "build-tools;34.0.0" "platform-tools" || true
  yes | sdkmanager --licenses || true
  echo "✓ Android SDK ready"
else
  echo "Android SDK not preinstalled, will use GitHub Actions for APK build"
fi

# 5. Init root database
echo ""
echo "==> [5/5] Initializing database at root level"
mkdir -p data/mobile
python scripts/init_db.py --check || true
python scripts/init_db.py || true

# 6. Build mobile PWA
echo ""
echo "==> Building mobile PWA at root"
chmod +x scripts/build_mobile.sh
./scripts/build_mobile.sh || true

echo ""
echo "==> Codespace Ready! Root Database Configured"
echo ""
echo "Database at root level:"
echo "  database/ - config.py, connection.py, schema.sql at root"
echo "  data/ - orbix.sqlite3, mobile/ at root"
echo "  .env - DATABASE_BACKEND, SUPABASE_URL at root (create from .env.example)"
echo ""
echo "To make LIVE and ONLINE (not offline):"
echo "  1. Create Supabase project at supabase.com"
echo "  2. Run database/schema.sql in Supabase SQL Editor"
echo "  3. cp .env.example .env and set SUPABASE_URL + SUPABASE_ANON_KEY"
echo "  4. Set DATABASE_BACKEND=supabase at root .env"
echo "  5. python scripts/init_db.py --check -> should show Supabase yes"
echo ""
echo "To run full app live in Codespace:"
echo "  uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1"
echo "  Then open forwarded port 8000 - LIVE PREVIEW"
echo ""
echo "To build APK in Codespace (via GitHub Actions, no Android Studio needed):"
echo "  git push origin <branch> -> Actions -> Download orbix-apk-debug"
echo "  Or locally if Android SDK installed: ./scripts/build_apk.sh"
echo ""
