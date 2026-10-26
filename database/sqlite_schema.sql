-- Orbix SQLite Schema - Root Level - For Mobile, Laptop, Offline
-- All Orbix data inside SQLite, works everywhere
-- This is the default backend, configured at root level

-- Datasets
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT 'M1',
    candle_count INTEGER NOT NULL DEFAULT 0,
    first_timestamp TEXT,
    latest_timestamp TEXT,
    content_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'IMPORTED',
    metadata TEXT NOT NULL DEFAULT '{}'
);

-- Candles - M1 OHLCV
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dataset_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_dataset_time ON candles(dataset_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles(timestamp DESC);

-- Candle tail - hot window
CREATE TABLE IF NOT EXISTS candle_tail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    UNIQUE(dataset_id, timestamp)
);

-- Active dataset - singleton
CREATE TABLE IF NOT EXISTS active_dataset (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    dataset_id TEXT REFERENCES datasets(dataset_id),
    pair TEXT NOT NULL DEFAULT 'EUR/USD',
    source TEXT NOT NULL DEFAULT 'SYNTHETIC DEMO',
    updated_at TEXT NOT NULL
);

-- Signals - paper trading
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL', 'NO_SIGNAL')),
    entry_price REAL NOT NULL,
    generated_at TEXT NOT NULL,
    entry_time TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    probability_up REAL,
    probability_raw REAL,
    uncertainty REAL,
    source TEXT NOT NULL DEFAULT 'SYNTHETIC DEMO',
    reason TEXT NOT NULL,
    features_json TEXT NOT NULL DEFAULT '{}',
    dataset_id TEXT REFERENCES datasets(dataset_id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'RESOLVED', 'CANCELLED')),
    exit_price REAL,
    outcome TEXT CHECK(outcome IN ('WIN', 'LOSS', 'TIE', NULL)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_signals_pair_time ON signals(pair, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy);

-- Abstentions
CREATE TABLE IF NOT EXISTS abstentions (
    abstention_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    strategy TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    session TEXT,
    hour INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_abstentions_pair_time ON abstentions(pair, timestamp DESC);

-- Paper outcomes
CREATE TABLE IF NOT EXISTS paper_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT REFERENCES signals(signal_id) ON DELETE CASCADE,
    pair TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('WIN', 'LOSS', 'TIE')),
    probability REAL,
    entry_price REAL,
    exit_price REAL,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Shadow signals
CREATE TABLE IF NOT EXISTS shadow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    raw_probability REAL,
    calibrated_probability REAL,
    reason TEXT,
    timestamp TEXT NOT NULL,
    outcome TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Learning outcomes
CREATE TABLE IF NOT EXISTS learning_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT,
    pair TEXT NOT NULL,
    outcome TEXT NOT NULL,
    probability REAL,
    features_json TEXT,
    timestamp TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Models
CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    pair TEXT NOT NULL,
    strategy TEXT NOT NULL,
    accuracy REAL,
    brier_score REAL,
    log_loss REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_champion INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);

-- Settings - key/value
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sync queue - offline-first clients
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('INSERT', 'UPDATE', 'DELETE')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    synced INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_queue_synced ON sync_queue(synced, created_at);

-- Provider settings
CREATE TABLE IF NOT EXISTS provider_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    settings TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider)
);

-- Seed - demo dataset
INSERT OR IGNORE INTO datasets (dataset_id, pair, candle_count, created_at, updated_at, source)
VALUES ('demo-root', 'EUR/USD', 0, datetime('now'), datetime('now'), 'SYNTHETIC DEMO');

INSERT OR IGNORE INTO active_dataset (singleton, dataset_id, pair, source, updated_at)
VALUES (1, 'demo-root', 'EUR/USD', 'SYNTHETIC DEMO', datetime('now'));
