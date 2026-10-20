from __future__ import annotations

import csv
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from orbix.calibration import IsotonicCalibrator
from orbix.features import compute_features
from orbix.ledger import PaperLedger
from orbix.runtime import Runtime
from orbix.engine import PredictionEngine

UTC = timezone.utc


def write_history(path: Path, count: int, first: datetime) -> None:
    price = 1.1
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for index in range(count):
            timestamp = first + timedelta(minutes=index)
            opening = price
            drift = 0.00009 if index % 4 in (0, 1) else -0.00003
            close = price * (1.0 + drift + 0.00002 * math.sin(index / 7))
            writer.writerow([
                timestamp.isoformat().replace("+00:00", "Z"),
                format(opening, ".12f"),
                format(max(opening, close) + 0.00004, ".12f"),
                format(min(opening, close) - 0.00004, ".12f"),
                format(close, ".12f"),
                100,
            ])
            price = close


def flat_model(runtime: Runtime) -> None:
    """Force the production-style flat calibration gate on the champion."""
    model = runtime.get_or_train_model("EUR/USD")
    model.calibrator = IsotonicCalibrator(thresholds=[0.5], values=[0.55], samples=120)
    runtime.models["EUR/USD"] = model
    runtime._save_champion("EUR/USD", model)


def shadow_row(runtime: Runtime, bar: int, horizon: int = 1) -> dict:
    candles = runtime.candles
    entry_time = candles[bar].timestamp + timedelta(minutes=1)
    expires_at = entry_time + timedelta(minutes=horizon)
    return {
        "shadow_id": f"shadow-{bar}",
        "pair": "EUR/USD",
        "strategy": "ml_consensus",
        "side": "BUY",
        "entry_price": candles[bar].close,
        "entry_time": entry_time.isoformat(),
        "expires_at": expires_at.isoformat(),
        "probability_up": 0.61,
        "probability_calibrated": 0.55,
        "reason": "Shadow read (not traded): test.",
    }


class ShadowLedgerTests(unittest.TestCase):
    def test_shadow_rows_are_idempotent_and_separate_from_paper_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(data_dir=Path(directory) / "data")
            history = Path(directory) / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, tzinfo=UTC))
            runtime.import_csv_file(history, "EUR/USD")
            dataset_id = runtime.dataset_id()
            features = compute_features(runtime.candles)[-5]
            row = shadow_row(runtime, len(runtime.candles) - 5)

            self.assertTrue(runtime.ledger.record_shadow(row, features, dataset_id))
            duplicate = dict(row, shadow_id="another-id")
            self.assertFalse(runtime.ledger.record_shadow(duplicate, features, dataset_id))

            summary = runtime.ledger.shadow_summary(dataset_id=dataset_id)
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["pending"], 1)
            self.assertEqual(summary["resolved"], 0)
            self.assertEqual(runtime.ledger.list_recent(), [])
            self.assertEqual(runtime.ledger.learning_history(), [])
            runtime.candle_store.close()

    def test_migration_adds_shadow_columns_to_an_old_learning_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE learning_events (
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
            connection.execute(
                "INSERT INTO learning_events VALUES ('old', 'EUR/USD', 'd', 1, 0.6, 1, 1, '2025-01-01T00:00:00+00:00')"
            )
            connection.commit()
            connection.close()

            ledger = PaperLedger(path)
            with sqlite3.connect(path) as check:
                columns = {row[1] for row in check.execute("PRAGMA table_info(learning_events)")}
                saved = check.execute("SELECT kind, features_json FROM learning_events").fetchone()
            self.assertIn("kind", columns)
            self.assertIn("features_json", columns)
            self.assertEqual(saved[0], "paper")
            self.assertEqual(saved[1], "")
            self.assertEqual(ledger.learning_summary()["resolved_outcomes_seen"], 1)


class ShadowRuntimeTests(unittest.TestCase):
    def test_a_flat_gate_records_a_shadow_read_and_no_paper_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(data_dir=root / "data")
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, tzinfo=UTC))
            runtime.import_csv_file(history, "EUR/USD")
            flat_model(runtime)

            features = compute_features(runtime.candles)[-1]
            shadow = shadow_row(runtime, len(runtime.candles) - 1)
            with mock.patch.object(
                PredictionEngine, "raw_shadow", return_value=(shadow, features)
            ):
                payload = runtime.generate_signal("EUR/USD", "ml_consensus", 1)

            self.assertEqual(payload["side"], "NO SIGNAL")
            self.assertIn("calibration is flat", payload["reason"])
            self.assertTrue(payload["shadow"]["recorded"])
            self.assertEqual(payload["shadow"]["side"], "BUY")
            self.assertEqual(runtime.ledger.list_recent(), [])
            summary = runtime.ledger.shadow_summary(dataset_id=runtime.dataset_id())
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["pending"], 1)
            learning = runtime.ledger.learning_summary()
            self.assertEqual(learning["resolved_outcomes_seen"], 0)
            self.assertEqual(learning["shadow_resolved"], 0)
            runtime.candle_store.close()

    def test_shadow_outcomes_resolve_and_learn_but_stay_out_of_paper_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(data_dir=root / "data")
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, tzinfo=UTC))
            runtime.import_csv_file(history, "EUR/USD")
            flat_model(runtime)
            dataset_id = runtime.dataset_id()

            bar = len(runtime.candles) - 10
            rows = compute_features(runtime.candles)
            row = shadow_row(runtime, bar)
            self.assertTrue(runtime.ledger.record_shadow(row, rows[bar], dataset_id))

            outcomes = runtime._resolve_matured_outcomes()
            self.assertEqual([item["kind"] for item in outcomes], ["shadow"])
            self.assertEqual(outcomes[0]["signal_id"], row["shadow_id"])

            summary = runtime.ledger.shadow_summary(dataset_id=dataset_id)
            self.assertEqual(summary["resolved"], 1)
            self.assertEqual(summary["pending"], 0)
            self.assertIsNotNone(summary["accuracy"])

            learning = runtime.ledger.learning_summary()
            self.assertEqual(learning["resolved_outcomes_seen"], 0)
            self.assertEqual(learning["shadow_resolved"], 1)
            self.assertEqual(runtime.ledger.learning_history(), [])

            engine = runtime.learning_engine("EUR/USD")
            self.assertEqual(engine.tracker.total_seen, 0)
            if summary["learned"]:
                self.assertEqual(engine.shadow_tracker.total_seen, 1)
                self.assertEqual(len(runtime.ledger.shadow_history()), 1)
            runtime.candle_store.close()

    def test_model_card_and_preflight_expose_shadow_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(data_dir=root / "data")
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, tzinfo=UTC))
            runtime.import_csv_file(history, "EUR/USD")
            flat_model(runtime)
            dataset_id = runtime.dataset_id()
            bar = len(runtime.candles) - 10
            rows = compute_features(runtime.candles)
            runtime.ledger.record_shadow(shadow_row(runtime, bar), rows[bar], dataset_id)
            runtime._resolve_matured_outcomes()

            card = runtime.model_card("EUR/USD")
            self.assertEqual(card["shadow"]["resolved"], 1)
            self.assertEqual(card["shadow"]["pending"], 0)
            self.assertIn("not paper trades", card["shadow"]["note"])
            runtime.candle_store.close()


if __name__ == "__main__":
    unittest.main()
