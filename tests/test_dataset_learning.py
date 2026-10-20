from __future__ import annotations

import csv
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from orbix.automation import AutomationService
from orbix.features import compute_features
from orbix.runtime import Runtime

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


class DatasetLearningTests(unittest.TestCase):
    def test_import_seeds_and_each_new_matured_bar_is_learned_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = datetime(2025, 5, 1, tzinfo=UTC)
            history = root / "history.csv"
            write_history(history, 320, first)
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            dataset_id = runtime.dataset_id()

            self.assertEqual(runtime.ledger.dataset_summary(dataset_id)["total"], 0)
            self.assertIsNotNone(runtime.ledger.last_dataset_entry_time(dataset_id))

            append = root / "append.csv"
            write_history(append, 10, first + timedelta(minutes=320))
            result = runtime.append_csv_file(append)

            learning = result["dataset_learning"]
            self.assertEqual(learning["status"], "learned")
            self.assertEqual(learning["processed"], 9)
            self.assertEqual(learning["learned"], 9)
            self.assertEqual(result["dataset_model_updates"], 9)

            summary = runtime.ledger.dataset_summary(dataset_id)
            self.assertEqual(summary["total"], 9)
            self.assertEqual(summary["resolved"], 9)
            self.assertEqual(summary["learned"], 9)
            self.assertIsNotNone(summary["accuracy"])
            self.assertEqual(len(runtime.ledger.dataset_history(dataset_id=dataset_id)), 9)

            counts = runtime.ledger.learning_summary()
            self.assertEqual(counts["resolved_outcomes_seen"], 0)
            self.assertEqual(counts["dataset_resolved"], 9)
            self.assertEqual(counts["dataset_learned"], 9)
            self.assertIsNotNone(counts["dataset_last_entry"])

            engine = runtime.learning_engine("EUR/USD")
            self.assertEqual(engine.tracker.total_seen, 0)
            self.assertEqual(engine.shadow_tracker.total_seen, 0)
            self.assertEqual(engine.dataset_tracker.total_seen, 9)
            self.assertEqual(engine.dataset_observations, 9)

            again = runtime.learn_dataset_outcomes()
            self.assertEqual(again["status"], "no_new")
            self.assertEqual(again["processed"], 0)
            self.assertEqual(runtime.ledger.dataset_summary(dataset_id)["total"], 9)

            card = runtime.model_card("EUR/USD")
            self.assertEqual(card["dataset"]["total"], 9)
            self.assertEqual(card["dataset"]["learned"], 9)
            self.assertTrue(card["dataset"]["enabled"])
            self.assertIn("not paper trades", card["dataset"]["note"])
            runtime.candle_store.close()

    def test_a_bar_already_learned_by_a_signal_is_not_double_weighted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = datetime(2025, 5, 1, tzinfo=UTC)
            history = root / "history.csv"
            write_history(history, 320, first)
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            dataset_id = runtime.dataset_id()

            append = root / "first.csv"
            write_history(append, 10, first + timedelta(minutes=320))
            runtime.append_csv_file(append)
            self.assertEqual(runtime.ledger.dataset_summary(dataset_id)["total"], 9)

            features = compute_features(runtime.candles)[-1]
            runtime.ledger.record_learning_event(
                signal_id="paper-covered",
                pair="EUR/USD",
                dataset_id=dataset_id,
                label=1,
                probability_up=0.55,
                correct=True,
                model_updated=True,
                kind="paper",
                features=features,
                entry_time=(first + timedelta(minutes=331)).isoformat(),
            )

            second = root / "second.csv"
            write_history(second, 2, first + timedelta(minutes=330))
            result = runtime.append_csv_file(second)
            learning = result["dataset_learning"]
            self.assertEqual(learning["covered_by_signal"], 1)
            self.assertEqual(learning["processed"], 2)
            self.assertEqual(learning["learned"], 1)
            self.assertEqual(runtime.ledger.dataset_summary(dataset_id)["total"], 10)
            runtime.candle_store.close()

    def test_a_gap_is_not_faked_into_a_one_minute_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = datetime(2025, 5, 1, tzinfo=UTC)
            history = root / "history.csv"
            write_history(history, 320, first)
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")

            # 320 missing: the 320->322 pair spans two minutes and must be skipped.
            append = root / "gap.csv"
            append.write_text(
                "timestamp,open,high,low,close,volume\n"
                f"{(first + timedelta(minutes=321)).isoformat()},1.1,1.1001,1.0999,1.10005,100\n"
                f"{(first + timedelta(minutes=323)).isoformat()},1.10005,1.1002,1.1,1.1001,100\n"
                f"{(first + timedelta(minutes=324)).isoformat()},1.1001,1.1003,1.1000,1.1002,100\n",
                encoding="utf-8",
            )
            result = runtime.append_csv_file(append)
            # Only 323 has a contiguous next minute; 321 and 324 have no label yet.
            self.assertEqual(result["dataset_learning"]["processed"], 1)
            runtime.candle_store.close()


class ImportedAutomationTests(unittest.TestCase):
    def make_runtime(self, root: Path, minutes_ago: int = 200) -> Runtime:
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        first = now - timedelta(minutes=minutes_ago)
        history = root / "history.csv"
        write_history(history, minutes_ago, first)
        # This test exercises the imported paper path, not the human session
        # clock, so it runs with the trading window open all day.
        with mock.patch.dict(os.environ, {"ORBIX_TRADING_SESSIONS": "all"}):
            runtime = Runtime(data_dir=root / "data")
        runtime.import_csv_file(history, "EUR/USD")
        return runtime

    def test_dataset_scan_generates_a_paper_only_read_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self.make_runtime(root)
            service = AutomationService(runtime)
            service.auto_imported = True
            service.strategy_id = "orbix_confluence"

            original = runtime.generate_signal
            calls = []

            def spy(*args, **kwargs):
                calls.append((args, kwargs))
                return original(*args, **kwargs)

            runtime.generate_signal = spy  # type: ignore[assignment]
            result = service._dataset_scan()

            self.assertEqual(result["status"], "analyzed")
            self.assertEqual(len(calls), 1)
            args, kwargs = calls[0]
            self.assertEqual(args[0], "EUR/USD")
            self.assertFalse(kwargs["is_live"])
            self.assertIn("paper-only", service._last_auto_status)
            self.assertEqual(service._last_imported_timestamp, runtime.candles[-1].timestamp)

            second = service._dataset_scan()
            self.assertEqual(second["status"], "waiting")
            self.assertEqual(len(calls), 1)

            status = service.status()
            self.assertTrue(status["paper_automation_active"])
            self.assertFalse(status["automatic_signals_active"])
            self.assertIn("dataset_learning", status)
            runtime.candle_store.close()

    def test_paper_only_signals_are_never_auto_posted_to_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(data_dir=root / "data")
            service = AutomationService(runtime)
            service.telegram = mock.Mock()
            service.telegram.configured = True
            service.telegram.send_message = mock.Mock()
            service._telegram_auto_post_enabled = True
            service._latest_auto_signal = {
                "signal_id": "paper-1",
                "side": "BUY",
                "source": "IMPORTED CSV",
                "is_live": False,
            }
            service._maybe_post_automatic_signal()
            service.telegram.send_message.assert_not_called()
            self.assertIn("Paper-only", service._telegram_last_skip_reason or "")
            runtime.candle_store.close()


if __name__ == "__main__":
    unittest.main()
