# Orbix in Termux - All Data Inside APK Permanently at Root Level

Run Orbix live 24/7 on your Android phone using Termux, with all data configured permanently at root level.

---

## Why Termux?

- **No hosting needed** - runs 24/7 on your own device
- **All data at root level** - `database/` + `data/` + `.env` at root, permanent
- **Live online** - Supabase at root level, syncs to cloud
- **APK inside Termux** - build and run PWA + APK directly on phone
- **GitHub files** - all files in GitHub, not just on device, clone in Termux

---

## Quick Start in Termux (5 mins)

### 1. Install Termux

- Install **Termux** from **F-Droid** (not Play Store, outdated): https://f-droid.org/en/packages/com.termux/
- Open Termux
- Run:
```bash
termux-setup-storage
# Allow storage permission
pkg update -y && pkg upgrade -y
pkg install -y python nodejs git termux-tools termux-api
pip install --upgrade pip
```

### 2. Clone Orbix (All Files from GitHub at Root Level)

```bash
cd ~
git clone https://github.com/Aizennn7/orbix.git
cd orbix
# All files now at ~/orbix at root level (in Termux home)
ls -la database/  # root level DB config, permanent
ls -la scripts/   # setup scripts
```

### 3. Setup in Termux (Root Level Permanent)

```bash
# Run Termux setup - installs deps, inits data/ at root level permanently
chmod +x scripts/termux_setup.sh
./scripts/termux_setup.sh

# This does:
# - pip install -r requirements.txt + supabase
# - npm install in mobile/
# - python scripts/configure_root_apk.py --init (creates data/ at root)
# - python scripts/configure_root_apk.py --build (generates permanent root configs)
# - Creates database/apk_config.json at root (permanent)
# - Creates mobile/src/js/root_config.js hardcoded (permanent in APK)
```

### 4. Configure Supabase LIVE ONLINE Permanently at Root Level (Termux)

**Option A: Interactive (recommended in Termux)**

```bash
python scripts/configure_root_apk.py --supabase
# Paste:
# Supabase URL: https://xxx.supabase.co (from supabase.com Project Settings -> API)
# Anon Key: eyJ... (from same page)
# Service Key: eyJ... (optional)

# Saves PERMANENTLY at root level in Termux:
# - ~/orbix/.env (DATABASE_BACKEND=supabase, SUPABASE_URL, etc.)
# - ~/orbix/database/apk_config.json (permanent, committed)
# - ~/orbix/mobile/src/js/root_config.js (hardcoded in APK, permanent)
# - ~/orbix/data/root_config.json (cache at root)
```

**Option B: Non-interactive**

```bash
python scripts/configure_root_apk.py --url https://xxx.supabase.co --anon-key eyJ... --service-key eyJ...
```

**How to get Supabase URL (in Termux browser or phone browser):**

1. Go to https://supabase.com on phone browser
2. New Project -> Name: orbix-live -> Wait 2 mins
3. SQL Editor -> New Query -> Copy `database/schema.sql` from `~/orbix/database/schema.sql` -> Run (creates tables)
4. Project Settings -> API -> Copy URL + anon key
5. Paste in Termux prompt above

### 5. Verify Permanent at Root Level (Termux)

```bash
python scripts/configure_root_apk.py --check
# Should show:
# - database/apk_config.json at root (permanent)
# - data/root_config.json at root
# - mobile/src/js/root_config.js hardcoded (permanent)
# - Backend: supabase -> LIVE ONLINE

cat database/apk_config.json
cat mobile/src/js/root_config.js | head -n 20
ls -lh data/  # data/ at root level, all Orbix data
```

### 6. Build Mobile PWA with Permanent Root Config (Termux)

```bash
./scripts/build_mobile.sh
# Generates:
# - mobile/dist/ with permanent root configs inside
# - mobile/dist/root_config.json (from root)
# - mobile/dist/apk_config.json (from database/ at root)
# - mobile/dist/js/root_config.js (hardcoded)
# - mobile/dist/data/mobile/ (initial data from root data/)

ls mobile/dist/*.json mobile/dist/js/root_config.js
cat mobile/dist/root_config.json | grep -E "backend|is_live"
```

### 7. Run LIVE ONLINE 24/7 in Termux (Root Level)

