from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbix.automation import AutomationService, FinnhubEconomicCalendar, TwelveDataCsvProvider
from orbix.domain import Candle, format_datetime
from orbix.runtime import Runtime


UTC = timezone.utc


class FakeMarketProvider:
    configured = True

    def __init__(self, candles: list[Candle]):
        self.candles = candles
        self.calls = 0

    def fetch_candles(self, pair: str, outputsize: int = 500) -> list[Candle]:
        self.calls += 1
        return self.candles


class AutomationTests(unittest.TestCase):
    def test_twelve_data_csv_accepts_datetime_header(self) -> None:
        start = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
        rows = ["datetime,open,high,low,close,volume"]
        for index in range(120):
            stamp = (start + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(f"{stamp},1.1,1.101,1.099,1.1005,25")
        provider = TwelveDataCsvProvider("server-key")
        with patch("orbix.automation._read_url", return_value=("\n".join(rows).encode(), "text/csv")):
            candles = provider.fetch_candles("EUR/USD")
        self.assertEqual(len(candles), 120)
        self.assertEqual(candles[0].timestamp, start)
        self.assertEqual(candles[-1].timestamp, start + timedelta(minutes=119))

    def test_high_impact_news_calendar_blocks_pair_inside_blackout(self) -> None:
        now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        payload = {
            "economicCalendar": [
                {"country": "US", "event": "CPI year over year", "impact": "high", "time": "2026-10-20 12:10:00", "actual": None, "estimate": 2.5, "prev": 2.7, "unit": "%"},
                {"country": "EU", "event": "Euro confidence survey", "impact": "low", "time": "2026-10-20 12:20:00", "unit": "index"},
            ]
        }
        calendar = FinnhubEconomicCalendar("server-key", blackout_before_minutes=15, blackout_after_minutes=30)
        with patch("orbix.automation._read_url", return_value=(json.dumps(payload).encode(), "application/json")):
            self.assertEqual(calendar.refresh(now), 2)
        us_pair = calendar.evaluate("EUR/USD", now)
        self.assertEqual(us_pair["status"], "BLACKOUT")
        self.assertIn("CPI", us_pair["active_event"]["event"])
        unrelated_pair = calendar.evaluate("EUR/JPY", now)
        self.assertEqual(unrelated_pair["status"], "CLEAR")
        self.assertFalse(unrelated_pair["events"])

    def test_unconfigured_calendar_is_unknown_not_clear(self) -> None:
        calendar = FinnhubEconomicCalendar(None)
        self.assertEqual(calendar.evaluate("EUR/USD")["status"], "UNAVAILABLE")
        with tempfile.TemporaryDirectory() as folder:
            status = AutomationService(Runtime(folder)).status()
            self.assertFalse(status["automatic_signals_active"])
            self.assertEqual(status["calendar"]["guard"]["status"], "UNAVAILABLE")
            self.assertFalse(status["market"]["connected"])
            self.assertFalse(status["market"]["active_dataset_is_provider"])

    def test_preflight_lists_blockers_and_warmup(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ,
            {"ORBIX_TWELVE_DATA_API_KEY": "", "ORBIX_FINNHUB_API_KEY": ""},
            clear=False,
        ):
            runtime = Runtime(folder)
            service = AutomationService(runtime)
            report = service.preflight()
        self.assertFalse(report["ready"])
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["market_provider"]["status"], "blocked")
        self.assertEqual(checks["calendar"]["status"], "blocked")
        self.assertEqual(checks["scheduler"]["status"], "ready")
        self.assertEqual(checks["telegram"]["status"], "warning")
        self.assertFalse(checks["telegram"]["required"])
        self.assertEqual(report["warmup"]["resolved_outcomes"], 0)
        self.assertEqual(report["warmup"]["to_calibration"], 60)
        self.assertIn("not a performance forecast", report["note"])

    def test_datetime_header_is_accepted_by_candle_store_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(folder)
            start = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
            rows = ["datetime,open,high,low,close,volume"]
            for index in range(120):
                stamp = (start + timedelta(minutes=index)).isoformat()
                rows.append(f"{stamp},1.1,1.101,1.099,1.1005,10")
            csv_path = Path(folder) / "datetime.csv"
            csv_path.write_text("\n".join(rows), encoding="utf-8")
            result = runtime.import_csv_file(csv_path, "EUR/USD")
            self.assertEqual(result["count"], 120)
            self.assertEqual(runtime.candles[0].timestamp, start)

    def test_market_sync_imports_only_closed_bars_and_preserves_archive(self) -> None:
        fixed_now = datetime(2026, 10, 20, 12, 0, 10, tzinfo=UTC)
        first = fixed_now.replace(second=0, microsecond=0) - timedelta(minutes=120)
        candles = [
            Candle(first + timedelta(minutes=index), 1.1, 1.101, 1.099, 1.1002 + index * 1e-7, 20.0)
            for index in range(121)
        ]
        # The final row opens at 12:00 and is still incomplete at the fixed clock.
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(folder)
            automation = AutomationService(runtime)
            automation.market_provider = FakeMarketProvider(candles)
            result = automation.sync_market(force_activate=True, now=fixed_now)
            self.assertEqual(result["status"], "synced")
            self.assertEqual(result["candles_added"], 120)
            self.assertEqual(runtime.source, "IMPORTED CSV")
            self.assertEqual(runtime.candle_count, 120)
            self.assertEqual(runtime.candles[-1].timestamp, fixed_now.replace(second=0, microsecond=0) - timedelta(minutes=1))
            datasets = runtime.candle_store.list_datasets(limit=10)
            self.assertEqual(len(datasets), 1)
            self.assertEqual(datasets[0]["pair"], "EUR/USD")

    def test_manual_import_is_not_overwritten_by_background_feed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(folder)
            csv_path = Path(folder) / "manual.csv"
            start = datetime(2026, 10, 18, tzinfo=UTC)
            lines = ["timestamp,open,high,low,close,volume"]
            for index in range(120):
                timestamp = (start + timedelta(minutes=index)).isoformat()
                lines.append(f"{timestamp},1.1,1.101,1.099,1.1001,5")
            csv_path.write_text("\n".join(lines), encoding="utf-8")
            runtime.import_csv_file(csv_path, "EUR/USD")
            automation = AutomationService(runtime)
            fake = FakeMarketProvider([])
            automation.market_provider = fake
            result = automation.sync_market(force_activate=False)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(fake.calls, 0)
            self.assertEqual(runtime.candle_count, 120)

    def test_user_pause_persists_until_explicit_provider_sync(self) -> None:
        fixed_now = datetime(2026, 10, 20, 12, 0, 10, tzinfo=UTC)
        first = fixed_now.replace(second=0, microsecond=0) - timedelta(minutes=120)
        candles = [
            Candle(first + timedelta(minutes=index), 1.1, 1.101, 1.099, 1.1002 + index * 1e-7, 20.0)
            for index in range(121)
        ]
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(folder)
            automation = AutomationService(runtime)
            fake = FakeMarketProvider(candles)
            automation.market_provider = fake
            automation.pause_market_feed()
            marker = Path(folder) / "automation_feed.json"
            self.assertTrue(json.loads(marker.read_text(encoding="utf-8"))["paused_by_user"])

            paused = automation.sync_market(force_activate=False, now=fixed_now)
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(fake.calls, 0)

            resumed = automation.sync_market(force_activate=True, now=fixed_now)
            self.assertEqual(resumed["status"], "synced")
            self.assertEqual(fake.calls, 1)
            self.assertFalse(json.loads(marker.read_text(encoding="utf-8"))["paused_by_user"])


