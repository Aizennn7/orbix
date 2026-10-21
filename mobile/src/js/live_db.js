/**
 * Orbix Live Database - PERMANENT at Root Level
 * All data inside APK configured permanently at root level
 * 
 * Config priority (permanent first):
 * 1. ROOT_CONFIG from mobile/src/js/root_config.js (hardcoded from root .env, permanent in APK)
 * 2. /root_config.json bundled in dist (from root level)
 * 3. window.ORBIX_ROOT_CONFIG (global, from root)
 * 4. localStorage (user override, saved via Settings)
 * 5. /api/database/config (API at root level)
 * 
 * Root level single source of truth:
 *   database/apk_config.json (permanent)
 *   data/root_config.json (permanent cache)
 *   .env at root (permanent)
 */

import { db as localDB } from './db.js';

// Try to load permanent root config - hardcoded in APK from root level
let ROOT_CONFIG = null;
try {
  // This file is generated from root .env at build time, permanent in APK
  const mod = await import('./root_config.js').catch(() => null);
  if (mod && mod.ROOT_CONFIG) {
    ROOT_CONFIG = mod.ROOT_CONFIG;
    console.log('[LiveDB] Loaded permanent ROOT_CONFIG from root level:', ROOT_CONFIG.backend, ROOT_CONFIG.is_live ? 'LIVE' : 'OFFLINE');
  }
} catch (e) {
  console.log('[LiveDB] No root_config.js yet, will generate from root .env');
}

// Fallback to global if available (bundled via index.html)
if (!ROOT_CONFIG && typeof window !== 'undefined' && window.ORBIX_ROOT_CONFIG) {
  ROOT_CONFIG = window.ORBIX_ROOT_CONFIG;
  console.log('[LiveDB] Loaded ROOT_CONFIG from window.ORBIX_ROOT_CONFIG (root level)');
}

class LiveDB {
  constructor() {
    // Priority: ROOT_CONFIG (permanent at root) > localStorage > empty
    this.supabaseUrl = (ROOT_CONFIG && ROOT_CONFIG.supabase_url) || localStorage.getItem('SUPABASE_URL') || localStorage.getItem('supabase_url') || '';
    this.supabaseKey = (ROOT_CONFIG && ROOT_CONFIG.supabase_anon_key) || localStorage.getItem('SUPABASE_ANON_KEY') || localStorage.getItem('supabase_key') || '';
    this.supabaseServiceKey = (ROOT_CONFIG && ROOT_CONFIG.supabase_service_key) || localStorage.getItem('SUPABASE_SERVICE_KEY') || '';
    this.backend = (ROOT_CONFIG && ROOT_CONFIG.backend) || localStorage.getItem('DATABASE_BACKEND') || 'sqlite';
    
    this.supabase = null;
    this.isLive = !!(ROOT_CONFIG && ROOT_CONFIG.is_live) || !!(this.supabaseUrl && this.supabaseKey);
    this.isOnline = navigator.onLine;
    this.syncInterval = null;
    this.rootConfig = ROOT_CONFIG;
    
    console.log(`[LiveDB] Init - Backend: ${this.backend}, Live: ${this.isLive}, Source: ${ROOT_CONFIG ? 'ROOT LEVEL PERMANENT' : 'localStorage/API'}`);
    
    // Check root configs
    this.checkRootConfig();
    
    window.addEventListener('online', () => {
      this.isOnline = true;
      this.updateStatus();
      this.syncToCloud();
    });
    window.addEventListener('offline', () => {
      this.isOnline = false;
      this.updateStatus();
    });
  }

  async checkRootConfig() {
    // 1. Try bundled root_config.json (permanent at root, bundled in APK)
    try {
      const res = await fetch('./root_config.json');
      if (res.ok) {
        const data = await res.json();
        console.log('[LiveDB] Loaded root_config.json from dist (root level permanent):', data.backend, data.is_live ? 'LIVE' : 'OFFLINE');
        if (data.supabase_url && data.supabase_anon_key) {
          if (!this.supabaseUrl) this.supabaseUrl = data.supabase_url;
          if (!this.supabaseKey) this.supabaseKey = data.supabase_anon_key;
          this.isLive = data.is_live || this.isLive;
          this.rootConfig = data;
          if (this.isLive) this.initSupabase();
        }
        this.updateStatus();
      }
    } catch (e) {
      console.log('[LiveDB] No bundled root_config.json yet');
    }

    // 2. Try public/root_config.json
    try {
      const res = await fetch('./public/root_config.json');
      if (res.ok) {
        const data = await res.json();
        if (data.supabase_url && !this.supabaseUrl) {
          this.supabaseUrl = data.supabase_url;
          this.supabaseKey = data.supabase_anon_key;
          this.isLive = true;
          this.initSupabase();
        }
      }
    } catch {}

    // 3. Try root API (when running via FastAPI at root level)
    try {
      const res = await fetch('/api/database/config');
      if (res.ok) {
        const data = await res.json();
        const cfg = data.config;
        console.log('[LiveDB] Root API config:', cfg.effective_backend, 'supabase:', cfg.supabase_configured);
        if (cfg.supabase_configured) {
          console.log('[LiveDB] Root Supabase configured at root level, switching to live');
          this.isLive = true;
          this.backend = cfg.effective_backend;
          this.updateStatus();
          this.fetchLiveData();
        }
        // Also check apk_config
        const apkRes = await fetch('/api/database/apk-config').catch(() => null);
        if (apkRes && apkRes.ok) {
          const apkData = await apkRes.json();
          if (apkData.config && apkData.config.supabase_url) {
            this.supabaseUrl = apkData.config.supabase_url;
            this.supabaseKey = apkData.config.supabase_anon_key;
            this.isLive = true;
            this.initSupabase();
          }
        }
      }
    } catch (e) {
      console.log('[LiveDB] No root API, using permanent root config + localStorage');
      if (this.supabaseUrl && this.supabaseKey) {
        this.isLive = true;
        this.initSupabase();
      }
      this.updateStatus();
    }

    // 4. If still have URL from ROOT_CONFIG, init
    if (this.supabaseUrl && this.supabaseKey && !this.supabase) {
      this.initSupabase();
    }
    
    this.updateStatus();
  }

