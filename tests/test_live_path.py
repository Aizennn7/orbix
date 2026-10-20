from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
from unittest.mock import patch
import unittest
from urllib.parse import parse_qs, urlparse

from orbix.automation import AutomationService
from orbix.runtime import Runtime


UTC = timezone.utc


class _ProviderHandler(BaseHTTPRequestHandler):
    """A local stand-in for Twelve Data and Finnhub; no external requests."""

    candles: list[dict] = []
    market_key = "test-market-key"
    calendar_key = "test-calendar-key"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/time_series":
            if query.get("apikey", [""])[0] != self.market_key:
                self._send(401, "application/json", json.dumps({"code": 401, "message": "Invalid API key"}).encode())
                return
            if query.get("symbol", [""])[0] != "EUR/USD":
                self._send(200, "application/json", json.dumps({"code": 400, "message": "Unknown symbol"}).encode())
                return
            lines = ["datetime,open,high,low,close,volume"]
            for candle in self.candles:
                lines.append(
                    f"{candle['timestamp'].isoformat()},{candle['open']},{candle['high']},"
                    f"{candle['low']},{candle['close']},0"
                )
            self._send(200, "text/csv", ("\n".join(lines) + "\n").encode())
            return
        if parsed.path == "/calendar/economic":
            if query.get("token", [""])[0] != self.calendar_key:
                self._send(401, "application/json", json.dumps({"error": "Invalid token"}).encode())
                return
            self._send(200, "application/json", json.dumps({"economicCalendar": []}).encode())
            return
        self._send(404, "text/plain", b"not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep test output quiet
        return


def _candles(end: datetime, count: int = 300) -> list[dict]:
    rows = []
    for index in range(count):
        stamp = end - timedelta(minutes=count - 1 - index)
        price = 1.08 + index * 0.00001
        rows.append({
            "timestamp": stamp,
            "open": round(price, 5),
            "high": round(price + 0.00004, 5),
            "low": round(price - 0.00004, 5),
            "close": round(price + 0.00001, 5),
        })
    return rows


class LiveProviderPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.now = datetime.now(UTC).replace(second=0, microsecond=0)
        _ProviderHandler.candles = _candles(self.now - timedelta(minutes=3))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _service(self, folder: str) -> AutomationService:
        with patch.dict(
            os.environ,
            {
                "ORBIX_TWELVE_DATA_API_KEY": _ProviderHandler.market_key,
                "ORBIX_FINNHUB_API_KEY": _ProviderHandler.calendar_key,
                "ORBIX_AUTO_SIGNALS": "true",
                "ORBIX_MAX_BAR_AGE_SECONDS": "300",
            },
            clear=False,
        ):
            runtime = Runtime(folder)
            service = AutomationService(runtime)
            service._analyze_new_bar = lambda *args, **kwargs: None
            return service

    def test_live_feed_and_calendar_make_automation_ready(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "orbix.automation.TWELVE_DATA_URL", f"{self.base_url}/time_series"
        ), patch("orbix.automation.FINNHUB_ECONOMIC_URL", f"{self.base_url}/calendar/economic"):
            service = self._service(folder)
            calendar = service.sync_calendar()
            self.assertEqual(calendar["status"], "connected")
            result = service.sync_market(force_activate=True)
            self.assertEqual(result["candles_added"], 300)
            self.assertEqual(result["status"], "synced")
            status = service.status()
        self.assertTrue(status["market"]["configured"])
        self.assertTrue(status["market"]["connected"])
        self.assertTrue(status["market"]["active_dataset_is_provider"])
        self.assertTrue(status["market"]["data_fresh"])
        self.assertTrue(status["calendar"]["connected"])
        self.assertEqual(status["calendar"]["guard"]["status"], "CLEAR")
        self.assertTrue(status["automatic_signals_active"])

    def test_saved_key_wakes_the_scheduler_and_appends_only_new_bars(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "orbix.automation.TWELVE_DATA_URL", f"{self.base_url}/time_series"
        ), patch("orbix.automation.FINNHUB_ECONOMIC_URL", f"{self.base_url}/calendar/economic"):
            service = self._service(folder)
            service.market_provider.api_key = ""
            service.calendar.api_key = ""
            service._next_market = float("inf")
            service._next_calendar = float("inf")
            service.apply_provider_config(
                {
                    "twelve_data_api_key": _ProviderHandler.market_key,
                    "finnhub_api_key": _ProviderHandler.calendar_key,
                }
            )
            self.assertEqual(service._next_market, 0.0)
            self.assertEqual(service._next_calendar, 0.0)

            service.sync_calendar()
            first = service.sync_market(force_activate=True)
            self.assertEqual(first["candles_added"], 300)
            self.assertGreater(service._next_market, 0.0)

            extra = _candles(self.now - timedelta(minutes=2), count=1)[0]
            _ProviderHandler.candles.append(extra)
            calls = []
            service._analyze_new_bar = lambda *args, **kwargs: calls.append(args)
            second = service.sync_market()
            self.assertEqual(second["candles_added"], 1)
            self.assertEqual(len(calls), 1)

            third = service.sync_market()
            self.assertEqual(third["candles_added"], 0)
            self.assertEqual(len(calls), 1)

    def test_restarted_service_reuses_the_provider_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "orbix.automation.TWELVE_DATA_URL", f"{self.base_url}/time_series"
        ), patch("orbix.automation.FINNHUB_ECONOMIC_URL", f"{self.base_url}/calendar/economic"):
            service = self._service(folder)
            service.sync_market(force_activate=True)
            dataset_id = service._feed_dataset_id
            with patch.dict(
                os.environ,
                {
                    "ORBIX_TWELVE_DATA_API_KEY": _ProviderHandler.market_key,
                    "ORBIX_FINNHUB_API_KEY": _ProviderHandler.calendar_key,
                },
                clear=False,
            ):
                restarted = AutomationService(service.runtime)
            self.assertEqual(restarted._feed_dataset_id, dataset_id)
            restarted._analyze_new_bar = lambda *args, **kwargs: None
            again = restarted.sync_market()
            self.assertEqual(again["candles_added"], 0)
            self.assertEqual(restarted._feed_dataset_id, dataset_id)
            self.assertEqual(service.runtime.candle_store.active_state()["dataset_id"], dataset_id)


if __name__ == "__main__":
    unittest.main()
