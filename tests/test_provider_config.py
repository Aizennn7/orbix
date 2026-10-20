from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch
import unittest

from orbix.automation import AutomationService, FinnhubEconomicCalendar, TwelveDataCsvProvider
from orbix.provider_config import ProviderConfigStore, mask_secret
from orbix.runtime import Runtime
from orbix.telegram import TelegramError, TelegramNotifier


EMPTY_KEYS = {
    "ORBIX_TWELVE_DATA_API_KEY": "",
    "ORBIX_FINNHUB_API_KEY": "",
    "ORBIX_TELEGRAM_BOT_TOKEN": "",
    "ORBIX_TELEGRAM_CHAT_ID": "",
}


class ProviderConfigStoreTests(unittest.TestCase):
    def test_missing_file_reports_nothing_configured(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = ProviderConfigStore(folder)
            status = store.status()
        self.assertFalse(status["stored"])
        self.assertTrue(all(not item["configured"] for item in status["fields"].values()))
        self.assertTrue(all(not item["configured"] for item in status["providers"].values()))

    def test_saved_keys_persist_and_are_masked(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, EMPTY_KEYS, clear=False):
            store = ProviderConfigStore(folder)
            store.update(
                {
                    "twelve_data_api_key": "td-secret-1234",
                    "finnhub_api_key": "fh-secret-9876",
                    "telegram_bot_token": "12345:bot-token-abcd",
                    "telegram_chat_id": "-100777",
                }
            )
            path = Path(folder) / "provider_settings.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(payload["keys"]["twelve_data_api_key"], "td-secret-1234")

            reloaded = ProviderConfigStore(folder)
            status = reloaded.status()
            serialized = json.dumps(status)
        self.assertTrue(status["stored"])
        self.assertTrue(status["providers"]["market"]["configured"])
        self.assertEqual(status["providers"]["market"]["source"], "web")
        self.assertEqual(status["providers"]["market"]["masked"], "••••1234")
        self.assertTrue(status["providers"]["calendar"]["configured"])
        self.assertTrue(status["providers"]["telegram"]["configured"])
        self.assertNotIn("td-secret-1234", serialized)
        self.assertNotIn("bot-token-abcd", serialized)
        self.assertNotIn("-100777", serialized)

    def test_environment_wins_and_cannot_be_replaced_from_the_web(self) -> None:
        env = {**EMPTY_KEYS, "ORBIX_TWELVE_DATA_API_KEY": "env-market-key"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=False):
            store = ProviderConfigStore(folder)
            with self.assertRaises(ValueError):
                store.update({"twelve_data_api_key": "web-market-key"})
            status = store.status()
        self.assertEqual(status["fields"]["twelve_data_api_key"]["source"], "environment")
        self.assertTrue(status["providers"]["market"]["configured"])
        self.assertEqual(status["providers"]["market"]["masked"], "••••-key")

    def test_clearing_removes_saved_keys_but_not_environment_keys(self) -> None:
        env = {**EMPTY_KEYS, "ORBIX_FINNHUB_API_KEY": "env-calendar-key"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=False):
            store = ProviderConfigStore(folder)
            store.update({"twelve_data_api_key": "web-key"})
            store.clear()
            status = store.status()
        self.assertFalse(status["fields"]["twelve_data_api_key"]["configured"])
        self.assertEqual(status["fields"]["finnhub_api_key"]["source"], "environment")
        self.assertTrue(status["providers"]["calendar"]["configured"])

    def test_settings_file_can_be_relocated(self) -> None:
        target = Path("relocated") / "provider_settings.json"
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ,
            {**EMPTY_KEYS, "ORBIX_PROVIDER_SETTINGS_FILE": str(Path(folder) / target)},
            clear=False,
        ):
            store = ProviderConfigStore(folder)
            store.update({"twelve_data_api_key": "relocated-key"})
            self.assertTrue((Path(folder) / target).is_file())
        self.assertEqual(ProviderConfigStore(Path(folder) / "unused").path.name, "provider_settings.json")

    def test_empty_test_results_do_not_create_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, EMPTY_KEYS, clear=False):
            store = ProviderConfigStore(folder)
            store.record_tests({"market": {"accessible": None, "message": ""}})
            self.assertFalse((Path(folder) / "provider_settings.json").exists())

    def test_key_input_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, EMPTY_KEYS, clear=False):
            store = ProviderConfigStore(folder)
            with self.assertRaises(ValueError):
                store.update({"twelve_data_api_key": "x" * 513})
            with self.assertRaises(ValueError):
                store.update({"finnhub_api_key": "line\nbreak"})
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret("abcd"), "••••")
        self.assertEqual(mask_secret("abcdef"), "••••cdef")

    def test_recorded_test_result_survives_and_is_dropped_on_key_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, EMPTY_KEYS, clear=False):
            store = ProviderConfigStore(folder)
            store.update({"twelve_data_api_key": "web-key"})
            store.record_tests({"market": {"accessible": True, "message": "reachable"}})
            present = ProviderConfigStore(folder).status()["providers"]["market"]
            store.update({"twelve_data_api_key": "another-key"})
            dropped = store.status()["providers"]["market"]
        self.assertTrue(present["accessible"])
        self.assertEqual(present["message"], "reachable")
        self.assertIsNone(dropped["accessible"])


