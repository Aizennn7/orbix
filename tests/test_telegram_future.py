from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from orbix.automation import AutomationService
from orbix.domain import Candle, Signal
from orbix.future import _replay, build_future_plan
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

    def __init__(self, edit_fails: bool = False) -> None:
        self.messages: list[str] = []
        self.edits: list[tuple[int, str]] = []
        self.edit_fails = edit_fails

    def send_message(self, text: str) -> dict:
        self.messages.append(text)
        return {"message_id": 100 + len(self.messages), "chat_id": self.chat_id, "edited": False}

    def edit_message_text(self, message_id: int, text: str) -> dict:
        if self.edit_fails:
            raise TelegramError("message can't be edited")
        self.edits.append((int(message_id), text))
        return {"message_id": int(message_id), "chat_id": self.chat_id, "edited": True}


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


class TelegramGuardrailTests(unittest.TestCase):
    def _automation(self, folder: str, *, edit_fails: bool = False):
        automation = AutomationService(Runtime(folder))
        automation.telegram = FakeTelegram(edit_fails=edit_fails)
        return automation

    def test_daily_cap_blocks_automatic_posting(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = self._automation(folder)
            fake = automation.telegram
            now = datetime.now(UTC)
            automation._telegram_auto_post_enabled = True
            automation._telegram_daily_date = now.date().isoformat()
            automation._telegram_daily_count = automation.telegram_daily_cap
            automation._latest_auto_signal = sample_signal()
            automation._maybe_post_automatic_signal()
        self.assertFalse(fake.messages)
        self.assertIn("Daily cap", automation._telegram_last_skip_reason or "")
        self.assertNotIn("abc123", automation._telegram_signal_ids)

    def test_cooldown_blocks_then_allows(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = self._automation(folder)
            automation._telegram_auto_post_enabled = True
            now = datetime.now(UTC)
            automation._telegram_daily_date = now.date().isoformat()
            automation._telegram_daily_count = 0
            automation._telegram_last_sent_at = now
            blocked, reason = automation._telegram_auto_gate(now)
            allowed, _ = automation._telegram_auto_gate(now + timedelta(seconds=automation.telegram_cooldown_seconds + 1))
        self.assertFalse(blocked)
        self.assertIn("Cooldown", reason or "")
        self.assertTrue(allowed)

    def test_manual_send_refuses_past_daily_cap_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = self._automation(folder)
            fake = automation.telegram
            now = datetime.now(UTC)
            automation._telegram_daily_date = now.date().isoformat()
            automation._telegram_daily_count = automation.telegram_daily_cap
            automation._auto_signal_history = [sample_signal()]
            with self.assertRaises(TelegramError) as context:
                automation.send_telegram_batch(limit=1)
            forced = automation.send_telegram_batch(limit=1, force=True)
        self.assertIn("Daily cap", str(context.exception))
        self.assertEqual(forced["status"], "sent")
        self.assertEqual(len(fake.messages), 1)

    def test_result_edits_the_original_message(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = self._automation(folder)
            fake = automation.telegram
            signal = Signal(
                signal_id="sig-edit",
                pair="EUR/USD",
                strategy="orbix_confluence",
                side="BUY",
                entry_price=1.085,
                generated_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
                expires_at=datetime(2026, 10, 20, 12, 1, tzinfo=UTC),
                probability_up=0.61,
                source="IMPORTED CSV",
                reason="Orbix Confluence Gate 6/6: trend, momentum.",
                entry_time=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
            )
            automation.runtime.ledger.record(signal, {}, dataset_id="d1")
            automation._telegram_signal_ids = ["sig-edit"]
            automation._telegram_message_ids = {"sig-edit": 42}
            automation._post_outcome_results([{"signal_id": "sig-edit", "outcome": "WIN", "learned": True}])
        self.assertEqual(len(fake.edits), 1)
        message_id, edit_text = fake.edits[0]
        self.assertEqual(message_id, 42)
        self.assertIn("RESULT", edit_text)
        self.assertIn("Edited in place", edit_text)
        self.assertFalse(fake.messages)
        self.assertIn("sig-edit", automation._telegram_edited_result_ids)

    def test_result_falls_back_to_a_new_message_when_edit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, TELEGRAM_ENV):
            automation = self._automation(folder, edit_fails=True)
            fake = automation.telegram
            signal = Signal(
                signal_id="sig-fallback",
                pair="EUR/USD",
                strategy="orbix_confluence",
                side="SELL",
                entry_price=1.085,
                generated_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
                expires_at=datetime(2026, 10, 20, 12, 1, tzinfo=UTC),
                probability_up=0.39,
                source="IMPORTED CSV",
                reason="Orbix Confluence Gate 6/6: trend, momentum.",
                entry_time=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
            )
            automation.runtime.ledger.record(signal, {}, dataset_id="d1")
            automation._telegram_signal_ids = ["sig-fallback"]
            automation._telegram_message_ids = {"sig-fallback": 77}
            automation._post_outcome_results([{"signal_id": "sig-fallback", "outcome": "LOSS", "learned": False}])
        self.assertEqual(len(fake.messages), 1)
        self.assertIn("PAPER RESULT", fake.messages[0])
        self.assertEqual(fake.edits, [])
        self.assertNotIn("sig-fallback", automation._telegram_edited_result_ids)


class WideEnsemble:
    """Stub ensemble that always reports a spread wider than the limit."""

    ready = True
    members = [object(), object(), object()]

    def predict(self, features) -> tuple[float, float]:
        return 0.5, 0.4


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

    def test_replay_applies_the_live_uncertainty_gate(self) -> None:
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
            runtime.get_or_train_model("GBP/USD")
            with patch.object(runtime, "get_ensemble", return_value=WideEnsemble()):
                replay = _replay(runtime, 3)
        self.assertTrue(replay)
        self.assertTrue(all(item["side"] == "NO SIGNAL" for item in replay))
        self.assertTrue(all("stability limit" in item["reason"] for item in replay))

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
