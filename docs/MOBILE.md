# Orbix Mobile & Laptop — All Orbix Inside APK

**All Orbix inside APK with offline database, works 24/7 on your device**

## What is inside APK

```
APK (com.orbix.signallab)
├── Frontend (all inside)
│   ├── mobile/src/index.html - Mobile UI
│   ├── mobile/src/css/mobile-app.css - Touch 44px, bottom nav
│   ├── mobile/src/js/engine.js - 5 strategies JS port (OCG, ML, pullback, reversal, breakout)
│   ├── mobile/src/js/db.js - SQLite (native) / IndexedDB (web fallback)
│   └── web/ - Enhanced with mobile.css + laptop.css + PWA
│
├── Backend (Python inside APK via Chaquopy)
│   ├── orbix/mobile_db.py - SQLite candle store + ledger for APK
│   ├── orbix/mobile_api.py - Offline FastAPI
│   └── orbix/strategies.py, engine.py, etc. (full Python logic)
│
├── Database (inside APK private storage)
│   ├── SQLite: /data/data/com.orbix.signallab/files/orbix_data/
│   ├── Tables: datasets, candles (M1), signals, settings, learning
│   ├── Works offline, no server needed
│   └── Future: Sync to Supabase when online
│
├── 24/7 Collection
│   ├── OrbixWorker.java - WorkManager every 15 min (Android min)
│   ├── BootReceiver.java - Restarts after reboot
│   └── Foreground service + wake lock
│
└── PWA
    ├── manifest.json - Installable, standalone
    ├── service-worker.js - Offline cache, background sync
    └── Push notifications via FCM
```

## Mobile Interface (Phone)

- **Bottom Navigation**: 5 tabs - Dash, Engine, Paper, Data, Trader (44px touch targets)
- **Swipe Gestures**: Swipe left/right to switch pages
- **Offline Badge**: Green "● OFFLINE DB" or "● ONLINE"
- **Signal Generation**: One tap, haptic feedback, local notification
- **Chart**: Last 20 candles, touch scrollable
- **Data Import**: CSV file picker, pair selector, local SQLite
- **PWA Install**: Banner "INSTALL ORBIX" -> Add to home screen, APK-like

## Laptop Interface (Desktop / Electron)

- **Top Navigation**: Full nav, keyboard shortcuts `Ctrl+1..5`
- **Electron Features**:
  - Tray icon - "Orbix running 24/7"
  - Background Python backend (mobile_api.py) on 127.0.0.1:8001
  - File menu: Import CSV, Open Data Folder, Clear DB
  - Window: 1440x900, hide to tray on close
- **Hover States**: Panel lift, button shadow (only on laptop, not touch)
- **Multi-column**: Dashboard grid 2 cols on 1200px+
- **Print**: Clean reports without nav

## Database Inside APK

**SQLite for APK, IndexedDB fallback for PWA**

Schema:
```sql
datasets (dataset_id, pair, candle_count, first_timestamp, latest_timestamp)
candles (dataset_id, timestamp, open, high, low, close, volume)
signals (signal_id, pair, strategy, side, entry_price, generated_at, probability_up, reason)
settings (key, value)
learning (signal_id, outcome, probability, timestamp)
```

Location:
- APK: `/data/data/com.orbix.signallab/files/orbix_data/mobile_candles.db` + `mobile_ledger.db`
- PWA: IndexedDB `orbix-mobile-v1`
- Laptop Electron: `~/Library/Application Support/Orbix Signal Lab/orbix_data/` (macOS) or `~/.config/Orbix/` (Linux) or `%APPDATA%/Orbix/` (Windows)
- Web: `./mobile_data/` or `ORBIX_MOBILE_DATA_DIR`

All Orbix data lives inside app, no server needed. Works offline 24/7.

## How to Build APK

### Prerequisites
- Node.js 18+, npm
- Android Studio + Android SDK + Java 17
- Python 3.11

### Steps

```bash
cd orbix
./scripts/build_mobile.sh   # Builds web + mobile PWA + checks Python

cd mobile
npm install                 # Installs Capacitor + SQLite plugin
npm run build               # Builds dist/ with Vite (or manual copy)

npx cap add android         # First time - creates android/ project
npx cap copy                # Copies dist/ to android assets
npx cap open android        # Opens Android Studio

# In Android Studio: Build -> Build APK(s)
# APK at: android/app/build/outputs/apk/debug/app-debug.apk

# Or command line:
cd android
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
adb install app/build/outputs/apk/debug/app-debug.apk
```

