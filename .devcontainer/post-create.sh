#!/usr/bin/env bash
# Orbix Codespace post-create - install dependencies and initialise the root database.
set -e

echo "==> Orbix Codespace Setup"
echo "    All config at root level: database/ + data/ + .env"

# 1. Python deps at root
echo ""
echo "==> [1/4] Installing Python deps at root"
pip install -r requirements.txt
pip install supabase

# 2. Node deps for the mobile PWA
echo ""
echo "==> [2/4] Installing Node deps for the mobile PWA"
cd mobile
npm install
cd ..

# 3. Electron deps for the desktop client
echo ""
echo "==> [3/4] Installing Electron deps"
cd electron
npm install || echo "Electron install skipped"
cd ..

# 4. Init root database
echo ""
echo "==> [4/4] Initialising the database at root level"
mkdir -p data/mobile
python scripts/init_db.py --check || true
python scripts/init_db.py || true

echo ""
echo "==> Codespace ready."
echo ""
echo "Database at root level:"
echo "  database/ - config.py, connection.py, schema.sql"
echo "  data/     - orbix.sqlite3, candles.duckdb, mobile/"
echo "  .env      - DATABASE_BACKEND, SUPABASE_URL (create from .env.example)"
echo ""
echo "To run the dashboard:"
echo "  uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1"
echo "  Then open forwarded port 8000."
echo ""
echo "To build the mobile PWA:"
echo "  ./scripts/build_mobile.sh"
