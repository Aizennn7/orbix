from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch

from orbix.abstentions import classify_reason, hour_bucket, session_for
from orbix.automation import AutomationService
from orbix.ledger import PaperLedger
from orbix.runtime import Runtime


UTC = timezone.utc
TELEGRAM_ENV = {
    "ORBIX_TELEGRAM_BOT_TOKEN": "",
    "ORBIX_TELEGRAM_CHAT_ID": "",
    "ORBIX_TELEGRAM_AUTO_POST": "false",
}


class ReasonClassificationTests(unittest.TestCase):
    def test_known_reasons_map_to_stable_codes(self) -> None:
        cases = {
            "Orbix Confluence Gate abstains: one-minute volatility is outside its pre-set operating band.": "volatility_band",
            "Orbix Confluence Gate abstains: buy rules passed 5/6, but the prequential model vetoed disagreement. Model score is uncalibrated.": "model_veto",
            "Orbix Confluence Gate abstains: strongest side passed 4/6 checks; at least 5/6 plus trend and ADX alignment are required.": "score_below_threshold",
            "High-impact event blackout: CPI year over year (US) is inside the configured window.": "news_blackout",
            "Skipped stale bar: its entry boundary passed by more than 10 seconds.": "stale_bar",
            "This strategy already has a signal recorded for the current entry time; duplicate suppressed.": "duplicate_suppressed",
            "Economic calendar not configured; news risk cannot be cleared.": "provider_unavailable",
            "The model is not ready; wait for more data.": "model_not_ready",
            "Price is still inside the recent 20-candle range.": "inside_range",
            "Something unexpected happened.": "other",
            "": "other",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(classify_reason(reason), expected)

    def test_session_boundaries_use_utc_hours(self) -> None:
        cases = {
            0: "Asian",
            6: "Asian",
            7: "London",
            11: "London",
            12: "London/NY overlap",
            15: "London/NY overlap",
            16: "New York",
            20: "New York",
            21: "Rollover",
            22: "Rollover",
            23: "Asian",
        }
        for hour, expected in cases.items():
            with self.subTest(hour=hour):
                self.assertEqual(session_for(hour), expected)
                moment = datetime(2026, 10, 20, hour, 30, tzinfo=UTC)
                self.assertEqual(session_for(moment), expected)
                self.assertEqual(hour_bucket(moment), hour)

    def test_iso_string_sessions_are_converted_to_utc(self) -> None:
        # 18:30 in Asia/Kolkata is 13:00 UTC, inside the London/NY overlap.
        self.assertEqual(session_for("2026-10-20T18:30:00+05:30"), "London/NY overlap")
        self.assertEqual(session_for("not-a-date"), "Unknown")


class AbstentionLedgerTests(unittest.TestCase):
    def test_duplicate_abstention_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            ledger = PaperLedger(f"{folder}/ledger.sqlite3")
            entry = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
            first = ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="Orbix Confluence Gate abstains: strongest side passed 3/6 checks.",
                entry_time=entry,
                source="IMPORTED CSV",
                dataset_id="dataset-a",
            )
            second = ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="Orbix Confluence Gate abstains: strongest side passed 3/6 checks.",
                entry_time=entry,
                source="IMPORTED CSV",
                dataset_id="dataset-a",
            )
            different_reason = ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="Orbix Confluence Gate abstains: one-minute volatility is outside its pre-set operating band.",
                entry_time=entry,
                source="IMPORTED CSV",
                dataset_id="dataset-a",
            )
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(different_reason)

    def test_summary_groups_by_session_hour_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            ledger = PaperLedger(f"{folder}/ledger.sqlite3")
            now = datetime(2026, 10, 20, 14, 5, tzinfo=UTC)
            ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="Orbix Confluence Gate abstains: strongest side passed 4/6 checks.",
                entry_time=datetime(2026, 10, 20, 13, 59, tzinfo=UTC),
                source="IMPORTED CSV",
                dataset_id="d1",
                now=now,
            )
            ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="High-impact event blackout: CPI (US).",
                entry_time=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
                source="IMPORTED CSV",
                dataset_id="d1",
                now=now,
            )
            ledger.record_abstention(
                pair="EUR/USD",
                strategy="orbix_confluence",
                reason="Skipped stale bar: its entry boundary passed by more than 10 seconds.",
                kind="operational",
                entry_time=datetime(2026, 10, 20, 14, 0, tzinfo=UTC),
                source="IMPORTED CSV",
                dataset_id="d1",
                now=now,
            )
            summary = ledger.abstention_summary(days=7, now=now)
        self.assertEqual(summary["gate"]["window"], 2)
        self.assertEqual(summary["operational"]["window"], 1)
        self.assertEqual(summary["totals"]["window"], 3)
        self.assertEqual(summary["decided_signals_window"], 0)
        self.assertEqual(summary["abstention_ratio"], 1.0)
        sessions = {row["session"]: row for row in summary["by_session"]}
        self.assertEqual(sessions["London"]["gate"], 1)
        self.assertEqual(sessions["London/NY overlap"]["gate"], 1)
        self.assertEqual(sessions["London/NY overlap"]["operational"], 1)
        hours = {row["hour"]: row for row in summary["by_hour"]}
        self.assertEqual(hours[8]["gate"], 1)
        self.assertEqual(hours[14]["operational"], 1)
        codes = {row["code"]: row["count"] for row in summary["by_code"]}
        self.assertEqual(codes["news_blackout"], 1)
        self.assertEqual(codes["stale_bar"], 1)
        self.assertEqual(len(summary["recent"]), 3)
        self.assertTrue(all("label" in row for row in summary["recent"]))

    def test_runtime_logs_gate_abstentions_but_not_as_signals(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(TELEGRAM_ENV):
            runtime = Runtime(folder)
            signal = runtime.generate_signal(
                "EUR/USD",
                "orbix_confluence",
                safety_filter_reason="High-impact event blackout: CPI year over year (US).",
            )
            summary = runtime.abstention_summary()
            recent_signals = runtime.ledger.list_recent(5)
        self.assertEqual(signal["side"], "NO SIGNAL")
        self.assertEqual(summary["gate"]["window"], 1)
        self.assertEqual(summary["by_code"][0]["code"], "news_blackout")
        self.assertEqual(recent_signals, [])

    def test_operational_skip_is_logged_when_calendar_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(TELEGRAM_ENV):
            runtime = Runtime(folder)
            automation = AutomationService(runtime)
            latest = runtime.candles[-1]
            entry = latest.timestamp + timedelta(minutes=1)
            automation._analyze_new_bar(latest, entry + timedelta(seconds=1))
            summary = runtime.abstention_summary()
        self.assertEqual(summary["operational"]["window"], 1)
        self.assertEqual(summary["by_code"][0]["code"], "provider_unavailable")
        self.assertIn("paused", summary["recent"][0]["reason"].lower())


if __name__ == "__main__":
    unittest.main()