```bash
# Run Termux live server (all data at root level permanently)
./scripts/termux_run.sh

# Or manually:
uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1

# In Termux, open new session (swipe from left -> New Session) and:
curl http://localhost:8000/api/database/config | python -m json.tool
curl http://localhost:8000/api/database/apk-config | python -m json.tool
# Should show is_live true, supabase_configured true, permanent at root

# To access from phone browser:
# http://localhost:8000
# Or if you want to expose:
# termux-open-url http://localhost:8000
```

**Keep Termux running 24/7:**

- In Termux: `termux-wake-lock` (prevents sleep)
- Swipe notification -> Acquire wakelock
- Run in background: `nohup uvicorn orbix.api:app --host 0.0.0.0 --port 8000 &`
- Or use `tmux`: `pkg install tmux`, `tmux new -s orbix`, run server, detach with Ctrl+B D

### 8. Access PWA in Phone Browser (Termux)

```bash
# After building mobile PWA:
cd mobile
npx vite --host 0.0.0.0 --port 3000
# Then in phone browser: http://localhost:3000
# Or: termux-open-url http://localhost:3000

# PWA will show:
# - Badge: ● LIVE ONLINE blue (because root_config.js has Supabase from root permanently)
# - All data inside APK configured at root level permanently
```

---

## Termux Scripts (Root Level Permanent)

### `scripts/termux_setup.sh` - Setup Everything at Root Level

```bash
#!/data/data/com.termux/files/usr/bin/bash
# Setup Orbix in Termux - all data at root level permanently
cd ~/orbix
pkg install -y python nodejs git
pip install -r requirements.txt supabase
cd mobile && npm install && cd ..
python scripts/configure_root_apk.py --init
python scripts/configure_root_apk.py --build
./scripts/build_mobile.sh
echo "✓ Setup complete at root level ~/orbix"
echo "  database/ + data/ + .env at root (permanent)"
```

### `scripts/termux_run.sh` - Run Live 24/7 in Termux

```bash
#!/data/data/com.termux/files/usr/bin/bash
cd ~/orbix
echo "==> Orbix LIVE ONLINE at root level (Termux)"
echo "    All data inside APK permanently at root: database/ + data/ + .env"
python scripts/configure_root_apk.py --check
echo ""
echo "Starting server at http://localhost:8000 (permanent at root)"
echo "Keep Termux awake: termux-wake-lock"
uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1
```

---

## All Data Inside APK Permanently at Root Level (Termux)

In Termux, root level is `~/orbix/` (Termux home):

```
~/orbix/                                  <- ROOT LEVEL in Termux (permanent)
├── database/                             <- ROOT LEVEL DB (permanent, single source)
│   ├── apk_config.json                   <- PERMANENT APK config at root (Supabase URL)
│   ├── root_manifest.json                <- Manifest at root
│   ├── config.py, schema.sql
│   └── ...
├── data/                                 <- ROOT LEVEL DATA (permanent, all Orbix data)
│   ├── orbix.sqlite3                     <- SQLite at root (offline fallback)
│   ├── mobile/                           <- Mobile datasets at root
│   ├── root_config.json                  <- Cache at root (permanent)
│   └── apk_build_info.json
├── .env                                  <- ROOT LEVEL ENV (permanent, Supabase at root)
├── mobile/
│   ├── src/js/root_config.js             <- HARDCODED from root .env, permanent in APK
│   └── dist/                             <- Built PWA with permanent root config inside
└── scripts/
    ├── configure_root_apk.py             <- Generates permanent root configs
    ├── termux_setup.sh                   <- Setup in Termux
    └── termux_run.sh                     <- Run live in Termux
```

**Why permanent?**

- `database/apk_config.json` at root is committed to GitHub, generated from root `.env`
- `mobile/src/js/root_config.js` hardcoded in APK JS, contains Supabase URL from root, permanent
- `data/` at root contains all Orbix data, `data/root_config.json` cache
- When you clone in Termux, you get all files at `~/orbix` root level, permanent
- When you run `configure_root_apk.py --supabase` in Termux, it saves at root level permanently

---

## Build APK in Termux? (Advanced)

Building APK in Termux is hard (needs Android SDK, Java). Easier:

