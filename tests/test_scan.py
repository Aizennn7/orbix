from __future__ import annotations

import csv
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orbix.domain import format_datetime, parse_datetime
from orbix.runtime import SCAN_ORDER, Runtime
from orbix.strategies import EXECUTABLE_STRATEGIES

UTC = timezone.utc


def write_history(path: Path, count: int, first: datetime) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for index in range(count):
            timestamp = first + timedelta(minutes=index)
            opening = 1.1 + 0.00002 * index
            drift = 0.00009 if index % 4 in (0, 1) else -0.00003
            close = opening * (1.0 + drift + 0.00002 * math.sin(index / 7))
            writer.writerow([
                timestamp.isoformat().replace("+00:00", "Z"),
                format(opening, ".12f"),
                format(max(opening, close) + 0.00004, ".12f"),
                format(min(opening, close) - 0.00004, ".12f"),
                format(close, ".12f"),
                100,
            ])


class StrategyScanTests(unittest.TestCase):
    def test_demo_scan_tests_every_strategy_without_recording(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(data_dir=Path(folder) / "data")
            scan = runtime.scan_strategies("EUR/USD", 1)
            ids = [result["strategy"] for result in scan["results"]]
            self.assertEqual(ids, list(SCAN_ORDER))
            self.assertEqual(set(ids), {strategy["id"] for strategy in EXECUTABLE_STRATEGIES})
            self.assertEqual(scan["qualified"], sum(1 for result in scan["results"] if result["side"] in {"BUY", "SELL"}))
            self.assertEqual(runtime.ledger.list_recent(), [])
            for result in scan["results"]:
                self.assertIn(result["side"], {"BUY", "SELL", "NO SIGNAL"})
                self.assertTrue(result["reason"])
                self.assertIn("grade", result)
                self.assertIn("entry_time", result)
            if scan["best"] is not None:
                self.assertIn(scan["best"]["side"], {"BUY", "SELL"})
                first_qualified = next(
                    result for result in scan["results"] if result["side"] in {"BUY", "SELL"}
                )
                self.assertEqual(scan["best"]["strategy"], first_qualified["strategy"])
            runtime.candle_store.close()

    def test_imported_scan_uses_one_model_and_writes_no_signal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, 8, 0, tzinfo=UTC))
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            scan = runtime.scan_strategies("EUR/USD", 1)
            self.assertEqual(len(scan["results"]), len(EXECUTABLE_STRATEGIES))
            self.assertEqual(runtime.ledger.list_recent(), [])
            self.assertEqual(scan["dataset_id"], runtime.dataset_id())
            by_id = {result["strategy"]: result for result in scan["results"]}
            self.assertIn("orbix_cadence", by_id)
            self.assertIn("orbix_confluence", by_id)
            for result in scan["results"]:
                self.assertTrue(result["entry_time"])
                self.assertTrue(result["prediction_time"])
            runtime.candle_store.close()

    def test_recording_the_best_read_writes_exactly_one_signal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, 8, 0, tzinfo=UTC))
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            scan = runtime.scan_strategies("EUR/USD", 1)
            best = scan["best"]
            self.assertIsNotNone(best)
            self.assertEqual(scan["qualified"], sum(
                1 for result in scan["results"] if result["side"] in {"BUY", "SELL"}
            ))
            recorded = runtime.generate_signal("EUR/USD", best["strategy"], 1)
            self.assertEqual(recorded["side"], best["side"])
            self.assertEqual(recorded["strategy"], best["strategy"])
            self.assertEqual(len(runtime.ledger.list_recent(limit=10)), 1)
            self.assertTrue(
                runtime.ledger.has_signal_for_time(
                    "EUR/USD", best["strategy"], scan["dataset_id"], parse_datetime(best["entry_time"])
                )
            )
            runtime.candle_store.close()

    def test_the_same_candle_cannot_record_the_same_strategy_twice(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, 8, 0, tzinfo=UTC))
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            scan = runtime.scan_strategies("EUR/USD", 1)
            best = scan["best"]
            self.assertIsNotNone(best)
            runtime.generate_signal("EUR/USD", best["strategy"], 1)
            with self.assertRaises(ValueError):
                runtime.generate_signal("EUR/USD", best["strategy"], 1)
            self.assertEqual(len(runtime.ledger.list_recent(limit=10)), 1)
            runtime.candle_store.close()

    def test_clock_is_live_on_demo_and_replays_an_imported_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = Runtime(data_dir=root / "demo-data")
            live = runtime.clock()
            self.assertEqual(live["mode"], "LIVE")
            self.assertEqual(live["source"], "SYNTHETIC DEMO")
            runtime.candle_store.close()

            history = root / "history.csv"
            first = datetime(2025, 5, 1, 8, 0, tzinfo=UTC)
            write_history(history, 320, first)
            replay_runtime = Runtime(data_dir=root / "replay-data")
            replay_runtime.import_csv_file(history, "EUR/USD")
            clock = replay_runtime.clock()
            expected = first + timedelta(minutes=320)
            self.assertEqual(clock["mode"], "REPLAY")
            self.assertEqual(clock["data_time"], format_datetime(expected))
            self.assertEqual(clock["reference"], clock["data_time"])
            self.assertGreater(clock["lag_seconds"], 300)
            replay_runtime.candle_store.close()

    def test_blackout_turns_every_method_into_no_signal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(data_dir=Path(folder) / "data")
            reason = "High-impact event blackout: CPI year over year (US)."
            scan = runtime.scan_strategies(
                "EUR/USD", 1, safety_filter_reason=reason
            )
            self.assertEqual(scan["qualified"], 0)
            self.assertIsNone(scan["best"])
            self.assertTrue(all(result["side"] == "NO SIGNAL" for result in scan["results"]))
            self.assertTrue(all(result["reason"] == reason for result in scan["results"]))
            self.assertEqual(runtime.ledger.list_recent(), [])
            runtime.candle_store.close()

    def test_scan_refuses_a_pair_that_does_not_match_the_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, 8, 0, tzinfo=UTC))
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            with self.assertRaisesRegex(ValueError, "Imported CSV data is for EUR/USD"):
                runtime.scan_strategies("GBP/USD", 1)
            runtime.candle_store.close()


if __name__ == "__main__":
    unittest.main()
