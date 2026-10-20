from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from .metrics import wilson_interval


class PaperLedger:
    """Small SQLite ledger for manually confirmed paper outcomes."""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    probability_up REAL,
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    dataset_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    exit_price REAL,
                    outcome TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(signals)")}
            if "dataset_id" not in columns:
                connection.execute("ALTER TABLE signals ADD COLUMN dataset_id TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS learning_events (
                    signal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    label INTEGER,
                    probability_up REAL,
                    correct INTEGER,
                    model_updated INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    def record(self, signal, features: dict[str, float] | None, dataset_id: str = "") -> None:
        if signal.side == "NO SIGNAL":
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO signals
                (signal_id, pair, strategy, side, entry_price, generated_at, expires_at,
                 probability_up, source, reason, features_json, dataset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.signal_id,
                    signal.pair,
                    signal.strategy,
                    signal.side,
                    signal.entry_price,
                    signal.generated_at.isoformat(),
                    signal.expires_at.isoformat(),
                    signal.probability_up,
                    signal.source,
                    signal.reason,
                    json.dumps(features or {}),
                    dataset_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("features_json", None)
            result.append(item)
        return result

    def get(self, signal_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
        return dict(row) if row else None

    def list_pending(self, pair: str, dataset_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM signals
                   WHERE status = 'PENDING' AND source = 'IMPORTED CSV'
                     AND pair = ? AND dataset_id = ?
                   ORDER BY expires_at, created_at""",
                (pair, dataset_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("features_json"))
            result.append(item)
        return result

    def record_learning_event(
        self,
        signal_id: str,
        pair: str,
        dataset_id: str,
        label: int | None,
        probability_up: float | None,
        correct: bool | None,
        model_updated: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO learning_events
                   (signal_id, pair, dataset_id, label, probability_up, correct, model_updated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal_id,
                    pair,
                    dataset_id,
                    label,
                    probability_up,
                    None if correct is None else int(correct),
                    int(model_updated),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def learning_summary(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(model_updated), 0),
                          AVG(CASE WHEN model_updated = 1 THEN correct END), MAX(created_at),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct = 1 THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct IS NOT NULL THEN 1 ELSE 0 END), 0)
                   FROM learning_events"""
            ).fetchone()
        correct = int(row[4])
        evaluated = int(row[5])
        return {
            "resolved_outcomes_seen": int(row[0]),
            "online_updates": int(row[1]),
            "prequential_accuracy": float(row[2]) if row[2] is not None else None,
            "prequential_correct_predictions": correct,
            "prequential_evaluated_predictions": evaluated,
            "prequential_accuracy_ci95": wilson_interval(correct, evaluated),
            "last_update": row[3],
        }

    def resolve(self, signal_id: str, exit_price: float) -> dict:
        if exit_price <= 0:
            raise ValueError("Exit price must be greater than zero")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("Signal was not found")
            if row["status"] != "PENDING":
                raise ValueError("Signal has already been resolved")
            if row["source"] == "SYNTHETIC DEMO":
                raise ValueError("Synthetic demo outcomes cannot train the model; import real historical/paper data first")
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > datetime.now(timezone.utc):
                raise ValueError("Signal has not reached its expiry time yet")
            entry = float(row["entry_price"])
            side = row["side"]
            if exit_price == entry:
                direction_label = None
                outcome = "TIE"
            else:
                direction_label = int(exit_price > entry)
                won = (side == "BUY" and direction_label == 1) or (side == "SELL" and direction_label == 0)
                outcome = "WIN" if won else "LOSS"
            connection.execute(
                "UPDATE signals SET status = 'RESOLVED', exit_price = ?, outcome = ? WHERE signal_id = ?",
                (exit_price, outcome, signal_id),
            )
            updated = dict(connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone())
        updated["features"] = json.loads(updated.pop("features_json"))
        updated["direction_label"] = direction_label
        return updated
