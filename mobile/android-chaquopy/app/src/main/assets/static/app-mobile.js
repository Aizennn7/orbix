/**
 * Orbix Mobile & Laptop Interface Enhancements
 * Handles responsive navigation, PWA, offline DB, touch gestures
 */
(function() {
  'use strict';

  const isMobile = window.innerWidth <= 900;
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
  const isElectron = navigator.userAgent.includes('Electron');
  const isCapacitor = window.Capacitor !== undefined;

  // Add platform classes
  document.documentElement.classList.toggle('is-mobile', isMobile);
  document.documentElement.classList.toggle('is-standalone', isStandalone);
  document.documentElement.classList.toggle('is-electron', isElectron);
  document.documentElement.classList.toggle('is-capacitor', isCapacitor);

  // Register service worker for PWA
  if ('serviceWorker' in navigator && !isCapacitor) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/static/service-worker.js')
        .then(reg => console.log('[Orbix] SW registered', reg.scope))
        .catch(err => console.warn('[Orbix] SW failed', err));
    });
  }

  // Mobile Bottom Navigation
  function createMobileNav() {
    const existing = document.querySelector('.mobile-bottom-nav');
    if (existing) return;

    const nav = document.createElement('nav');
    nav.className = 'mobile-bottom-nav';
    nav.setAttribute('aria-label', 'Mobile navigation');
    nav.innerHTML = `
      <div class="mobile-bottom-nav-inner">
        <a href="#dashboard" class="mobile-nav-item active" data-route="dashboard">
          <span class="nav-icon">◉</span>
          <span class="nav-label">Dash</span>
        </a>
        <a href="#engine" class="mobile-nav-item" data-route="engine">
          <span class="nav-icon">⚡</span>
          <span class="nav-label">Engine</span>
        </a>
        <a href="#backtest" class="mobile-nav-item" data-route="backtest">
          <span class="nav-icon">📊</span>
          <span class="nav-label">Backtest</span>
        </a>
        <a href="#data" class="mobile-nav-item" data-route="data">
          <span class="nav-icon">🗄️</span>
          <span class="nav-label">Data</span>
        </a>
        <a href="#trader" class="mobile-nav-item" data-route="trader">
          <span class="nav-icon">💼</span>
          <span class="nav-label">Trader</span>
        </a>
        <a href="#paper" class="mobile-nav-item" data-route="paper">
          <span class="nav-icon">📝</span>
          <span class="nav-label">Paper</span>
        </a>
      </div>
    `;
    document.body.appendChild(nav);

    // Sync active state with top nav
    const observer = new MutationObserver(() => {
      const activeRoute = document.querySelector('.top-nav a.active')?.dataset.route;
      nav.querySelectorAll('.mobile-nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.route === activeRoute);
      });
    });
    const topNav = document.querySelector('.top-nav');
    if (topNav) observer.observe(topNav, {attributes: true, subtree: true, attributeFilter: ['class']});
  }

  // Hamburger Menu Drawer
  function createMobileDrawer() {
    if (document.querySelector('.mobile-drawer')) return;

    const drawer = document.createElement('div');
    drawer.className = 'mobile-drawer';
    drawer.innerHTML = `
      <div class="mobile-drawer-panel">
        <div class="mobile-drawer-header">
          <div class="brand">
            <span class="brand-mark">◉</span>
            <span class="brand-copy"><strong>ORBIX</strong><small>SIGNAL LAB</small></span>
          </div>
          <button class="mobile-drawer-close" aria-label="Close menu" style="width:32px;height:32px;border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:6px;">×</button>
        </div>
        <nav style="display:grid;gap:4px;">
          <a href="#dashboard" data-route="dashboard" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">◉ Dashboard</a>
          <a href="#engine" data-route="engine" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">⚡ Signal Engine</a>
          <a href="#backtest" data-route="backtest" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">📊 Backtest</a>
          <a href="#data" data-route="data" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">🗄️ Data</a>
          <a href="#connections" data-route="connections" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">🔌 Connections</a>
          <a href="#paper" data-route="paper" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">📝 Paper Log</a>
          <a href="#strategy-lab" data-route="strategy-lab" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">🧪 Strategy Lab</a>
          <a href="#trader" data-route="trader" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">💼 Trader</a>
          <a href="#telegram" data-route="telegram" style="padding:14px 12px;border-radius:8px;display:flex;align-items:center;gap:12px;">✈️ Telegram</a>
        </nav>
        <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--line);">
          <div style="font-size:8px;color:var(--dim);letter-spacing:.1em;margin-bottom:8px;">OFFLINE STATUS</div>
          <div id="mobileOfflineStatus" style="font-size:10px;color:var(--muted);">Checking...</div>
        </div>
      </div>
    `;
    document.body.appendChild(drawer);

    const toggle = document.createElement('button');
    toggle.className = 'mobile-menu-toggle';
    toggle.setAttribute('aria-label', 'Open menu');
    toggle.innerHTML = '<span></span><span></span><span></span>';
    const topbar = document.querySelector('.topbar');
    if (topbar) topbar.appendChild(toggle);

    function openDrawer() {
      drawer.classList.add('is-open');
      document.body.style.overflow = 'hidden';
    }
    function closeDrawer() {
      drawer.classList.remove('is-open');
      document.body.style.overflow = '';
    }

    toggle.addEventListener('click', openDrawer);
    drawer.querySelector('.mobile-drawer-close').addEventListener('click', closeDrawer);
    drawer.addEventListener('click', (e) => {
      if (e.target === drawer) closeDrawer();
    });
    drawer.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', closeDrawer);
    });
  }

  // Offline DB - IndexedDB fallback for mobile
  class OfflineDB {
    constructor() {
      this.dbName = 'orbix-mobile';
      this.version = 1;
      this.db = null;
    }

    async init() {
      return new Promise((resolve, reject) => {
        const req = indexedDB.open(this.dbName, this.version);
        req.onupgradeneeded = (e) => {
          const db = e.target.result;
          if (!db.objectStoreNames.contains('candles')) {
            const store = db.createObjectStore('candles', {keyPath: 'id'});
            store.createIndex('pair', 'pair', {unique: false});
            store.createIndex('timestamp', 'timestamp', {unique: false});
          }
          if (!db.objectStoreNames.contains('signals')) {
            const store = db.createObjectStore('signals', {keyPath: 'signal_id'});
            store.createIndex('pair', 'pair', {unique: false});
            store.createIndex('status', 'status', {unique: false});
          }
          if (!db.objectStoreNames.contains('paper_outcomes')) {
            db.createObjectStore('paper_outcomes', {keyPath: 'id'});
          }
          if (!db.objectStoreNames.contains('settings')) {
            db.createObjectStore('settings', {keyPath: 'key'});
          }
        };
        req.onsuccess = (e) => {
          this.db = e.target.result;
          resolve(this.db);
        };
        req.onerror = (e) => reject(e.target.error);
      });
    }

    async put(store, data) {
      if (!this.db) await this.init();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(store, 'readwrite');
        tx.objectStore(store).put(data);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    async getAll(store) {
      if (!this.db) await this.init();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(store, 'readonly');
        const req = tx.objectStore(store).getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
  }

  // Initialize offline DB
  const offlineDB = new OfflineDB();
  offlineDB.init().then(() => {
    console.log('[Orbix Mobile] IndexedDB ready');
    updateOfflineStatus();
  }).catch(err => console.warn('[Orbix Mobile] IndexedDB failed', err));

  function updateOfflineStatus() {
    const el = document.getElementById('mobileOfflineStatus');
    if (!el) return;
    const online = navigator.onLine;
    el.innerHTML = online 
      ? '<span style="color:#4ade80;">● Online — live sync active</span>' 
      : '<span style="color:#fbbf24;">● Offline — using local database</span><br><small>Signals saved locally, will sync when online</small>';
  }

  window.addEventListener('online', updateOfflineStatus);
  window.addEventListener('offline', updateOfflineStatus);

  // Touch gestures - swipe between pages
  let touchStartX = 0;
  let touchStartY = 0;
  document.addEventListener('touchstart', e => {
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, {passive: true});

  document.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 80) {
      // Horizontal swipe
      const routes = ['dashboard','engine','backtest','data','trader','paper'];
      const current = document.querySelector('.top-nav a.active')?.dataset.route || 'dashboard';
      const idx = routes.indexOf(current);
      if (dx < 0 && idx < routes.length - 1) {
        // Swipe left -> next
        document.querySelector(`[data-route="${routes[idx+1]}"]`)?.click();
      } else if (dx > 0 && idx > 0) {
        // Swipe right -> prev
        document.querySelector(`[data-route="${routes[idx-1]}"]`)?.click();
      }
    }
  }, {passive: true});

  // Laptop keyboard shortcuts
  if (!isMobile) {
    document.addEventListener('keydown', e => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.ctrlKey || e.metaKey) {
        const map = {'1':'dashboard','2':'engine','3':'backtest','4':'data','5':'trader'};
        if (map[e.key]) {
          e.preventDefault();
          document.querySelector(`[data-route="${map[e.key]}"]`)?.click();
        }
      }
    });

    // Show shortcuts hint
    const hint = document.createElement('div');
    hint.className = 'keyboard-shortcuts';
    hint.innerHTML = '<div style="margin-bottom:6px;font-weight:600;">SHORTCUTS</div><div><kbd>Ctrl+1</kbd> Dash <kbd>Ctrl+2</kbd> Engine <kbd>Ctrl+3</kbd> Backtest</div>';
    document.body.appendChild(hint);
    setTimeout(() => hint.style.opacity = '0.6', 1000);
  }

  // PWA Install prompt
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferredPrompt = e;
    const banner = document.createElement('div');
    banner.style.cssText = 'position:fixed;bottom:90px;left:12px;right:12px;z-index:80;padding:14px;background:linear-gradient(100deg,rgba(20,20,20,.98),rgba(10,10,10,.98));border:1px solid var(--line-bright);border-radius:12px;display:flex;align-items:center;justify-content:space-between;gap:12px;backdrop-filter:blur(16px);';
    banner.innerHTML = `
      <div><div style="font-size:10px;font-weight:600;letter-spacing:.08em;">INSTALL ORBIX</div><div style="font-size:8px;color:var(--muted);margin-top:2px;">Add to home screen for offline APK-like experience</div></div>
      <button id="pwaInstallBtn" style="padding:10px 16px;background:white;color:black;border:0;border-radius:8px;font-size:9px;font-weight:700;letter-spacing:.08em;">INSTALL</button>
      <button id="pwaDismissBtn" style="width:32px;height:32px;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:6px;">×</button>
    `;
    document.body.appendChild(banner);
    banner.querySelector('#pwaInstallBtn').addEventListener('click', async () => {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        const {outcome} = await deferredPrompt.userChoice;
        console.log('[PWA] Install outcome:', outcome);
        deferredPrompt = null;
      }
      banner.remove();
    });
    banner.querySelector('#pwaDismissBtn').addEventListener('click', () => banner.remove());
  });

  // Init on load
  window.addEventListener('DOMContentLoaded', () => {
    if (window.innerWidth <= 900) {
      createMobileNav();
      createMobileDrawer();
    }
    // Inject CSS
    const mobileCss = document.createElement('link');
    mobileCss.rel = 'stylesheet';
    mobileCss.href = '/static/mobile.css?v=1';
    document.head.appendChild(mobileCss);
    
    const laptopCss = document.createElement('link');
    laptopCss.rel = 'stylesheet';
    laptopCss.href = '/static/laptop.css?v=1';
    document.head.appendChild(laptopCss);

    // Manifest
    const manifest = document.createElement('link');
    manifest.rel = 'manifest';
    manifest.href = '/static/manifest.json';
    document.head.appendChild(manifest);

    updateOfflineStatus();
  });

  // Capacitor back button
  if (isCapacitor) {
    document.addEventListener('backbutton', () => {
      if (document.querySelector('.mobile-drawer.is-open')) {
        document.querySelector('.mobile-drawer').classList.remove('is-open');
      } else {
        window.history.back();
      }
    }, false);
  }

  // Expose for debugging
  window.OrbixMobile = {
    offlineDB,
    isMobile,
    isStandalone,
    isElectron,
    isCapacitor,
    updateOfflineStatus
  };

})();
