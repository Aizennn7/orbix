/**
 * Orbix Root Config - PERMANENT at Root Level
 * Generated from root .env - bundled into APK permanently
 * All data inside APK configured at root level: database/ + data/ + .env
 * Generated at: 2026-10-21T05:40:01.377860
 * Location: database/apk_config.json at root (single source of truth)
 */

// Permanent root level config - hardcoded in APK, not localStorage
export const ROOT_CONFIG = {
  generated_at: "2026-10-21T05:40:01.377862",
  location: "ROOT LEVEL - database/ + data/ + .env",
  backend: "sqlite",
  DATABASE_BACKEND: "sqlite",
  is_live: false,
  live: false,
  supabase_url: "",
  SUPABASE_URL: "",
  supabase_anon_key: "",
  SUPABASE_ANON_KEY: "",
  supabase_service_key: "",
  SUPABASE_SERVICE_KEY: "",
  supabase_configured: false,
  data_dir: "./data at root",
  database_dir: "./database at root",
  root_level: true,
  note: "All data inside APK configured permanently at root level"
};

// For backward compatibility
export const SUPABASE_URL = ROOT_CONFIG.supabase_url;
export const SUPABASE_ANON_KEY = ROOT_CONFIG.supabase_anon_key;
export const DATABASE_BACKEND = ROOT_CONFIG.backend;
export const IS_LIVE = ROOT_CONFIG.is_live;

// Make available globally for APK
if (typeof window !== 'undefined') {
  window.ORBIX_ROOT_CONFIG = ROOT_CONFIG;
  window.ORBIX_IS_LIVE = ROOT_CONFIG.is_live;
  console.log('[Orbix Root] Permanent config from root level:', ROOT_CONFIG.backend, ROOT_CONFIG.is_live ? 'LIVE ONLINE' : 'OFFLINE');
}

export default ROOT_CONFIG;
