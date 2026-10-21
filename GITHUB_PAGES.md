# Orbix on GitHub Pages - Live Online at Root Level

**Yes, we can run GitHub Pages!** All data inside PWA configured permanently at root level, live online via Supabase, no Python hosting needed.

---

## GitHub Pages - Live Online PWA

**URL:** https://Aizennn7.github.io/orbix/ (after enabling Pages)

**How it works (permanent at root):**

- **Static hosting:** GitHub Pages serves static files from `mobile/dist/` + `database/` + `data/` at root level
- **No Python backend needed:** PWA uses Supabase JS directly + IndexedDB offline
- **Live online:** `mobile/src/js/root_config.js` hardcoded with Supabase URL from root `.env` (permanent at root), bundled into PWA
- **All data at root:** `database/apk_config.json` + `data/root_config.json` + `mobile/src/js/root_config.js` at root level, permanent, committed to GitHub
- **Offline fallback:** IndexedDB + CapacitorSQLite when offline, syncs to Supabase when online

---

## Setup GitHub Pages (2 mins)

### 1. Enable Pages in Repo Settings

1. Go to https://github.com/Aizennn7/orbix/settings/pages
2. **Build and deployment** -> Source: **GitHub Actions**
3. Save

### 2. Configure Supabase at Root Level (Permanent)

In Codespace, Termux, or local:

```bash
# Clone repo
git clone https://github.com/Aizennn7/orbix.git
cd orbix
git checkout arena/6260349f-orbix

# Configure Supabase permanently at root level
python scripts/configure_root_apk.py --supabase
# Paste URL + anon key from supabase.com Project Settings -> API
# Saves permanently at:
# - .env at root
# - database/apk_config.json at root (committed)
# - mobile/src/js/root_config.js hardcoded (committed, permanent in PWA)

# Verify
cat database/apk_config.json
cat mobile/src/js/root_config.js
python scripts/configure_root_apk.py --check  # should show LIVE ONLINE
```

### 3. Push -> GitHub Actions Deploys PWA to Pages

```bash
git add database/apk_config.json mobile/src/js/root_config.js
git commit -m "feat: live config for Pages at root permanent"
git push origin arena/6260349f-orbix

# GitHub Actions:
# - Build PWA with permanent root config (VITE_BASE=/orbix/ for Pages)
# - Deploy to GitHub Pages
# - URL: https://Aizennn7.github.io/orbix/
```

**Workflows:**

- `deploy-pwa-pages.yml` - Builds PWA with `VITE_BASE=/orbix/` and deploys to Pages (live online, permanent at root)
- `pages.yml` - Deploys static snapshot `docs/` (read-only, old)
- `build-apk.yml` - Builds APK with permanent root config (11.9 MB, live online)

Only one Pages deployment active at a time - last workflow wins. `deploy-pwa-pages.yml` deploys PWA with live config.

### 4. Access PWA on GitHub Pages

- **URL:** https://Aizennn7.github.io/orbix/
- **PWA:** Should show `● LIVE ONLINE` blue badge if Supabase configured at root before build
- **Offline:** Works offline via IndexedDB, syncs to Supabase when online
- **All data at root:** `database/` + `data/` + `.env` at root level, permanent

---

## Workflows Explained

### `deploy-pwa-pages.yml` - GitHub Pages PWA Live

```yaml
on: push to main, arena/6260349f-orbix
- Install Python + Node at root
- python scripts/configure_root_apk.py --build (generates permanent root configs at root)
- Build PWA with VITE_BASE=/orbix/ for Pages
- Copy root configs into dist-pages: root_config.json, apk_config.json, js/root_config.js
- Deploy _site/ to GitHub Pages
- URL: https://Aizennn7.github.io/orbix/
```

**Root level permanent files bundled into Pages:**

- `mobile/dist-pages/root_config.json` from `mobile/src/root_config.json` at root
- `mobile/dist-pages/apk_config.json` from `database/apk_config.json` at root (permanent)
- `mobile/dist-pages/assets/root_config.js` hardcoded from root `.env`
- `mobile/dist-pages/data/mobile/` initial data from `data/mobile/` at root

**PWA reads:**

1. `ROOT_CONFIG` from `root_config.js` hardcoded (permanent at root)
2. `root_config.json` bundled
3. `localStorage` (user override)
4. No API needed - Supabase JS direct

### `pages.yml` - Static Snapshot (Old)

- Deploys `docs/` static snapshot (read-only, no live)
- For main branch
- Not live, just snapshot

### `build-apk.yml` - APK Live

- Builds mobile PWA + APK with permanent root config
- Artifacts: `orbix-mobile-pwa` + `orbix-apk-debug` (11.9 MB)
- APK contains permanent root config, live online 24/7

---

## Local Test for Pages

```bash
# Build PWA for Pages locally (base /orbix/)
cd mobile
VITE_BASE=/orbix/ npm run build
# Or with custom config:
cat > vite.config.pages.js << 'EOF'
import { defineConfig } from 'vite';
import { resolve } from 'path';
export default defineConfig({
  root: 'src',
  base: '/orbix/',
  publicDir: resolve(__dirname, '../public'),
  build: {
    outDir: '../dist-pages',
    emptyOutDir: true,
    rollupOptions: { input: { main: resolve(__dirname, 'src/index.html') } }
  }
});
EOF
npx vite build --config vite.config.pages.js
ls dist-pages/
# Serve:
npx vite preview --host 0.0.0.0 --port 3000 --outDir dist-pages
# Open http://localhost:3000/orbix/
```

---

## Termux + GitHub Pages

In Termux, you can both run live 24/7 AND deploy to Pages:

```bash
# In Termux:
cd ~/orbix
./scripts/termux_setup.sh
python scripts/configure_root_apk.py --supabase  # permanent at root
./scripts/build_mobile.sh
git add database/apk_config.json mobile/src/js/root_config.js
git commit -m "feat: live from Termux for Pages"
git push origin arena/6260349f-orbix
# GitHub Actions deploys to Pages -> https://Aizennn7.github.io/orbix/ live online
# And Termux runs live 24/7 at http://localhost:8000
```

---

## Troubleshooting Pages

- **Pages not updating?** Check Actions tab, ensure `deploy-pwa-pages.yml` succeeded, last deployment wins
- **404 on assets?** Ensure `VITE_BASE=/orbix/` set in workflow, assets at `/orbix/assets/`
- **Still offline badge?** Check `database/apk_config.json` has `supabase_url`, rebuild, push, wait for Pages deploy
- **PWA not live?** Open DevTools -> Console -> should show `[LiveDB] Loaded permanent ROOT_CONFIG from root level: supabase LIVE`
- **Want custom domain?** Set in Pages settings, update `VITE_BASE=/` in workflow

---

## Summary

- **Yes, GitHub Pages works!** No hosting needed, free, live online via Supabase at root
- **Root level permanent:** `database/apk_config.json` + `mobile/src/js/root_config.js` + `data/root_config.json` at root, committed, bundled into PWA
- **PWA live:** Reads from `ROOT_CONFIG` hardcoded (permanent), Supabase JS direct, IndexedDB offline
- **Deploy:** `git push` -> `deploy-pwa-pages.yml` builds PWA with `VITE_BASE=/orbix/` and deploys to https://Aizennn7.github.io/orbix/
- **Termux:** Same root configs, run 24/7 in Termux + deploy to Pages from Termux
- **APK:** Same root configs, GitHub Actions builds APK 11.9 MB live online
- **All files in GitHub**, root level permanent, all data inside PWA/APK at root, works everywhere: Pages, Termux, APK, Codespace.