class ProviderProbeTests(unittest.TestCase):
    def test_twelve_data_probe_reports_success_and_rejection(self) -> None:
        provider = TwelveDataCsvProvider("key")
        with patch(
            "orbix.automation._read_url",
            return_value=(b"timestamp,open,high,low,close,volume\n2026-10-20T10:00:00Z,1,1,1,1,0\n", "text/csv"),
        ):
            self.assertTrue(provider.probe()["accessible"])
        with patch(
            "orbix.automation._read_url",
            return_value=(json.dumps({"code": 401, "message": "Invalid API key"}).encode(), "application/json"),
        ):
            rejected = provider.probe()
        self.assertFalse(rejected["accessible"])
        self.assertIn("Invalid API key", rejected["message"])

    def test_finnhub_probe_requires_a_calendar_list(self) -> None:
        calendar = FinnhubEconomicCalendar("key")
        with patch(
            "orbix.automation._read_url",
            return_value=(json.dumps({"economicCalendar": []}).encode(), "application/json"),
        ):
            self.assertTrue(calendar.probe()["accessible"])
        with patch(
            "orbix.automation._read_url",
            return_value=(json.dumps({"error": "You don't have access to this resource."}).encode(), "application/json"),
        ):
            rejected = calendar.probe()
        self.assertFalse(rejected["accessible"])
        self.assertIn("don't have access", rejected["message"])

    def test_telegram_probe_rejects_a_bot_without_channel_rights(self) -> None:
        notifier = TelegramNotifier("123:token", "-100777")

        def fake_call(method: str, body: dict, label: str) -> dict:
            if method == "getMe":
                return {"ok": True, "result": {"id": 42, "username": "orbix_bot"}}
            if method == "getChat":
                return {"ok": True, "result": {"type": "channel", "title": "Orbix Signals"}}
            if method == "getChatMember":
                return {"ok": True, "result": {"status": "member"}}
            raise AssertionError(method)

        with patch.object(TelegramNotifier, "_call", side_effect=fake_call):
            result = notifier.probe()
        self.assertFalse(result["accessible"])
        self.assertIn("administrator", result["message"])

    def test_telegram_probe_reports_a_reachable_bot(self) -> None:
        notifier = TelegramNotifier("123:token", "-100777")

        def fake_call(method: str, body: dict, label: str) -> dict:
            if method == "getMe":
                return {"ok": True, "result": {"id": 42, "username": "orbix_bot"}}
            if method == "getChat":
                return {"ok": True, "result": {"type": "channel", "title": "Orbix Signals"}}
            if method == "getChatMember":
                return {"ok": True, "result": {"status": "administrator", "can_post_messages": True}}
            raise AssertionError(method)

        with patch.object(TelegramNotifier, "_call", side_effect=fake_call):
            result = notifier.probe()
        self.assertTrue(result["accessible"])
        self.assertIn("@orbix_bot", result["message"])
        self.assertIsNone(TelegramNotifier(None, None).probe()["accessible"])

    def test_telegram_probe_reports_telegram_errors_without_the_token(self) -> None:
        notifier = TelegramNotifier("123:secret-token", "-100777")
        with patch.object(
            TelegramNotifier,
            "_call",
            side_effect=TelegramError("Telegram chat check rejected (HTTP 400): chat not found"),
        ):
            result = notifier.probe()
        self.assertFalse(result["accessible"])
        self.assertNotIn("secret-token", result["message"])


class LiveConfigurationTests(unittest.TestCase):
    def test_saved_keys_are_injected_into_the_running_service(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, EMPTY_KEYS, clear=False):
            runtime = Runtime(folder)
            service = AutomationService(runtime)
            self.assertFalse(service.market_provider.configured)
            self.assertFalse(service.calendar.configured)
            self.assertFalse(service.telegram.configured)
            store = ProviderConfigStore(folder)
            store.update(
                {
                    "twelve_data_api_key": "market-key",
                    "finnhub_api_key": "calendar-key",
                    "telegram_bot_token": "123:token",
                    "telegram_chat_id": "-100777",
                }
            )
            service.apply_provider_config(store.effective())
            calendar_object = service.calendar
            service.apply_provider_config(store.effective())
        self.assertTrue(service.market_provider.configured)
        self.assertTrue(service.calendar.configured)
        self.assertIs(service.calendar, calendar_object)
        self.assertTrue(service.telegram.configured)
        self.assertEqual(service.telegram.chat_id, "-100777")
        self.assertFalse(service.calendar.status()["connected"])


if __name__ == "__main__":
    unittest.main()