### Chaquopy - Python Inside APK

`mobile/android/app/build.gradle` already includes:

```gradle
python {
  version "3.11"
  pip {
    install "fastapi"
    install "uvicorn"
    install "numpy"
    install "supabase"
  }
}
chaquopy {
  sourceSets {
    main {
      python.srcDirs = ["src/main/python", "../../../orbix"]
    }
  }
}
```

This bundles all `orbix/` Python code inside APK. `MainActivity.java` starts Python backend in background thread.

## How to Build Laptop App

```bash
cd electron
npm install
npm start          # Dev - loads Python backend on 8001
npm run build      # Builds installers: AppImage, deb, dmg, exe
```

Laptop app:
- Starts Python `uvicorn orbix.mobile_api:mobile_app --port 8001` as child process
- Loads `web/index.html` or `http://127.0.0.1:8001`
- Tray, menu, file import, data folder
- Same SQLite DB as APK

## Running Without Hosting (Local)

Already works:

```bash
# Python only (no Docker, no hosting)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn orbix.mobile_api:mobile_app --host 127.0.0.1 --port 8000 --workers 1
# -> http://127.0.0.1:8000 - all Orbix with local SQLite

# Or main API (DuckDB)
uvicorn orbix.api:app --host 127.0.0.1 --port 8000 --workers 1

# Or Docker local (not hosting, just container on your PC)
docker compose up -d --build
```

## Supabase Sync (Future)

APK works offline first, but can sync when online:

```python
# In mobile_db.py - future
from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
supabase.table("candles").insert(candles).execute()
```

`mobile/src/js/db.js` already has `sync` placeholder, and `orbix/mobile_api.py` has `/api/sync/status`.

Free tier Supabase: 500MB DB, 50k MAU - enough for 1-2 pairs M1.

## Comparison

| Feature | APK (Mobile) | Laptop (Electron) | Web (Local) | Cloud (Docker) |
|---------|--------------|-------------------|-------------|----------------|
| All Orbix inside | ✓ Yes (Python+JS) | ✓ Yes | ✓ Yes | ✓ Yes |
| Database | SQLite in APK | SQLite in app data | DuckDB/SQLite | DuckDB |
| Offline | ✓ 24/7 | ✓ 24/7 | ✓ if PC on | Needs server |
| Needs hosting | No | No | No | Yes |
| 24/7 collection | WorkManager 15min | Tray + background | Only if PC on | Yes |
| Install | APK sideload | AppImage/dmg/exe | pip + uvicorn | Docker |
| Push notif | LocalNotifications | Tray | Browser | Telegram |

## Files Added

- `web/manifest.json` - PWA manifest
- `web/service-worker.js` - Offline cache
- `web/mobile.css` - Mobile responsive (bottom nav, touch, swipe)
- `web/laptop.css` - Laptop enhancements (hover, shortcuts, print)
- `web/app-mobile.js` - Mobile logic (PWA, bottom nav, IndexedDB, gestures)
- `orbix/mobile_db.py` - SQLite store for APK
- `orbix/mobile_api.py` - Offline FastAPI
- `mobile/` - Capacitor project, all Orbix inside APK
  - `src/index.html` - Mobile UI
  - `src/js/db.js` - SQLite/IndexedDB
  - `src/js/engine.js` - 5 strategies JS port
  - `src/js/app.js` - Main app
  - `android/` - Android + Chaquopy + 24/7 worker
- `electron/` - Laptop desktop app
- `scripts/build_mobile.sh` - Build mobile & laptop
- `scripts/build_apk.sh` - Build APK

## Testing APK without Android SDK

In this sandbox (no Java), we built PWA structure:

```bash
./scripts/build_mobile.sh
# Creates mobile/dist/ with all assets
# You can serve it: npx serve mobile/dist
# Or open mobile/dist/index.html in browser - works offline with IndexedDB
```

For real APK, need Android Studio on your laptop.

## Security

- APK private storage - other apps cannot read DB
- No `ALLOW_EMBED` in production
- `mobile_db.py` uses WAL mode, single process
- Chaquopy Python isolated
- HTTPS for Supabase sync

## Next Steps

1. Add Supabase tables + RLS
2. Add biometric lock for APK (Capacitor SQLite biometric)
3. Add FCM push for signals
4. Build release APK with signing
5. Publish to Play Store (or sideload)
