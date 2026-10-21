/**
 * Orbix Live Database - Supabase at Root Level
 * Makes offline DB live and online when Supabase configured at root .env
 * All config at root level: database/ + data/ + .env
 */

import { db as localDB } from './db.js';

class LiveDB {
  constructor() {
    this.supabaseUrl = localStorage.getItem('SUPABASE_URL') || '';
    this.supabaseKey = localStorage.getItem('SUPABASE_ANON_KEY') || '';
    this.supabase = null;
    this.isLive = false;
    this.isOnline = navigator.onLine;
    this.syncInterval = null;
    
    // Check if live config exists at root level via API
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
    try {
      // Try to get root database config from API (when running via FastAPI)
      const res = await fetch('/api/database/config');
      if (res.ok) {
        const data = await res.json();
        const cfg = data.config;
        if (cfg.supabase_configured) {
          console.log('[LiveDB] Root Supabase configured, switching to live');
          this.isLive = true;
          this.updateStatus();
          // If API has supabase, backend is live, we can fetch live data
          this.fetchLiveData();
        } else {
          console.log('[LiveDB] Root DB offline, using local SQLite');
          this.isLive = false;
          this.updateStatus();
        }
      }
    } catch (e) {
      console.log('[LiveDB] No root API, checking localStorage for Supabase');
      // Check localStorage for manual Supabase config
      if (this.supabaseUrl && this.supabaseKey) {
        this.isLive = true;
        this.initSupabase();
      }
      this.updateStatus();
    }
  }

  initSupabase() {
    if (!this.supabaseUrl || !this.supabaseKey) return false;
    try {
      // Dynamic import supabase from CDN (works in PWA)
      import('https://esm.sh/@supabase/supabase-js@2').then(({ createClient }) => {
        this.supabase = createClient(this.supabaseUrl, this.supabaseKey);
        this.isLive = true;
        console.log('[LiveDB] Supabase client ready - LIVE and ONLINE');
        this.updateStatus();
        this.setupRealtime();
        this.syncToCloud();
      });
      return true;
    } catch (e) {
      console.warn('[LiveDB] Supabase init failed', e);
      this.isLive = false;
      return false;
    }
  }

  configure(url, anonKey, serviceKey = '') {
    this.supabaseUrl = url;
    this.supabaseKey = anonKey;
    localStorage.setItem('SUPABASE_URL', url);
    localStorage.setItem('SUPABASE_ANON_KEY', anonKey);
    if (serviceKey) localStorage.setItem('SUPABASE_SERVICE_KEY', serviceKey);
    
    // Also set at root level via API if possible
    fetch('/api/database/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({supabase_url: url, supabase_key: anonKey})
    }).catch(() => {});
    
    return this.initSupabase();
  }

  async setupRealtime() {
    if (!this.supabase) return;
    
    // Subscribe to live signals from Supabase (root level)
    this.supabase.channel('live-signals')
      .on('postgres_changes', {event: 'INSERT', schema: 'public', table: 'signals'}, payload => {
        console.log('[LiveDB] Live signal from Supabase:', payload.new);
        // Save to local DB as well
        localDB.saveSignal(payload.new);
        // Notify UI
        window.dispatchEvent(new CustomEvent('orbix-live-signal', {detail: payload.new}));
        // Haptic + notification
        if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
      })
      .subscribe();
    
    console.log('[LiveDB] Realtime subscribed - live signals from root Supabase');
  }

  async fetchLiveData() {
    try {
      // Fetch live candles from root API (which uses Supabase at root if configured)
      const res = await fetch('/api/market');
      if (res.ok) {
        const data = await res.json();
        console.log(`[LiveDB] Fetched ${data.candles?.length || 0} live candles from root API`);
        return data;
      }
    } catch (e) {
      console.warn('[LiveDB] Fetch live failed, using local', e);
    }
    return null;
  }

  async syncToCloud() {
    if (!this.isLive || !this.isOnline) return;
    
    try {
      const signals = await localDB.getSignals(100);
      const datasets = await localDB.getDatasets();
      
      // If we have Supabase client, push
      if (this.supabase) {
        if (signals.length) {
          const { error } = await this.supabase.from('signals').upsert(signals, {onConflict: 'signal_id'});
          if (!error) console.log(`[LiveDB] Synced ${signals.length} signals to Supabase at root`);
        }
      } else {
        // Try via root API sync endpoint
        const res = await fetch('/api/database/sync', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({signals, datasets})
        });
        if (res.ok) console.log('[LiveDB] Synced via root API');
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
    }, 30000); // Every 30s
  }

  updateStatus() {
    const badge = document.getElementById('offlineBadge');
    if (!badge) return;
    
    if (this.isLive && this.isOnline) {
      badge.textContent = '● LIVE ONLINE';
      badge.style.background = 'rgba(100,150,255,.15)';
      badge.style.borderColor = 'rgba(100,150,255,.3)';
      badge.style.color = '#8ab4ff';
      badge.title = 'Live and online via Supabase at root level';
    } else if (this.isOnline) {
      badge.textContent = '● ONLINE (LOCAL)';
      badge.style.background = 'rgba(74,222,128,.15)';
      badge.style.borderColor = 'rgba(74,222,128,.3)';
      badge.style.color = '#4ade80';
      badge.title = 'Online but using local DB, configure Supabase at root .env for live';
    } else {
      badge.textContent = '● OFFLINE DB';
      badge.style.background = 'rgba(251,191,36,.15)';
      badge.style.borderColor = 'rgba(251,191,36,.3)';
      badge.style.color = '#fbbf24';
      badge.title = 'Offline - using local SQLite inside APK';
    }
  }

  getStatus() {
    return {
      isLive: this.isLive,
      isOnline: this.isOnline,
      backend: this.isLive ? 'supabase (live)' : 'sqlite (offline)',
      location: 'database/ at root level',
      supabaseConfigured: !!this.supabaseUrl,
      dataDir: 'data/ at root'
    };
  }
}

export const liveDB = new LiveDB();
export default liveDB;