class DisciplineAutomationTests(unittest.TestCase):
    def make_runtime(self, root: Path, last_bar: datetime) -> Runtime:
        history = root / "history.csv"
        first = last_bar - timedelta(minutes=319)
        with history.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for index in range(320):
                stamp = first + timedelta(minutes=index)
                opening = 1.1 + 0.00002 * index
                close = opening + (0.00004 if index % 3 else -0.00003)
                writer.writerow([
                    format_datetime(stamp),
                    format(opening, ".12f"),
                    format(max(opening, close) + 0.00003, ".12f"),
                    format(min(opening, close) - 0.00003, ".12f"),
                    format(close, ".12f"),
                    100,
                ])
        runtime = Runtime(data_dir=root / "data")
        runtime.import_csv_file(history, "EUR/USD")
        return runtime

    def test_outside_the_session_no_signal_is_generated_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            last_bar = datetime(2026, 10, 21, 2, 59, tzinfo=UTC)  # Wednesday, Asia
            runtime = self.make_runtime(root, last_bar)
            service = AutomationService(runtime)
            service.strategy_id = "orbix_confluence"
            with patch.object(runtime, "generate_signal") as spy:
                service._analyze_new_bar(
                    runtime.candles[-1],
                    datetime(2026, 10, 21, 3, 0, tzinfo=UTC),
                    is_live=False,
                    require_news=False,
                    max_delay=120,
                )
            spy.assert_not_called()
            self.assertIn("outside my trading session", service._last_auto_status)
            self.assertEqual(runtime.ledger.list_recent(), [])
            # Resting outside the session is not a per-bar setup decision, so it
            # must not flood the abstention ledger.
            self.assertEqual(runtime.ledger.abstention_summary()["totals"]["all_time"], 0)
            runtime.candle_store.close()

    def test_inside_the_session_the_trade_gets_a_grade_and_a_journal_line(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            last_bar = datetime(2026, 10, 21, 9, 59, tzinfo=UTC)  # London
            runtime = self.make_runtime(root, last_bar)
            service = AutomationService(runtime)
            service.strategy_id = "orbix_confluence"
            entry = last_bar + timedelta(minutes=1)
            fake = {
                "signal_id": "discipline-1",
                "pair": "EUR/USD",
                "strategy": "orbix_confluence",
                "side": "BUY",
                "entry_price": 1.1,
                "entry_time": format_datetime(entry),
                "prediction_time": format_datetime(entry + timedelta(minutes=1)),
                "probability_up": 0.60,
                "uncertainty": 0.02,
                "source": "IMPORTED CSV",
                "reason": "test setup",
                "is_live": False,
                "paper_only": True,
            }
            with patch.object(runtime, "generate_signal", return_value=dict(fake)):
                service._analyze_new_bar(
                    runtime.candles[-1],
                    entry,
                    is_live=False,
                    require_news=False,
                    max_delay=120,
                )
            payload = service._latest_auto_signal
            self.assertEqual(payload["side"], "BUY")
            self.assertEqual(payload["discipline"]["grade"], "A")
            self.assertGreaterEqual(payload["discipline"]["conviction"], 0.7)
            self.assertEqual(payload["discipline"]["budget_max"], 6)
            journal = runtime.ledger.list_engine_events(
                10, dataset_id=runtime.dataset_id(), kinds=("journal",)
            )
            self.assertEqual(len(journal), 1)
            self.assertIn("Took BUY", journal[0]["detail"])
            runtime.candle_store.close()


if __name__ == "__main__":
    unittest.main()
