# Orbix LIVE and ONLINE - Not Offline DB

**Problem:** APK says "offline db" — you want live and online 24/7.

**Solution:** Configure Supabase at **root level** in Codespace. All database config lives at root: `database/` + `data/` + `.env`

---

## Why Offline Before?

- `DATABASE_BACKEND=auto` without `SUPABASE_URL` → falls back to SQLite at `data/orbix.sqlite3` (offline)
- APK used CapacitorSQLite + IndexedDB (offline-first) → shows "OFFLINE DB" badge

## How to Make LIVE and ONLINE (in Codespace)

### 1. Create Supabase Project (2 mins)

1. Go to https://supabase.com → Sign in → New Project
2. Project name: `orbix-live`, set password, wait 2 mins
3. Go to **SQL Editor** → **New Query**
4. Copy entire `database/schema.sql` from root and **Run** (creates tables: candles, datasets, signals, etc.)
5. Go to **Project Settings → API** → Copy:
   - **Project URL** (e.g., `https://abcdefgh.supabase.co`)
   - **anon public** key (eyJ...)
   - **service_role** key (eyJ... optional)

### 2. Configure at Root Level in Codespace

In Codespace terminal at repo root (`/home/user/orbix`):

```bash
# Interactive setup - writes to root .env
python scripts/setup_supabase.py

# Or non-interactive:
python scripts/setup_supabase.py --url https://xxx.supabase.co --anon-key eyJ... --service-key eyJ...

# Or manually:
cp .env.example .env
# Edit .env at root:
# DATABASE_BACKEND=supabase
# SUPABASE_URL=https://xxx.supabase.co
# SUPABASE_ANON_KEY=eyJ...
# SUPABASE_SERVICE_KEY=eyJ...

# Check - should show LIVE ONLINE
python scripts/setup_supabase.py --check
python scripts/init_db.py --check
```

### 3. Run Live in Codespace (24/7)

```bash
# Devcontainer already installed deps
uvicorn orbix.api:app --host 0.0.0.0 --port 8000 --workers 1

# Open forwarded port 8000 → LIVE PREVIEW
# Should show: Supabase yes, LIVE ONLINE
# API: /api/database/config → {"supabase_configured": true}
```

### 4. Build APK that is Live Online 24/7

**Option A: GitHub Actions (no Android Studio needed, works in Codespace)**

```bash
# Build mobile PWA at root
./scripts/build_mobile.sh

# Commit and push
git add mobile/dist/ database/ .devcontainer/ scripts/setup_supabase.py DATABASE_LIVE.md
git commit -m "feat: live online Supabase at root, APK live"
git push origin arena/6260349f-orbix

# Go to GitHub → Actions → build-apk workflow → Download orbix-apk-debug artifact
# That APK will be live online when SUPABASE_URL set, offline fallback otherwise
```

**Option B: Local build in Codespace (if Android SDK installed via devcontainer)**

```bash
./scripts/build_apk.sh
# APK at mobile/android-chaquopy/app/build/outputs/apk/debug/app-debug.apk
# Install on phone → Settings ⚙️ → paste Supabase URL + key → SAVE & GO LIVE ONLINE
```

### 5. On Phone - Make APK Live

1. Install APK on Android phone
2. Open app → tap ⚙️ Settings (top right)
3. Paste **same** Supabase URL + Anon Key from root `.env`
4. Tap **SAVE & GO LIVE ONLINE**
5. Badge changes from "OFFLINE DB" → "● LIVE ONLINE" (blue)
6. Now: signals sync to Supabase Realtime 24/7, even when app backgrounded
7. Works offline too → auto-syncs when back online

---

## Root Level Database Structure

```
orbix/                         ← ROOT
├── database/                  ← ROOT LEVEL DB (single source of truth)
│   ├── config.py              ← DatabaseBackend.AUTO detects supabase/postgres/sqlite
│   ├── connection.py
│   ├── schema.sql             ← Run this in Supabase SQL Editor for live
│   └── sqlite_schema.sql
├── data/                      ← ROOT LEVEL DATA
│   ├── orbix.sqlite3          ← Local SQLite (offline fallback)
│   ├── mobile/                ← Mobile datasets
│   ├── runtime_supabase.json  ← Live config cache from mobile
│   └── mobile_sync.json       ← Sync log
├── .env                       ← ROOT LEVEL ENV (gitignored)
│   ├── DATABASE_BACKEND=supabase
│   ├── SUPABASE_URL=https://...
│   ├── SUPABASE_ANON_KEY=eyJ...
│   └── SUPABASE_SERVICE_KEY=eyJ...
├── .env.example               ← Template at root
├── scripts/
│   ├── setup_supabase.py      ← Interactive root config for live online
│   ├── init_db.py             ← Init/check root DB
│   ├── build_mobile.sh
│   └── build_apk.sh
├── mobile/                    ← PWA + APK
│   ├── src/js/live_db.js      ← NEW: Supabase Realtime sync
│   └── dist/                  ← Built PWA
└── .devcontainer/             ← Codespace: Python+Node+Android
    ├── devcontainer.json
    ├── post-create.sh
    └── post-start.sh
```

## How Auto-Detection Works

`database/config.py` at root:

```python
DATABASE_BACKEND=auto  # default
- If SUPABASE_URL + SUPABASE_ANON_KEY set at root .env → backend=supabase (LIVE ONLINE)
- Else if DATABASE_URL set → postgres (LIVE ONLINE)
- Else if duckdb installed → duckdb (offline but fast)
- Else → sqlite at data/orbix.sqlite3 (offline)
```

To force live: set `DATABASE_BACKEND=supabase` at root `.env`.

## Verification

```bash
# In Codespace
curl http://localhost:8000/api/database/config | jq
# Should show: "supabase_configured": true, "backend": "supabase"

curl http://localhost:8000/api/database/health | jq
# Health check

# In mobile APK
# Settings → Status → Should show LIVE ONLINE + Backend supabase (live)
```

## Costs - Free Forever

- Supabase Free: 500MB DB, 50k MAU, unlimited API, Realtime included → enough for Orbix signals
- GitHub Codespace Free: 60 hours/month
- GitHub Actions Free: 2000 mins/month → APK builds
- APK: runs 24/7 on your own device, no hosting cost

## Troubleshooting

- Still "OFFLINE DB"? → Check root `.env` has `SUPABASE_URL`, run `python scripts/setup_supabase.py --check`
- Supabase tables not found? → Did you run `database/schema.sql` in Supabase SQL Editor?
- APK not live? → Open Settings ⚙️ in APK, paste same URL/key as root `.env`, SAVE
- Codespace not forwarding? → Ports tab → Add 8000, make public
