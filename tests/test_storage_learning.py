from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbix.candle_store import CandleStore
from orbix.runtime import Runtime


class StorageAndOnlineLearningTests(unittest.TestCase):
    def write_history(self, path: Path, count: int, first: datetime, breakout_last: bool = False) -> tuple[datetime, float]:
        price = 1.1
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for index in range(count):
                timestamp = first + timedelta(minutes=index)
                opening = price
                if breakout_last and index == count - 1:
                    close = price + 0.002
                else:
                    drift = 0.00008 if index % 3 == 0 else -0.00004 if index % 3 == 1 else 0.00002
                    close = price * (1.0 + drift + 0.00001 * math.sin(index / 5))
                high = max(opening, close) + 0.00005
                low = min(opening, close) - 0.00005
                writer.writerow([
                    timestamp.isoformat().replace("+00:00", "Z"),
                    format(opening, ".12f"),
                    format(high, ".12f"),
                    format(low, ".12f"),
                    format(close, ".12f"),
                    100,
                ])
                price = close
        return first + timedelta(minutes=count - 1), price

    def test_chunked_import_append_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initial = root / "initial.csv"
            first = datetime(2025, 1, 1, tzinfo=timezone.utc)
            latest_timestamp, latest_price = self.write_history(initial, 120, first)
            store = CandleStore(root / "data" / "candles.duckdb")
            with patch("orbix.candle_store.IMPORT_CHUNK_ROWS", 40):
                imported = store.import_csv_file(initial, "EUR/USD")
            self.assertEqual(imported["count"], 120)
            self.assertEqual(imported["source"], "IMPORTED CSV")
            self.assertEqual(len(store.load_latest(imported["dataset_id"], 720)), 120)

            append = root / "append.csv"
            append_time = latest_timestamp + timedelta(minutes=1)
            with append.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                writer.writerow([append_time.isoformat().replace("+00:00", "Z"), latest_price, latest_price + .0001, latest_price - .0001, latest_price + .00005, 90])
            appended = store.append_csv_file(append, "EUR/USD")
            self.assertEqual(appended["count"], 1)
            self.assertEqual(appended["candle_count"], 121)
            self.assertEqual(store.active_state()["candle_count"], 121)

            bad_append = root / "bad.csv"
            with bad_append.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                writer.writerow([append_time.isoformat().replace("+00:00", "Z"), 1.1, 1.2, 1.0, 1.15, 10])
            with self.assertRaisesRegex(ValueError, "ascending"):
                store.append_csv_file(bad_append, "EUR/USD")
            self.assertEqual(store.active_state()["candle_count"], 121)

            second = root / "second.csv"
            self.write_history(second, 120, datetime(2025, 2, 1, tzinfo=timezone.utc))
            other = store.import_csv_file(second, "GBP/USD")
            self.assertEqual(store.active_state()["dataset_id"], other["dataset_id"])
            activated = store.activate_dataset(imported["dataset_id"])
            self.assertEqual(activated["pair"], "EUR/USD")
            self.assertEqual(store.active_state()["dataset_id"], imported["dataset_id"])
            self.assertEqual(len(store.load_latest(imported["dataset_id"], 720)), 121)

            malformed = root / "malformed.csv"
            self.write_history(malformed, 120, datetime(2025, 3, 1, tzinfo=timezone.utc))
            lines = malformed.read_text(encoding="utf-8").splitlines()
            fields = lines[-1].split(",")
            fields[4] = "not-a-price"
            lines[-1] = ",".join(fields)
            malformed.write_text("\n".join(lines), encoding="utf-8")
            with patch("orbix.candle_store.IMPORT_CHUNK_ROWS", 40):
                with self.assertRaisesRegex(ValueError, "Invalid candle row"):
                    store.import_csv_file(malformed, "AUD/USD")
            self.assertEqual(store.summary()["datasets_archived"], 2)
            self.assertEqual(store.summary()["candles_archived"], 241)
            self.assertEqual(store.active_state()["dataset_id"], imported["dataset_id"])

            store.close()

    def test_expired_signal_auto_resolves_and_updates_model_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            first = now - timedelta(minutes=181)
            initial = root / "history.csv"
            latest_timestamp, _ = self.write_history(initial, 180, first, breakout_last=True)

            runtime = Runtime(data_dir=root / "data")
            imported = runtime.import_csv_file(initial, "EUR/USD")
            signal = runtime.generate_signal("EUR/USD", "breakout", 1)
            self.assertEqual(signal["side"], "BUY")
            self.assertFalse(signal["is_live"])
            self.assertEqual(signal["source"], "IMPORTED CSV")

            exit_time = latest_timestamp + timedelta(minutes=1)
            exit_price = signal["entry_price"] + 0.001
            append = root / "next-candle.csv"
            with append.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                writer.writerow([exit_time.isoformat().replace("+00:00", "Z"), signal["entry_price"], exit_price + .0001, signal["entry_price"] - .0001, exit_price, 110])

            result = runtime.append_csv_file(append, "EUR/USD")
            self.assertEqual(result["candle_count"], 181)
            self.assertEqual(result["automatic_model_updates"], 1)
            self.assertEqual(result["auto_resolved"][0]["outcome"], "WIN")
            self.assertTrue(result["auto_resolved"][0]["learned"])
            summary = runtime.learning_summary()
            self.assertEqual(summary["online_updates"], 1)
            self.assertEqual(summary["resolved_outcomes_seen"], 1)
            self.assertIsNotNone(summary["prequential_accuracy"])
            self.assertEqual(summary["prequential_evaluated_predictions"], 1)
            self.assertIn("lower", summary["prequential_accuracy_ci95"])
            self.assertIn("upper", summary["prequential_accuracy_ci95"])
            self.assertEqual(imported["dataset_id"], runtime.dataset_id())

            runtime.candle_store.close()
            restored = Runtime(data_dir=root / "data")
            self.assertEqual(restored.source, "IMPORTED CSV")
            self.assertEqual(restored.candle_count, 181)
            self.assertEqual(restored.dataset_id(), imported["dataset_id"])
            self.assertEqual(restored.learning_summary()["online_updates"], 1)
            restored.candle_store.close()


if __name__ == "__main__":
    unittest.main()
