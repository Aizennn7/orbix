/**
 * Orbix Mobile Database - IndexedDB local store for the PWA.
 *
 * Offline-first: candles, datasets, signals and settings are kept in the
 * browser. Optional Supabase sync is handled separately by live_db.js.
 */

class IndexedDBWrapper {
  constructor() {
    this.dbName = 'orbix-mobile-v1';
    this.version = 2;
    this.db = null;
  }

  async init() {
    if (this.db) return this.db;
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

export const db = {
  async init() {
    await idb.init();
    console.log('[Orbix DB] IndexedDB ready');
  },

  async importCandles(datasetId, pair, candles) {
    const now = new Date().toISOString();
    await idb.put('datasets', {
      dataset_id: datasetId,
      pair,
      candle_count: candles.length,
      first_timestamp: candles[0]?.timestamp,
      latest_timestamp: candles[candles.length-1]?.timestamp,
      created_at: now,
      updated_at: now,
      source: 'mobile-pwa'
    });
    for (const c of candles) {
      await idb.put('candles', {dataset_id: datasetId, ...c});
    }
    return datasetId;
  },

  async getDatasets() {
    const all = await idb.getAll('datasets');
    return all.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  },

  async getCandles(datasetId, limit = 500) {
    const all = await idb.getAll('candles');
    return all
      .filter(c => c.dataset_id === datasetId)
      .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
      .slice(-limit);
  },

  async getActiveDataset() {
    const datasets = await this.getDatasets();
    return datasets[0] || null;
  },

  async saveSignal(signal) {
    await idb.put('signals', signal);
  },

  async getSignals(limit = 100) {
    const all = await idb.getAll('signals');
    return all.sort((a, b) => new Date(b.generated_at) - new Date(a.generated_at)).slice(0, limit);
  },

  async getSetting(key) {
    const all = await idb.getAll('settings');
    const found = all.find(s => s.key === key);
    if (!found) return null;
    try { return JSON.parse(found.value); } catch { return found.value; }
  },

  async setSetting(key, value) {
    await idb.put('settings', {key, value: JSON.stringify(value), updated_at: new Date().toISOString()});
  },

  async clearAll() {
    await idb.clear('candles');
    await idb.clear('datasets');
    await idb.clear('signals');
    await idb.clear('settings');
  },

  async getStorageInfo() {
    const datasets = await this.getDatasets();
    const signals = await this.getSignals(10000);
    const candlesCount = datasets.reduce((a, b) => a + (b.candle_count || 0), 0);
    return {
      datasets: datasets.length,
      candles: candlesCount,
      signals: signals.length,
      dbType: 'IndexedDB',
      estimatedSize: `${((candlesCount * 0.05) + (signals.length * 0.5)).toFixed(1)} KB`
    };
  }
};

export default db;
