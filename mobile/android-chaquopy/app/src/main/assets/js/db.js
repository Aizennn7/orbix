/**
 * Orbix Mobile Database - SQLite for APK + IndexedDB fallback
 * All Orbix data lives inside APK, offline-first, syncs to Supabase when online
 */
import { Capacitor } from '@capacitor/core';
import { CapacitorSQLite, SQLiteConnection, SQLiteDBConnection } from '@capacitor-community/sqlite';

const isNative = Capacitor.isNativePlatform();
let sqliteConn = null;
let dbConn = null;
let useIndexedDB = false;

// IndexedDB fallback for web/PWA
class IndexedDBWrapper {
  constructor() {
    this.dbName = 'orbix-mobile-v1';
    this.version = 2;
    this.db = null;
  }

  async init() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(this.dbName, this.version);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains('candles')) {
          const s = db.createObjectStore('candles', {keyPath: 'id', autoIncrement: true});
          s.createIndex('dataset_id', 'dataset_id');
          s.createIndex('timestamp', 'timestamp');
        }
        if (!db.objectStoreNames.contains('datasets')) {
          db.createObjectStore('datasets', {keyPath: 'dataset_id'});
        }
        if (!db.objectStoreNames.contains('signals')) {
          const s = db.createObjectStore('signals', {keyPath: 'signal_id'});
          s.createIndex('generated_at', 'generated_at');
        }
        if (!db.objectStoreNames.contains('settings')) {
          db.createObjectStore('settings', {keyPath: 'key'});
        }
      };
      req.onsuccess = (e) => { this.db = e.target.result; resolve(this.db); };
      req.onerror = (e) => reject(e.target.error);
    });
  }

  async query(store, mode = 'readonly') {
    if (!this.db) await this.init();
    return this.db.transaction(store, mode).objectStore(store);
  }

  async put(store, data) {
    const objStore = await this.query(store, 'readwrite');
    return new Promise((res, rej) => {
      const req = objStore.put(data);
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  }

  async getAll(store) {
    const objStore = await this.query(store, 'readonly');
    return new Promise((res, rej) => {
      const req = objStore.getAll();
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  }

  async clear(store) {
    const objStore = await this.query(store, 'readwrite');
    return new Promise((res, rej) => {
      const req = objStore.clear();
      req.onsuccess = () => res();
      req.onerror = () => rej(req.error);
    });
  }
}

const idb = new IndexedDBWrapper();

// SQLite for native APK
async function initSQLite() {
  if (!isNative) {
    useIndexedDB = true;
    await idb.init();
    console.log('[Orbix DB] Using IndexedDB (web)');
    return;
  }

  try {
    sqliteConn = new SQLiteConnection(CapacitorSQLite);
    const ret = await sqliteConn.checkConnectionsConsistency();
    const isConn = (await sqliteConn.isConnection('orbix', false)).result;
    if (ret.result && isConn) {
      await sqliteConn.retrieveConnection('orbix', false);
    }

    dbConn = await sqliteConn.createConnection('orbix', false, 'no-encryption', 1, false);
    await dbConn.open();

    // Create tables - all Orbix inside APK
    const schema = `
      CREATE TABLE IF NOT EXISTS datasets (
        dataset_id TEXT PRIMARY KEY,
        pair TEXT NOT NULL,
        timeframe TEXT DEFAULT 'M1',
        candle_count INTEGER DEFAULT 0,
        first_timestamp TEXT,
        latest_timestamp TEXT,
        created_at TEXT,
        updated_at TEXT,
        source TEXT DEFAULT 'mobile-apk'
      );
      CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL DEFAULT 0,
        UNIQUE(dataset_id, timestamp)
      );
      CREATE INDEX IF NOT EXISTS idx_candles_ds_time ON candles(dataset_id, timestamp);
      CREATE TABLE IF NOT EXISTS signals (
        signal_id TEXT PRIMARY KEY,
        pair TEXT NOT NULL,
        strategy TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        generated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        probability_up REAL,
        reason TEXT,
        status TEXT DEFAULT 'PENDING',
        outcome TEXT,
        created_at TEXT
      );
      CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT
      );
      CREATE TABLE IF NOT EXISTS learning (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id TEXT,
        outcome TEXT,
        probability REAL,
        timestamp TEXT
      );
    `;
    await dbConn.execute(schema);
    console.log('[Orbix DB] SQLite ready - APK database initialized');
  } catch (e) {
    console.warn('[Orbix DB] SQLite failed, fallback to IndexedDB', e);
    useIndexedDB = true;
    await idb.init();
  }
}

// Unified API
export const db = {
  async init() {
    await initSQLite();
  },

  async importCandles(datasetId, pair, candles) {
    const now = new Date().toISOString();
    if (useIndexedDB) {
      await idb.put('datasets', {
        dataset_id: datasetId,
        pair,
        candle_count: candles.length,
        first_timestamp: candles[0]?.timestamp,
        latest_timestamp: candles[candles.length-1]?.timestamp,
        created_at: now,
        updated_at: now,
        source: 'mobile-apk'
      });
      for (const c of candles) {
        await idb.put('candles', {dataset_id: datasetId, ...c});
      }
      return datasetId;
    } else {
      await dbConn.run(`INSERT OR REPLACE INTO datasets 
        (dataset_id, pair, candle_count, first_timestamp, latest_timestamp, created_at, updated_at) 
        VALUES (?, ?, ?, ?, ?, ?, ?)`,
        [datasetId, pair, candles.length, candles[0]?.timestamp, candles[candles.length-1]?.timestamp, now, now]
      );
      for (const c of candles) {
        await dbConn.run(`INSERT OR IGNORE INTO candles 
          (dataset_id, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)`,
          [datasetId, c.timestamp, c.open, c.high, c.low, c.close, c.volume || 0]
        );
      }
      return datasetId;
    }
  },

  async getDatasets() {
    if (useIndexedDB) {
      return await idb.getAll('datasets');
    } else {
      const res = await dbConn.query('SELECT * FROM datasets ORDER BY updated_at DESC');
      return res.values || [];
    }
  },

  async getCandles(datasetId, limit = 500) {
    if (useIndexedDB) {
      const all = await idb.getAll('candles');
      return all.filter(c => c.dataset_id === datasetId).sort((a,b) => new Date(a.timestamp) - new Date(b.timestamp)).slice(-limit);
    } else {
      const res = await dbConn.query('SELECT * FROM candles WHERE dataset_id=? ORDER BY timestamp DESC LIMIT ?', [datasetId, limit]);
      const rows = (res.values || []).reverse();
      return rows;
    }
  },

  async getActiveDataset() {
    const datasets = await this.getDatasets();
    return datasets[0] || null;
  },

  async saveSignal(signal) {
    if (useIndexedDB) {
      await idb.put('signals', signal);
    } else {
      await dbConn.run(`INSERT OR REPLACE INTO signals 
        (signal_id, pair, strategy, side, entry_price, generated_at, expires_at, probability_up, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [signal.signal_id, signal.pair, signal.strategy, signal.side, signal.entry_price,
         signal.generated_at, signal.expires_at, signal.probability_up, signal.reason,
         signal.status || 'PENDING', new Date().toISOString()]
      );
    }
  },

  async getSignals(limit = 100) {
    if (useIndexedDB) {
      const all = await idb.getAll('signals');
      return all.sort((a,b) => new Date(b.generated_at) - new Date(a.generated_at)).slice(0, limit);
    } else {
      const res = await dbConn.query('SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?', [limit]);
      return res.values || [];
    }
  },

  async getSetting(key) {
    if (useIndexedDB) {
      const all = await idb.getAll('settings');
      const found = all.find(s => s.key === key);
      return found ? JSON.parse(found.value) : null;
    } else {
      const res = await dbConn.query('SELECT value FROM settings WHERE key=?', [key]);
      if (res.values && res.values.length) {
        try { return JSON.parse(res.values[0].value); } catch { return res.values[0].value; }
      }
      return null;
    }
  },

  async setSetting(key, value) {
    const val = JSON.stringify(value);
    if (useIndexedDB) {
      await idb.put('settings', {key, value: val, updated_at: new Date().toISOString()});
    } else {
      await dbConn.run('INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)',
        [key, val, new Date().toISOString()]);
    }
  },

  async clearAll() {
    if (useIndexedDB) {
      await idb.clear('candles');
      await idb.clear('datasets');
      await idb.clear('signals');
    } else {
      await dbConn.execute('DELETE FROM candles; DELETE FROM datasets; DELETE FROM signals; DELETE FROM settings;');
    }
  },

  async getStorageInfo() {
    const datasets = await this.getDatasets();
    const signals = await this.getSignals(10000);
    const candlesCount = datasets.reduce((a,b) => a + (b.candle_count || 0), 0);
    return {
      datasets: datasets.length,
      candles: candlesCount,
      signals: signals.length,
      dbType: useIndexedDB ? 'IndexedDB (web)' : 'SQLite (APK)',
      estimatedSize: `${((candlesCount * 0.05) + (signals.length * 0.5)).toFixed(1)} KB`
    };
  }
};

export default db;
