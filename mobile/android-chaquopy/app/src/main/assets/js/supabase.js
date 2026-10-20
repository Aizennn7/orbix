/**
 * Orbix Supabase Sync - JS client for APK
 * Local SQLite is primary, Supabase syncs when online
 */
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

class SupabaseSync {
  constructor() {
    this.url = localStorage.getItem('supabase_url') || '';
    this.key = localStorage.getItem('supabase_key') || '';
    this.client = null;
    this.enabled = false;
    
    if (this.url && this.key) {
      this.init();
    }
  }

  init() {
    if (!this.url || !this.key) return false;
    try {
      this.client = createClient(this.url, this.key);
      this.enabled = true;
      console.log('[Orbix Supabase] Sync enabled');
      return true;
    } catch (e) {
      console.warn('[Orbix Supabase] Failed', e);
      this.enabled = false;
      return false;
    }
  }

  configure(url, key) {
    this.url = url;
    this.key = key;
    localStorage.setItem('supabase_url', url);
    localStorage.setItem('supabase_key', key);
    return this.init();
  }

  async syncCandles(candles) {
    if (!this.enabled || !candles.length) return 0;
    try {
      const { data, error } = await this.client.from('candles').upsert(candles, {onConflict: 'dataset_id,timestamp'});
      if (error) throw error;
      return data?.length || 0;
    } catch (e) {
      console.warn('[Supabase] Sync candles failed', e);
      return 0;
    }
  }

  async syncSignals(signals) {
    if (!this.enabled || !signals.length) return 0;
    try {
      const { data, error } = await this.client.from('signals').upsert(signals, {onConflict: 'signal_id'});
      if (error) throw error;
      return data?.length || 0;
    } catch (e) {
      console.warn('[Supabase] Sync signals failed', e);
      return 0;
    }
  }

  async fetchDatasets() {
    if (!this.enabled) return [];
    try {
      const { data, error } = await this.client.from('datasets').select('*').order('updated_at', {ascending: false});
      if (error) throw error;
      return data || [];
    } catch (e) {
      console.warn('[Supabase] Fetch failed', e);
      return [];
    }
  }

  isEnabled() {
    return this.enabled;
  }
}

export const supabaseSync = new SupabaseSync();
export default supabaseSync;