**Option 1: GitHub Actions (Recommended, from Termux)**

```bash
# In Termux, after configuring Supabase at root:
cd ~/orbix
git add database/apk_config.json mobile/src/js/root_config.js
git commit -m "feat: permanent root config live from Termux"
git push origin arena/6260349f-orbix
# Go to GitHub on phone browser: https://github.com/Aizennn7/orbix/actions
# Download orbix-apk-debug artifact (11.9 MB) - contains permanent root config, LIVE ONLINE
# Install APK on phone
```

**Option 2: Build PWA Only in Termux (no APK, but live)**

```bash
# In Termux:
cd ~/orbix
./scripts/build_mobile.sh
cd mobile
npx vite --host 0.0.0.0 --port 3000
# Open http://localhost:3000 in phone browser
# Add to Home Screen -> PWA works like APK, live online 24/7
```

**Option 3: Full APK Build in Termux (experimental)**

```bash
# In Termux (needs ~2GB storage):
pkg install -y openjdk-17 gradle android-tools
# Download Android SDK manually (complex)
# Or use: https://github.com/termux/termux-packages/issues/...
# Better to use GitHub Actions
```

---

## Termux Commands Cheat Sheet (Root Level Permanent)

```bash
# Setup
cd ~/orbix
./scripts/termux_setup.sh

# Configure LIVE ONLINE permanently at root
python scripts/configure_root_apk.py --supabase
# or
python scripts/configure_root_apk.py --url https://xxx --anon-key eyJ...

# Check permanent root config
python scripts/configure_root_apk.py --check
cat database/apk_config.json
cat mobile/src/js/root_config.js

# Build PWA with permanent root config
./scripts/build_mobile.sh

# Run live 24/7 at root level
./scripts/termux_run.sh
# or
uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1

# Keep alive
termux-wake-lock
nohup uvicorn orbix.api:app --host 0.0.0.0 --port 8000 &

# Access
curl http://localhost:8000/api/database/apk-config | python -m json.tool
termux-open-url http://localhost:8000
termux-open-url http://localhost:3000  # if vite running

# Push permanent config to GitHub (all files in GitHub not on device)
git add database/apk_config.json mobile/src/js/root_config.js
git commit -m "feat: live from Termux at root"
git push

# Pull latest
git pull origin arena/6260349f-orbix
```

---

## Troubleshooting Termux

- **pkg install fails?** `termux-change-repo` -> select mirror, `pkg update -y`
- **Storage permission?** `termux-setup-storage`, allow
- **Port 8000 busy?** `lsof -i :8000`, `kill -9 PID`, or use different port `--port 8001`
- **Python not found?** `pkg install python`, `pip install -r requirements.txt`
- **Node not found?** `pkg install nodejs`, `npm install`
- **APK not live?** Check `database/apk_config.json` has supabase_url, rebuild PWA, check badge
- **Termux sleeps?** `termux-wake-lock`, keep Termux notification, disable battery optimization for Termux in Android Settings
- **Data lost?** `data/` at root is gitignored, but `database/apk_config.json` + `mobile/src/js/root_config.js` are permanent at root (committed), so re-clone and re-run `configure_root_apk.py --init`

---

## Summary - Termux Root Level Permanent

- **Install Termux** from F-Droid
- **Clone** `https://github.com/Aizennn7/orbix.git` to `~/orbix` (root level in Termux)
- **Setup:** `./scripts/termux_setup.sh` (creates `data/` + `database/apk_config.json` + `mobile/src/js/root_config.js` at root permanently)
- **Live:** `python scripts/configure_root_apk.py --supabase` (saves Supabase URL permanently at root level `.env` + `apk_config.json` + `root_config.js`)
- **Build:** `./scripts/build_mobile.sh` (PWA with permanent root config inside)
- **Run:** `./scripts/termux_run.sh` or `uvicorn orbix.api:app --host 0.0.0.0 --port 8000` (live 24/7 at root)
- **APK:** `git push` -> GitHub Actions builds APK with permanent root config (11.9 MB, live online)
- **All files in GitHub** not just on device, root level `database/` + `data/` + `.env` permanent, all data inside APK configured at root level permanently, works 24/7 in Termux on your phone.
