from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from orbix.automation import AutomationService
from orbix.domain import Candle
from orbix.future import build_future_plan
from orbix.runtime import Runtime
from orbix.telegram import TelegramError, TelegramNotifier


UTC = timezone.utc
TELEGRAM_ENV = {
    "ORBIX_TELEGRAM_BOT_TOKEN": "",
    "ORBIX_TELEGRAM_CHAT_ID": "",
    "ORBIX_TELEGRAM_AUTO_POST": "false",
}


class FakeTelegram:
    configured = True
    chat_id = "@orbix_test"

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str) -> dict:
        self.messages.append(text)
        return {"message_id": 100 + len(self.messages), "chat_id": self.chat_id}


def sample_signal(**overrides) -> dict:
    signal = {
        "signal_id": "abc123",
        "pair": "EUR/USD",
        "side": "BUY",
        "entry_time": "2026-10-20T12:00:00+00:00",
        "prediction_time": "2026-10-20T12:01:00+00:00",
        "probability_up": 0.61,
        "source": "IMPORTED CSV",
        "reason": "Orbix Confluence Gate 6/6: trend, momentum.",
        "horizon": 1,
    }
    signal.update(overrides)
    return signal


class TelegramFormattingTests(unittest.TestCase):
    def test_signal_message_is_labelled_and_has_no_accuracy_promise(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            automation.telegram_timezone_name = "Asia/Kolkata"
            automation.telegram_timezone = ZoneInfo("Asia/Kolkata")
            text = automation.telegram_message_text(sample_signal())
        self.assertIn("CALL (BUY)", text)
        self.assertIn("17:30 Asia/Kolkata", text)
        self.assertIn("No order was placed", text)
        self.assertIn("no accuracy", text.lower())
        self.assertIn("not calibrated", text)

    def test_batch_message_uses_m1_rows_and_flags_demo(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            automation.telegram_timezone_name = "Asia/Kolkata"
            automation.telegram_timezone = ZoneInfo("Asia/Kolkata")
            text = automation.telegram_batch_text(
                [
                    sample_signal(signal_id="one", side="BUY"),
                    sample_signal(signal_id="two", side="SELL", source="SYNTHETIC DEMO"),
                ],
                demo_included=True,
            )
        self.assertIn("M1; EUR/USD; 17:30; CALL", text)
        self.assertIn("M1; EUR/USD; 17:30; PUT", text)
        self.assertIn("SYNTHETIC DEMO", text)
        self.assertIn("no accuracy", text.lower())

    def test_result_message_reports_outcome_and_learning_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            text = automation.telegram_result_text(
                {
                    "pair": "EUR/USD",
                    "side": "SELL",
                    "entry_time": "2026-10-20T12:00:00+00:00",
                    "expires_at": "2026-10-20T12:01:00+00:00",
                    "outcome": "WIN",
                    "exit_price": 1.08412,
                },
                learned=True,
            )
        self.assertIn("PUT (SELL)", text)
        self.assertIn("WIN", text)
        self.assertIn("Model updated once after expiry", text)
        self.assertIn("Not a live trade", text)


class TelegramDeliveryTests(unittest.TestCase):
    def test_unconfigured_sender_refuses_without_calling_network(self) -> None:
        notifier = TelegramNotifier(None, None)
        self.assertFalse(notifier.configured)
        with self.assertRaises(TelegramError) as context:
            notifier.send_message("hello")
        self.assertIn("ORBIX_TELEGRAM_BOT_TOKEN", str(context.exception))
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            with self.assertRaises(TelegramError):
                automation.send_telegram_batch()

    def test_demo_signal_is_blocked_unless_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            fake = FakeTelegram()
            automation.telegram = fake
            automation._auto_signal_history = [sample_signal(source="SYNTHETIC DEMO")]
            blocked = automation.send_telegram_batch(limit=1)
            self.assertEqual(blocked["status"], "nothing_to_send")
            self.assertFalse(fake.messages)
            sent = automation.send_telegram_batch(limit=1, include_demo=True)
            self.assertEqual(sent["status"], "sent")
            self.assertEqual(len(fake.messages), 1)
            self.assertIn("SYNTHETIC DEMO", fake.messages[0])

    def test_duplicate_signal_is_not_sent_twice(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            fake = FakeTelegram()
            automation.telegram = fake
            automation._auto_signal_history = [sample_signal()]
            first = automation.send_telegram_batch(limit=1)
            second = automation.send_telegram_batch(limit=1)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "nothing_to_send")
        self.assertEqual(len(fake.messages), 1)

    def test_auto_post_toggle_requires_configuration_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            with self.assertRaises(TelegramError):
                automation.set_telegram_auto_post(True)
            automation.telegram = FakeTelegram()
            state = automation.set_telegram_auto_post(True)
            self.assertTrue(state["auto_post_enabled"])
            reloaded = AutomationService(automation.runtime)
            self.assertTrue(reloaded._telegram_auto_post_enabled)

    def test_dry_run_never_calls_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = AutomationService(Runtime(folder))
            fake = FakeTelegram()
            automation.telegram = fake
            automation._auto_signal_history = [sample_signal()]
            preview = automation.send_telegram_batch(limit=1, dry_run=True)
        self.assertEqual(preview["status"], "preview")
        self.assertIn("ORBIX", preview["text"])
        self.assertFalse(fake.messages)


class FuturePlanTests(unittest.TestCase):
    def test_future_plan_never_invents_a_direction_and_labels_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            runtime = Runtime(folder)
            automation = AutomationService(runtime)
            plan = build_future_plan(runtime, automation, count=4)
        self.assertEqual(len(plan["windows"]), 4)
        for window in plan["windows"]:
            self.assertIsNone(window["direction"])
            self.assertEqual(window["status"], "BLOCKED")
        self.assertFalse(plan["readiness"]["ready"])
        self.assertTrue(plan["readiness"]["reasons"])
        self.assertIn("No pair is more likely", plan["focus"]["accuracy_note"])
        self.assertEqual(plan["focus"]["pair"], "EUR/USD")
        self.assertTrue(any(item["active"] for item in plan["alternatives"]))
        self.assertLessEqual(len(plan["replay"]), 4)
        self.assertTrue(all(item["replayed"] for item in plan["replay"]))
        self.assertIsNotNone(plan["evidence"])
        self.assertEqual(plan["data"]["source"], "SYNTHETIC DEMO")

    def test_future_plan_uses_the_active_imported_pair(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            runtime = Runtime(folder)
            start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
            rows = ["timestamp,open,high,low,close,volume"]
            for index in range(180):
                stamp = (start + timedelta(minutes=index)).isoformat()
                close = 1.1 + (index % 7) * 0.00005
                rows.append(f"{stamp},{close},{close + 0.0002},{close - 0.0002},{close},{10}")
            path = Path(folder) / "gbp.csv"
            path.write_text("\n".join(rows), encoding="utf-8")
            runtime.import_csv_file(path, "GBP/USD")
            automation = AutomationService(runtime)
            plan = build_future_plan(runtime, automation, count=3)
        self.assertEqual(plan["focus"]["pair"], "GBP/USD")
        self.assertEqual(plan["data"]["source"], "IMPORTED CSV")
        active = [item for item in plan["alternatives"] if item["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["pair"], "GBP/USD")

    def test_future_windows_are_one_minute_apart(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            runtime = Runtime(folder)
            automation = AutomationService(runtime)
            plan = build_future_plan(runtime, automation, count=3)
        entries = [datetime.fromisoformat(window["entry_time"]) for window in plan["windows"]]
        gaps = [(b - a).total_seconds() for a, b in zip(entries, entries[1:])]
        self.assertEqual(gaps, [60.0, 60.0])


if __name__ == "__main__":
    unittest.main()
