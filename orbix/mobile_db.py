"""
Orbix Mobile Database Adapter - SQLite store for the mobile client, works offline
Replaces DuckDB for mobile where native libs unavailable
Same API for the desktop (Electron) and mobile clients
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .domain import Candle, parse_datetime

# Root level database config - single source of truth
try:
    from database import get_database_config
    _root_config = get_database_config()
    print(f"[MobileDB] Using root config: { _root_config.data_dir} backend={_root_config.effective_backend.value}")
except Exception as e:
    _root_config = None
    print(f"[MobileDB] Root config not available: {e}, using local")


class MobileCandleStore:
    """SQLite-backed candle store for the mobile client - offline first"""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
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
                    source TEXT NOT NULL DEFAULT 'mobile'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0,
                    UNIQUE(dataset_id, timestamp)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_dataset_time ON candles(dataset_id, timestamp)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_dataset (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    dataset_id TEXT,
                    pair TEXT NOT NULL DEFAULT 'EUR/USD',
                    source TEXT NOT NULL DEFAULT 'mobile',
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def import_csv(self, path: Path, pair: str = "EUR/USD") -> str:
        """Import CSV, same API as DuckDB version but SQLite"""
        import csv, hashlib, uuid
        dataset_id = f"mobile-{uuid.uuid4().hex[:12]}"
        sha = hashlib.sha256()
        rows = []
        first_ts = None
        last_ts = None

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            sha.update(content.encode())
            f.seek(0)
            reader = csv.DictReader(f)
            for row in reader:
                # Support various CSV formats
                ts_raw = row.get('timestamp') or row.get('datetime') or row.get('time') or row.get('date')
                if not ts_raw:
                    continue
                try:
                    dt = parse_datetime(ts_raw)
                except Exception:
                    continue
                try:
                    o = float(row.get('open', row.get('o', 0)))
                    h = float(row.get('high', row.get('h', 0)))
                    l = float(row.get('low', row.get('l', 0)))
                    c = float(row.get('close', row.get('c', 0)))
                    v = float(row.get('volume', row.get('v', 0)) or 0)
                except Exception:
                    continue
                iso = dt.astimezone(timezone.utc).isoformat()
                rows.append((dataset_id, iso, o, h, l, c, v))
                if first_ts is None:
                    first_ts = iso
                last_ts = iso

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO datasets (dataset_id, pair, timeframe, candle_count, first_timestamp, latest_timestamp, content_sha256, created_at, updated_at, source)
                VALUES (?, ?, 'M1', ?, ?, ?, ?, ?, ?, 'mobile')
            """, (dataset_id, pair, len(rows), first_ts, last_ts, sha.hexdigest(), now, now))
            conn.executemany("""
                INSERT OR IGNORE INTO candles (dataset_id, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.execute("""
                INSERT OR REPLACE INTO active_dataset (singleton, dataset_id, pair, source, updated_at)
                VALUES (1, ?, ?, 'mobile', ?)
            """, (dataset_id, pair, now))
            conn.commit()
        return dataset_id

    def get_candles(self, dataset_id: str, limit: int = 1000) -> List[Candle]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("""
                SELECT timestamp, open, high, low, close, volume
                FROM candles WHERE dataset_id=? ORDER BY timestamp DESC LIMIT ?
            """, (dataset_id, limit))
            result = []
            for row in cur.fetchall():
                try:
                    dt = parse_datetime(row['timestamp'])
                    result.append(Candle(
                        timestamp=dt,
                        open=row['open'],
                        high=row['high'],
                        low=row['low'],
                        close=row['close'],
                        volume=row['volume']
                    ))
                except Exception:
                    continue
            return list(reversed(result))

    def get_active_dataset(self) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM active_dataset WHERE singleton=1")
            row = cur.fetchone()
            return dict(row) if row else None

    def list_datasets(self) -> List[dict]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM datasets ORDER BY updated_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def append_candle(self, dataset_id: str, candle: Candle) -> bool:
        """Append single candle - for live feed"""
        iso = candle.timestamp.astimezone(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO candles (dataset_id, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (dataset_id, iso, candle.open, candle.high, candle.low, candle.close, candle.volume))
                conn.execute("""
                    UPDATE datasets SET candle_count = candle_count + 1, latest_timestamp=?, updated_at=?
                    WHERE dataset_id=?
                """, (iso, datetime.now(timezone.utc).isoformat(), dataset_id))
                conn.commit()
                return True
            except Exception:
                return False


class MobileLedger:
    """SQLite ledger for mobile - same API as PaperLedger but mobile-optimized"""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    entry_time TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    probability_up REAL,
                    probability_raw REAL,
                    uncertainty REAL,
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    dataset_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    exit_price REAL,
                    outcome TEXT,
                    created_at TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS abstentions (
                    abstention_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT,
                    pair TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    probability REAL,
                    timestamp TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def save_signal(self, signal: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO signals
                (signal_id, pair, strategy, side, entry_price, generated_at, entry_time, expires_at,
                 probability_up, probability_raw, uncertainty, source, reason, features_json, dataset_id,
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal['signal_id'], signal['pair'], signal['strategy'], signal['side'],
                signal['entry_price'], signal['generated_at'], signal.get('entry_time',''),
                signal['expires_at'], signal.get('probability_up'), signal.get('probability_raw'),
                signal.get('uncertainty'), signal['source'], signal['reason'],
                json.dumps(signal.get('features', {})), signal.get('dataset_id',''),
                signal.get('status','PENDING'), datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def get_signals(self, limit: int = 100) -> List[dict]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def get_setting(self, key: str, default=None):
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cur.fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except Exception:
                    return row['value']
            return default

    def set_setting(self, key: str, value) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(value), datetime.now(timezone.utc).isoformat()))
            conn.commit()


def get_mobile_store(data_dir: Path) -> tuple[MobileCandleStore, MobileLedger]:
    """Factory for the mobile client stores."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    candle_db = data_dir / "mobile_candles.db"
    ledger_db = data_dir / "mobile_ledger.db"
    return MobileCandleStore(candle_db), MobileLedger(ledger_db)
