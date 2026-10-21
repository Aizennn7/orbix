# Orbix Mobile — APK with All Orbix Inside + Database

**All Orbix inside APK, offline SQLite, works 24/7 on your device**

## Quick Start

```bash
# Build mobile & laptop interfaces
./scripts/build_mobile.sh

# Build APK (needs Android Studio)
./scripts/build_apk.sh
```

## Structure

```
mobile/
├── src/
│   ├── index.html          # Mobile UI - bottom nav, touch optimized
│   ├── css/
│   │   ├── mobile-app.css  # All mobile styles
│   │   └── orbix-core.css  # Shared core
│   └── js/
│       ├── db.js           # SQLite (APK) / IndexedDB (PWA) - all tables inside APK
│       ├── engine.js       # 5 strategies JS port - OCG, ML, pullback, reversal, breakout
│       └── app.js          # Main app - offline first, 24/7
├── android/                # Android project
│   ├── app/build.gradle    # Chaquopy Python inside APK
│   └── app/src/main/java/com/orbix/signallab/
│       ├── MainActivity.java   # WebView + Python backend
│       ├── OrbixWorker.java    # 24/7 background collection
│       └── BootReceiver.java   # After reboot
├── dist/                   # Built PWA - all assets
├── package.json            # Capacitor config
└── capacitor.config.json
```

## Database Inside APK

**All Orbix data lives inside APK, no server**

- **SQLite** (native APK) via `@capacitor-community/sqlite`
- **IndexedDB** fallback for PWA/web
- Tables: `datasets`, `candles`, `signals`, `settings`, `learning`
- Location APK: `/data/data/com.orbix.signallab/files/orbix_data/`
- Works offline, syncs to Supabase when online (future)

## Mobile Interface

- Bottom nav (Dash, Engine, Paper, Data, Trader)
- Swipe left/right to switch pages
- 44px touch targets, haptic feedback
- Offline badge, PWA install prompt
- Chart + candle table + signal generation
- CSV import -> local SQLite
- Local notifications for signals

## Laptop Interface

In `electron/` - same DB, tray, background Python, file menu, keyboard shortcuts Ctrl+1..5

## Build APK

Needs: Node 18+, Android Studio, Java 17, Python 3.11

```bash
cd mobile
npm install
npm run build
npx cap add android   # first time
npx cap copy
npx cap open android  # Android Studio -> Build APK
# or
cd android && ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

APK contains:
- All web UI
- All Python (Chaquopy) - orbix/mobile_db.py, mobile_api.py, etc.
- SQLite DB
- 24/7 worker + boot receiver

## Python Inside APK (Chaquopy)

`android/app/build.gradle`:

```gradle
python {
  version "3.11"
  pip {
    install "fastapi"
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

## PWA Without APK

`mobile/dist/` works as PWA in browser:

```bash
npx serve mobile/dist
# Open http://localhost:3000
# Works offline with IndexedDB, installable
```

## Laptop

```bash
cd electron
npm install
npm start          # Dev
npm run build      # AppImage, deb, dmg, exe
```

## Supabase Sync (Future)

Local DB is primary, syncs when online. See `docs/MOBILE.md`