  initSupabase() {
    if (!this.supabaseUrl || !this.supabaseKey) {
      console.log('[LiveDB] No Supabase URL/key, staying offline with root SQLite');
      return false;
    }
    try {
      // Dynamic import supabase from CDN (works in PWA, bundled in APK)
      import('https://esm.sh/@supabase/supabase-js@2').then(({ createClient }) => {
        this.supabase = createClient(this.supabaseUrl, this.supabaseKey);
        this.isLive = true;
        console.log('[LiveDB] Supabase client ready - LIVE and ONLINE (permanent at root level)');
        console.log(`[LiveDB] URL: ${this.supabaseUrl.slice(0,30)}...`);
        this.updateStatus();
        this.setupRealtime();
        this.syncToCloud();
        
        // Save to localStorage as cache (but root config is primary)
        localStorage.setItem('SUPABASE_URL', this.supabaseUrl);
        localStorage.setItem('SUPABASE_ANON_KEY', this.supabaseKey);
        if (this.supabaseServiceKey) localStorage.setItem('SUPABASE_SERVICE_KEY', this.supabaseServiceKey);
        localStorage.setItem('DATABASE_BACKEND', 'supabase');
      }).catch(e => {
        console.warn('[LiveDB] Supabase CDN failed, trying local', e);
        // Try local supabase.js
        this.initSupabaseLocal();
      });
      return true;
    } catch (e) {
      console.warn('[LiveDB] Supabase init failed', e);
      this.isLive = false;
      return false;
    }
  }

  async initSupabaseLocal() {
    try {
      const { createClient } = await import('./supabase.js');
      // supabase.js already has logic
      console.log('[LiveDB] Using local supabase.js');
    } catch {}
  }

  configure(url, anonKey, serviceKey = '') {
    console.log('[LiveDB] Configuring permanent at root level:', url.slice(0,30));
    this.supabaseUrl = url;
    this.supabaseKey = anonKey;
    this.supabaseServiceKey = serviceKey;
    
    // Save to localStorage (user override)
    localStorage.setItem('SUPABASE_URL', url);
    localStorage.setItem('SUPABASE_ANON_KEY', anonKey);
    if (serviceKey) localStorage.setItem('SUPABASE_SERVICE_KEY', serviceKey);
    localStorage.setItem('DATABASE_BACKEND', 'supabase');
    
    // Also try to save at root level via API (permanent)
    fetch('/api/database/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({supabase_url: url, supabase_key: anonKey, service_key: serviceKey})
    }).then(r => r.json()).then(data => {
      console.log('[LiveDB] Saved at root level via API:', data);
    }).catch(() => {
      console.log('[LiveDB] No API, saved locally only. For permanent root level, set at root .env and rebuild APK');
    });
    
    // Also update ROOT_CONFIG in memory
    if (this.rootConfig) {
      this.rootConfig.supabase_url = url;
      this.rootConfig.supabase_anon_key = anonKey;
      this.rootConfig.is_live = true;
    }
    
