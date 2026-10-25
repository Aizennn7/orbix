/**
 * Orbix Live Database - optional Supabase sync for the mobile PWA.
 *
 * Configuration is resolved at runtime, never baked into the bundle:
 *   1. localStorage (an explicit per-session override set in Settings)
 *   2. /api/database/config (the server's own .env)
 *
 * Only the Supabase anon key is ever handed to a client. The service_role key
 * stays on the server, so no credential can leak through a published build.
 */

import { db as localDB } from './db.js';

class LiveDB {
  constructor() {
    // A per-session override only. The authoritative config is the server's .env,
    // read through /api/database/config in checkRootConfig().
    this.supabaseUrl = localStorage.getItem('SUPABASE_URL') || '';
    this.supabaseKey = localStorage.getItem('SUPABASE_ANON_KEY') || '';
    this.backend = localStorage.getItem('DATABASE_BACKEND') || 'sqlite';

    this.supabase = null;
    this.isLive = !!(this.supabaseUrl && this.supabaseKey);
    this.isOnline = navigator.onLine;
    this.syncInterval = null;

    console.log(`[LiveDB] Init - Backend: ${this.backend}, Live: ${this.isLive}`);

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
    // Ask the server for its configured backend.
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
      }
    } catch (e) {
      console.log('[LiveDB] No API reachable, falling back to the localStorage override');
      if (this.supabaseUrl && this.supabaseKey) {
        this.isLive = true;
        this.initSupabase();
      }
      this.updateStatus();
    }

    // Init if an override was supplied.
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
      // Dynamic import supabase from CDN
      import('https://esm.sh/@supabase/supabase-js@2').then(({ createClient }) => {
        this.supabase = createClient(this.supabaseUrl, this.supabaseKey);
        this.isLive = true;
        console.log('[LiveDB] Supabase client ready - live');
        console.log(`[LiveDB] URL: ${this.supabaseUrl.slice(0,30)}...`);
        this.updateStatus();
        this.setupRealtime();
        this.syncToCloud();
        
        // Cache for the next page load; the server config stays authoritative.
        localStorage.setItem('SUPABASE_URL', this.supabaseUrl);
        localStorage.setItem('SUPABASE_ANON_KEY', this.supabaseKey);
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

  // Only the anon key is accepted here. The service_role key bypasses RLS and
  // must never be entered into, stored by, or sent from a browser client.
  configure(url, anonKey) {
    console.log('[LiveDB] Configuring session override:', url.slice(0, 30));
    this.supabaseUrl = url;
    this.supabaseKey = anonKey;

    localStorage.setItem('SUPABASE_URL', url);
    localStorage.setItem('SUPABASE_ANON_KEY', anonKey);
    localStorage.setItem('DATABASE_BACKEND', 'supabase');
    localStorage.removeItem('SUPABASE_SERVICE_KEY');

    fetch('/api/database/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({supabase_url: url, supabase_key: anonKey})
    }).then(r => r.json()).then(data => {
      console.log('[LiveDB] Saved via API:', data);
    }).catch(() => {
      console.log('[LiveDB] No API reachable, saved locally only. Set SUPABASE_URL in the server .env to enable sync.');
    });
    
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
      badge.title = `Live via Supabase\n${this.supabaseUrl ? this.supabaseUrl.slice(0,40) : ''}`;
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
      badge.title = 'Offline - using local browser storage';
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
      dataDir: 'data/ at root level',
      databaseDir: 'database/ at root level',
      note: 'Configuration is resolved from the server at runtime'
    };
  }
}

export const liveDB = new LiveDB();
export default liveDB;