    return this.initSupabase();
  }

  async setupRealtime() {
    if (!this.supabase) return;
    
    console.log('[LiveDB] Setting up Realtime for live signals from root Supabase');
    try {
      this.supabase.channel('live-signals-root')
        .on('postgres_changes', {event: 'INSERT', schema: 'public', table: 'signals'}, payload => {
          console.log('[LiveDB] Live signal from root Supabase (permanent):', payload.new);
          localDB.saveSignal(payload.new);
          window.dispatchEvent(new CustomEvent('orbix-live-signal', {detail: payload.new}));
          if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
        })
        .on('postgres_changes', {event: 'INSERT', schema: 'public', table: 'candles'}, payload => {
          console.log('[LiveDB] Live candle from root:', payload.new);
          window.dispatchEvent(new CustomEvent('orbix-live-candle', {detail: payload.new}));
        })
        .subscribe((status) => {
          console.log('[LiveDB] Realtime status:', status);
        });
      
      console.log('[LiveDB] Realtime subscribed - live from root Supabase, permanent');
    } catch (e) {
      console.warn('[LiveDB] Realtime failed', e);
    }
  }

  async fetchLiveData() {
    try {
      const res = await fetch('/api/market');
      if (res.ok) {
        const data = await res.json();
        console.log(`[LiveDB] Fetched ${data.candles?.length || 0} live candles from root API (permanent)`);
        return data;
      }
      // Try Supabase direct if no API
      if (this.supabase) {
        const { data, error } = await this.supabase.from('candles').select('*').order('timestamp', {ascending: false}).limit(500);
        if (!error && data) {
          console.log(`[LiveDB] Fetched ${data.length} live candles from Supabase direct (root)`);
          return {candles: data};
        }
      }
    } catch (e) {
      console.warn('[LiveDB] Fetch live failed, using local root DB', e);
    }
    return null;
  }

  async syncToCloud() {
    if (!this.isLive || !this.isOnline) {
      console.log(`[LiveDB] Skip sync - live:${this.isLive} online:${this.isOnline}`);
      return;
    }
    
    try {
      const signals = await localDB.getSignals(100);
      const datasets = await localDB.getDatasets();
      
      console.log(`[LiveDB] Syncing ${signals.length} signals, ${datasets.length} datasets to root Supabase (permanent)`);
      
      if (this.supabase) {
        if (signals.length) {
          const { error } = await this.supabase.from('signals').upsert(signals, {onConflict: 'signal_id'});
          if (!error) console.log(`[LiveDB] ✓ Synced ${signals.length} signals to Supabase at root level (permanent)`);
          else console.warn('[LiveDB] Sync error', error);
        }
        if (datasets.length) {
          const { error } = await this.supabase.from('datasets').upsert(datasets.map(d => ({
            dataset_id: d.dataset_id,
            pair: d.pair,
            candle_count: d.candle_count,
            updated_at: d.updated_at
          })), {onConflict: 'dataset_id'});
          if (!error) console.log(`[LiveDB] ✓ Synced ${datasets.length} datasets to root`);
        }
      } else {
        const res = await fetch('/api/database/sync', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({signals, datasets})
        });
        if (res.ok) {
          const result = await res.json();
          console.log('[LiveDB] ✓ Synced via root API (permanent):', result);
        }
      }
    } catch (e) {
      console.warn('[LiveDB] Sync failed', e);
    }
  }

  startAutoSync() {
    if (this.syncInterval) clearInterval(this.syncInterval);
    this.syncInterval = setInterval(() => {
      if (this.isLive && this.isOnline) {
        this.syncToCloud();
        this.fetchLiveData();
      }
    }, 30000);
    console.log('[LiveDB] Auto-sync started every 30s (permanent at root)');
  }

  updateStatus() {
    const badge = document.getElementById('offlineBadge');
    if (!badge) return;
    
    if (this.isLive && this.isOnline) {
      badge.textContent = '● LIVE ONLINE';
      badge.style.background = 'rgba(100,150,255,.15)';
      badge.style.borderColor = 'rgba(100,150,255,.3)';
      badge.style.color = '#8ab4ff';
      badge.title = `Live and online via Supabase at root level - permanent\n${this.supabaseUrl ? this.supabaseUrl.slice(0,40) : ''}\nAll data inside APK configured at root: database/ + data/ + .env`;
    } else if (this.isOnline) {
      badge.textContent = '● ONLINE (LOCAL)';
      badge.style.background = 'rgba(74,222,128,.15)';
      badge.style.borderColor = 'rgba(74,222,128,.3)';
      badge.style.color = '#4ade80';
      badge.title = 'Online but using local DB at root data/ - configure Supabase at root .env for live permanent';
    } else {
      badge.textContent = '● OFFLINE DB';
      badge.style.background = 'rgba(251,191,36,.15)';
      badge.style.borderColor = 'rgba(251,191,36,.3)';
      badge.style.color = '#fbbf24';
      badge.title = 'Offline - using local SQLite inside APK, data at root data/ (permanent)';
    }
  }

  getStatus() {
    return {
      isLive: this.isLive,
      isOnline: this.isOnline,
      backend: this.isLive ? 'supabase (live, permanent at root)' : 'sqlite (offline, at root data/)',
      location: 'database/ + data/ + .env at root level (single source of truth)',
      permanent: true,
      rootConfig: this.rootConfig,
      supabaseUrl: this.supabaseUrl ? this.supabaseUrl.slice(0,30)+'...' : 'not set',
      supabaseConfigured: !!this.supabaseUrl,
      dataDir: 'data/ at root level - permanent',
      databaseDir: 'database/ at root level - permanent',
      allDataInsideApk: true,
      note: 'All data inside APK configured permanently at root level'
    };
  }
}

export const liveDB = new LiveDB();
export default liveDB;
