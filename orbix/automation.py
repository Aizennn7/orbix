from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import Candle, format_datetime, parse_datetime
from .market_data import parse_candles_csv, validate_candles
from .runtime import Runtime
from .strategies import EXECUTABLE_STRATEGIES


UTC = timezone.utc
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
FINNHUB_ECONOMIC_URL = "https://finnhub.io/api/v1/calendar/economic"
MAX_PROVIDER_RESPONSE_BYTES = 2_000_000


class ProviderError(ValueError):
    """A safe-to-display data-provider error with no credentials attached."""


def _read_url(url: str, accept: str, timeout: float = 12.0) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": "Orbix-Signal-Lab/0.2", "Accept": accept})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raise ProviderError(f"Provider returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError) as exc:
        # Do not echo the request URL: provider keys can be query parameters.
        reason = getattr(exc, "reason", None)
        detail = "network error" if reason else "request failed"
        raise ProviderError(f"Provider {detail}; check server egress and provider status.") from None
    if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderError("Provider response exceeded the 2 MB safety limit.")
    return payload, content_type


class TwelveDataCsvProvider:
    """Fetch UTC one-minute OHLCV CSV from Twelve Data using a server-side key."""

    def __init__(self, api_key: str | None):
        self.api_key = (api_key or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_candles(self, pair: str, outputsize: int = 500) -> list[Candle]:
        if not self.configured:
            raise ProviderError("Set ORBIX_TWELVE_DATA_API_KEY on the server to connect the M1 feed.")
        query = urlencode({
            "symbol": pair,
            "interval": "1min",
            "outputsize": max(120, min(int(outputsize), 5000)),
            "timezone": "UTC",
            "order": "ASC",
            "format": "CSV",
            "delimiter": ",",
            "apikey": self.api_key,
        })
        body, content_type = _read_url(f"{TWELVE_DATA_URL}?{query}", "text/csv,application/json")
        text = body.decode("utf-8-sig", errors="replace").strip()
        if "json" in content_type.lower() or text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                raise ProviderError("Twelve Data returned an unreadable response.") from None
            message = str(payload.get("message", payload.get("code", "provider error")))
            raise ProviderError(f"Twelve Data did not return candles: {message[:220]}")
        candles = parse_candles_csv(text, minimum=120)
        candles.sort(key=lambda candle: candle.timestamp)
        return validate_candles(candles, minimum=120)


class FinnhubEconomicCalendar:
    """Cached high-impact event calendar; it filters risk, never guesses direction."""

    COUNTRY_BY_CURRENCY = {
        "USD": {"US", "USA", "UNITED STATES"},
        "EUR": {"EU", "EA", "EMU", "EURO AREA", "EUROZONE", "DE", "FR", "IT", "ES", "NL"},
        "GBP": {"GB", "UK", "UNITED KINGDOM"},
        "JPY": {"JP", "JAPAN"},
        "AUD": {"AU", "AUSTRALIA"},
        "CAD": {"CA", "CANADA"},
        "CHF": {"CH", "SWITZERLAND"},
        "NZD": {"NZ", "NEW ZEALAND"},
        "INR": {"IN", "INDIA"},
    }

    def __init__(
        self,
        api_key: str | None,
        timezone_name: str = "UTC",
        blackout_before_minutes: int = 15,
        blackout_after_minutes: int = 30,
    ):
        self.api_key = (api_key or "").strip()
        self.timezone_name = timezone_name.strip() or "UTC"
        try:
            self.event_timezone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown economic-calendar timezone: {self.timezone_name}") from exc
        self.blackout_before = timedelta(minutes=max(0, blackout_before_minutes))
        self.blackout_after = timedelta(minutes=max(0, blackout_after_minutes))
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._updated_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def refresh(self, now: datetime | None = None) -> int:
        if not self.configured:
            raise ProviderError("Set ORBIX_FINNHUB_API_KEY on the server for the event calendar.")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        query = urlencode({
            "from": (current - timedelta(days=1)).date().isoformat(),
            "to": (current + timedelta(days=4)).date().isoformat(),
            "token": self.api_key,
        })
        body, _ = _read_url(f"{FINNHUB_ECONOMIC_URL}?{query}", "application/json")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("Finnhub returned invalid calendar JSON.") from None
        if payload.get("error"):
            raise ProviderError(f"Finnhub calendar error: {str(payload['error'])[:220]}")
        raw_events = payload.get("economicCalendar")
        if not isinstance(raw_events, list):
            raise ProviderError("Finnhub response did not contain an economicCalendar list.")

        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in raw_events:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("event") or "").strip()
            time_text = str(raw.get("time") or "").strip()
            if not name or not time_text:
                continue
            try:
                event_time = datetime.fromisoformat(time_text.replace("Z", "+00:00"))
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=self.event_timezone)
                event_time = event_time.astimezone(UTC)
            except ValueError:
                continue
            country = str(raw.get("country") or "").strip()
            dedupe_key = (event_time.isoformat(), country.upper(), name.casefold())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append({
                "event": name,
                "country": country,
                "impact": str(raw.get("impact") or "unknown").strip().lower(),
                "time": format_datetime(event_time),
                "actual": raw.get("actual"),
                "estimate": raw.get("estimate"),
                "previous": raw.get("prev"),
                "unit": str(raw.get("unit") or ""),
            })
        normalized.sort(key=lambda item: item["time"])
        with self._lock:
            self._events = normalized
            self._updated_at = current
            self._last_error = None
        return len(normalized)

    def record_refresh_error(self, message: str) -> None:
        with self._lock:
            self._last_error = str(message)[:300]

    def _event_is_relevant(self, event: dict[str, Any], currencies: set[str]) -> bool:
        countries: set[str] = set()
        for currency in currencies:
            countries.update(self.COUNTRY_BY_CURRENCY.get(currency, set()))
        country = str(event.get("country", "")).strip().upper()
        return country in countries

    def evaluate(self, pair: str, now: datetime | None = None) -> dict[str, Any]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            events = list(self._events)
            updated_at = self._updated_at
            last_error = self._last_error
        if not self.configured:
            return {
                "status": "UNAVAILABLE",
                "available": False,
                "message": "Economic calendar not configured; news risk cannot be cleared.",
                "last_updated": None,
                "events": [],
            }
        if updated_at is None or current - updated_at > timedelta(seconds=1800) or last_error:
            return {
                "status": "UNAVAILABLE",
                "available": False,
                "message": last_error or "Economic calendar is not fresh; automatic signals are paused.",
                "last_updated": format_datetime(updated_at) if updated_at else None,
                "events": [],
            }

        currencies = {part.strip().upper() for part in pair.split("/") if part.strip()}
        relevant = [
            event for event in events
            if event["impact"] == "high" and self._event_is_relevant(event, currencies)
        ]
        active: list[dict[str, Any]] = []
        upcoming: list[dict[str, Any]] = []
        for event in relevant:
            event_time = parse_datetime(event["time"])
            if current - self.blackout_after <= event_time <= current + self.blackout_before:
                active.append(event)
            elif current < event_time <= current + timedelta(hours=24):
                upcoming.append(event)
        if active:
            event = active[0]
            message = f"High-impact event blackout: {event['event']} ({event['country']}) is inside the configured news window."
            status = "BLACKOUT"
        else:
            message = "Calendar is fresh; no relevant high-impact release is inside the blackout window."
            status = "CLEAR"
        return {
            "status": status,
            "available": True,
            "message": message,
            "last_updated": format_datetime(updated_at),
            "events": upcoming[:8],
            "active_event": active[0] if active else None,
        }

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._lock:
            updated_at = self._updated_at
            last_error = self._last_error
        return {
            "configured": self.configured,
            "connected": bool(updated_at and now - updated_at <= timedelta(seconds=1800) and not last_error),
            "last_updated": format_datetime(updated_at) if updated_at else None,
            "last_error": last_error,
            "timezone": self.timezone_name,
            "blackout_before_minutes": int(self.blackout_before.total_seconds() // 60),
            "blackout_after_minutes": int(self.blackout_after.total_seconds() // 60),
            "event_count": len(self._events),
        }


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _write_csv(candles: list[Candle], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow([
                candle.timestamp.astimezone(UTC).replace(tzinfo=None).isoformat(sep="T"),
                format(candle.open, ".17g"),
                format(candle.high, ".17g"),
                format(candle.low, ".17g"),
                format(candle.close, ".17g"),
                format(candle.volume, ".17g"),
            ])


class AutomationService:
    """Single-process refresh, event guard, and one-signal-per-new-M1-bar loop."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.market_pair = os.getenv("ORBIX_MARKET_PAIR", "EUR/USD").upper().strip()
        self.market_provider = TwelveDataCsvProvider(os.getenv("ORBIX_TWELVE_DATA_API_KEY"))
        self._calendar_config_error: str | None = None
        try:
            self.calendar = FinnhubEconomicCalendar(
                os.getenv("ORBIX_FINNHUB_API_KEY"),
                timezone_name=os.getenv("ORBIX_FINNHUB_TIMEZONE", "UTC"),
                blackout_before_minutes=_env_int("ORBIX_NEWS_BEFORE_MINUTES", 15, 0, 180),
                blackout_after_minutes=_env_int("ORBIX_NEWS_AFTER_MINUTES", 30, 0, 360),
            )
        except ValueError as exc:
            self._calendar_config_error = str(exc)
            self.calendar = FinnhubEconomicCalendar(None, timezone_name="UTC")
            self.calendar.record_refresh_error(self._calendar_config_error)
        self.refresh_seconds = _env_int("ORBIX_MARKET_REFRESH_SECONDS", 60, 60, 3600)
        self.calendar_refresh_seconds = _env_int("ORBIX_NEWS_REFRESH_SECONDS", 900, 300, 86400)
        self.max_entry_delay_seconds = _env_int("ORBIX_MAX_ENTRY_DELAY_SECONDS", 10, 1, 30)
        self.max_bar_age_seconds = _env_int("ORBIX_MAX_BAR_AGE_SECONDS", 180, 60, 900)
        self.auto_signals_enabled = _env_bool("ORBIX_AUTO_SIGNALS", True)
        requested_strategy = os.getenv("ORBIX_AUTO_STRATEGY", "orbix_confluence").strip()
        valid_strategies = {item["id"] for item in EXECUTABLE_STRATEGIES}
        self._strategy_config_error = None
        if requested_strategy not in valid_strategies:
            self._strategy_config_error = f"Unknown auto strategy '{requested_strategy}'; falling back to orbix_confluence."
            requested_strategy = "orbix_confluence"
        self.strategy_id = requested_strategy
        self._sync_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._task: asyncio.Task | None = None
        self._user_paused = False
        self._feed_dataset_id: str | None = None
        self._last_analyzed_timestamp: datetime | None = None
        self._latest_auto_signal: dict[str, Any] | None = None
        self._last_auto_status = self._strategy_config_error or "Waiting for a configured market feed and event calendar."
        self._market_last_sync: datetime | None = None
        self._market_last_success: datetime | None = None
        self._market_error: str | None = None
        self._market_connected = False
        self._last_added_candles = 0
        self._load_marker()

    def _marker_path(self) -> Path:
        return self.runtime.data_dir / "automation_feed.json"

    def _load_marker(self) -> None:
        try:
            payload = json.loads(self._marker_path().read_text(encoding="utf-8"))
            self._user_paused = bool(payload.get("paused_by_user", False))
            with self.runtime.lock:
                active_id = self.runtime._dataset_id
                active_pair = self.runtime.pair
            if (
                payload.get("provider") == "twelve_data"
                and payload.get("dataset_id")
                and payload.get("dataset_id") == active_id
                and payload.get("pair") == active_pair
            ):
                self._feed_dataset_id = active_id
                text = payload.get("last_analyzed_timestamp")
                self._last_analyzed_timestamp = parse_datetime(text) if text else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save_marker(self) -> None:
        payload = {
            "provider": "twelve_data",
            "pair": self.market_pair,
            "dataset_id": self._feed_dataset_id,
            "paused_by_user": self._user_paused,
            "last_analyzed_timestamp": format_datetime(self._last_analyzed_timestamp)
            if self._last_analyzed_timestamp else None,
        }
        target = self._marker_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _set_market_error(self, error: Exception | str) -> None:
        message = str(error)[:300]
        with self._state_lock:
            self._market_last_sync = datetime.now(UTC)
            self._market_error = message
            self._market_connected = False

    def _entry_is_too_late(self, entry_time: datetime, now: datetime) -> bool:
        delay = (now - entry_time).total_seconds()
        return delay < 0 or delay > self.max_entry_delay_seconds

    def _analyze_new_bar(self, latest: Candle, now: datetime) -> None:
        entry_time = latest.timestamp + timedelta(minutes=1)
        if self._last_analyzed_timestamp == latest.timestamp:
            return
        if self._entry_is_too_late(entry_time, now):
            self._last_analyzed_timestamp = latest.timestamp
            self._last_auto_status = (
                f"Skipped stale bar: its entry boundary passed by more than "
                f"{self.max_entry_delay_seconds} seconds. Waiting for the next completed M1 candle."
            )
            self._save_marker()
            return

        self._last_analyzed_timestamp = latest.timestamp
        guard = self.calendar.evaluate(self.market_pair, now)
        if guard["status"] != "CLEAR":
            self._last_auto_status = f"Automatic signal paused: {guard['message']}"
            self._save_marker()
            return

        dataset_id = self.runtime.dataset_id()
        if self.runtime.ledger.has_signal_for_time(
            self.market_pair, self.strategy_id, dataset_id, entry_time
        ):
            self._last_auto_status = "This strategy already has a signal recorded for the current entry time; duplicate suppressed."
            self._save_marker()
            return

        try:
            signal = self.runtime.generate_signal(
                self.market_pair,
                self.strategy_id,
                horizon=1,
                generated_at=now,
                is_live=True,
            )
            strategy_name = next((item["name"] for item in EXECUTABLE_STRATEGIES if item["id"] == self.strategy_id), self.strategy_id)
            signal["heading"] = f"{strategy_name} · {signal['side']}"
            signal["automatic"] = True
            signal["automation_generated_at"] = format_datetime(now)
            signal["news_guard"] = guard
            self._latest_auto_signal = signal
            self._last_auto_status = (
                f"Automatic analysis completed for {self.market_pair}; latest result: {signal['side']}. "
                f"Entry {signal['entry_time']} → prediction {signal['prediction_time']}."
            )
        except (ValueError, RuntimeError) as exc:
            self._last_auto_status = f"Automatic signal failed safely: {str(exc)[:240]}"
        self._save_marker()

    def pause_market_feed(self) -> None:
        with self._sync_lock:
            self._user_paused = True
            self._last_auto_status = "Live feed paused by user; click Sync / Activate Live Feed to resume."
            self._save_marker()

    def sync_market(
        self,
        force_activate: bool = False,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not self.market_provider.configured:
            raise ProviderError("Set ORBIX_TWELVE_DATA_API_KEY on the server before syncing live candles.")
        with self._sync_lock:
            if force_activate:
                self._user_paused = False
                self._save_marker()
            elif self._user_paused:
                message = "Live feed is paused by user; click Sync / Activate Live Feed to resume."
                self._last_auto_status = message
                return {"status": "paused", "message": message, "candles_added": 0}
            with self.runtime.lock:
                current_source = self.runtime.source
                current_pair = self.runtime.pair
                current_dataset = self.runtime._dataset_id
            if (
                not force_activate
                and current_source == "IMPORTED CSV"
                and (not self._feed_dataset_id or current_dataset != self._feed_dataset_id)
            ):
                message = "A manual CSV dataset is active; automatic feed sync will not overwrite it. Use the explicit Sync Live Feed button to activate provider data."
                with self._state_lock:
                    self._market_last_sync = datetime.now(UTC)
                    self._market_error = None
                    self._market_connected = False
                    self._last_auto_status = message
                return {"status": "paused", "message": message, "candles_added": 0}

            try:
                fetched = self.market_provider.fetch_candles(self.market_pair, outputsize=500)
                now = (now or datetime.now(UTC)).astimezone(UTC)
                complete_cutoff = now - timedelta(seconds=5)
                complete = [
                    candle for candle in fetched
                    if candle.timestamp + timedelta(minutes=1) <= complete_cutoff
                ]
                if len(complete) < 120:
                    raise ProviderError("Fewer than 120 closed M1 candles were returned; no data was activated.")

                initial = force_activate or current_source == "SYNTHETIC DEMO" or not self._feed_dataset_id
                added = 0
                if initial:
                    with tempfile.TemporaryDirectory(prefix="orbix-feed-", dir=self.runtime.data_dir) as folder:
                        path = Path(folder) / "candles.csv"
                        _write_csv(complete, path)
                        result = self.runtime.import_csv_file(path, self.market_pair)
                    self._feed_dataset_id = result["dataset_id"]
                    self._last_analyzed_timestamp = None
                    added = len(complete)
                    self._save_marker()
                else:
                    with self.runtime.lock:
                        if self.runtime.source != "IMPORTED CSV" or self.runtime._dataset_id != self._feed_dataset_id:
                            raise ProviderError("Active dataset changed; refusing to append provider data to another dataset.")
                        latest_stored = self.runtime.candles[-1].timestamp if self.runtime.candles else None
                    new_candles = [candle for candle in complete if latest_stored is None or candle.timestamp > latest_stored]
                    if new_candles:
                        with tempfile.TemporaryDirectory(prefix="orbix-feed-", dir=self.runtime.data_dir) as folder:
                            path = Path(folder) / "candles.csv"
                            _write_csv(new_candles, path)
                            result = self.runtime.append_csv_file(path, self.market_pair)
                        added = int(result["count"])

                with self.runtime.lock:
                    latest = self.runtime.candles[-1] if self.runtime.candles else None
                    active_dataset = self.runtime._dataset_id
                with self._state_lock:
                    self._market_last_sync = now
                    self._market_last_success = now
                    self._market_error = None
                    self._market_connected = True
                    self._last_added_candles = added
                    if self._feed_dataset_id != active_dataset:
                        self._market_connected = False
                        self._last_auto_status = "Provider data was not made active; check the active dataset before generating automatic signals."
                    elif not self.auto_signals_enabled:
                        self._last_auto_status = "Automatic signal generation is disabled by ORBIX_AUTO_SIGNALS."
                    elif latest is not None and (initial or added > 0):
                        self._analyze_new_bar(latest, now)
                    elif added == 0:
                        self._last_auto_status = "Feed refreshed; no new completed M1 candle was available."
                return {
                    "status": "synced" if self._market_connected else "paused",
                    "pair": self.market_pair,
                    "candles_added": added,
                    "latest": latest.to_dict() if latest else None,
                    "message": self._last_auto_status,
                }
            except Exception as exc:
                self._set_market_error(exc)
                raise

    def sync_calendar(self) -> dict[str, Any]:
        try:
            count = self.calendar.refresh()
            with self._state_lock:
                self._last_auto_status = f"Economic calendar refreshed: {count} events cached."
            return {"status": "connected", "events_cached": count}
        except Exception as exc:
            self.calendar.record_refresh_error(str(exc))
            raise

    def evaluate_news(self, pair: str) -> dict[str, Any]:
        return self.calendar.evaluate(pair)

    def _next_market_delay(self) -> float:
        if self.refresh_seconds == 60:
            seconds_to_boundary = 60.0 - (time.time() % 60.0)
            return max(1.0, seconds_to_boundary + 5.0)
        return float(self.refresh_seconds)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="orbix-market-automation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        next_market = 0.0 if self.market_provider.configured else float("inf")
        next_calendar = 0.0 if self.calendar.configured else float("inf")
        while True:
            now = time.monotonic()
            if self.calendar.configured and now >= next_calendar:
                try:
                    await asyncio.to_thread(self.sync_calendar)
                except Exception:
                    pass
                next_calendar = time.monotonic() + self.calendar_refresh_seconds
            if self.market_provider.configured and now >= next_market:
                try:
                    await asyncio.to_thread(self.sync_market, False)
                except Exception:
                    pass
                next_market = time.monotonic() + self._next_market_delay()
            deadlines = [deadline for deadline in (next_market, next_calendar) if deadline != float("inf")]
            if not deadlines:
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(max(0.5, min(30.0, min(deadlines) - time.monotonic())))

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        calendar_status = self.calendar.status()
        with self.runtime.lock:
            source = self.runtime.source
            pair = self.runtime.pair
            dataset_id = self.runtime._dataset_id
            latest = self.runtime.candles[-1] if self.runtime.candles else None
            candle_count = self.runtime.candle_count
        with self._state_lock:
            last_market_sync = self._market_last_sync
            last_market_success = self._market_last_success
            market_error = self._market_error
            provider_dataset_active = bool(
                self._feed_dataset_id and dataset_id == self._feed_dataset_id and source == "IMPORTED CSV"
            )
            market_connected = self._market_connected and provider_dataset_active and not self._user_paused and bool(
                last_market_success and now - last_market_success <= timedelta(seconds=self.refresh_seconds * 3)
            )
            auto_signal = dict(self._latest_auto_signal) if self._latest_auto_signal else None
            auto_message = self._last_auto_status
        guard = self.calendar.evaluate(pair, now)
        latest_bar_age_seconds = (
            (now - (latest.timestamp + timedelta(minutes=1))).total_seconds() if latest else None
        )
        market_data_fresh = bool(
            latest_bar_age_seconds is not None
            and -5 <= latest_bar_age_seconds <= self.max_bar_age_seconds
        )
        market_ready = bool(
            self.market_provider.configured
            and not self._user_paused
            and market_connected
            and market_data_fresh
            and source == "IMPORTED CSV"
            and self._feed_dataset_id
            and dataset_id == self._feed_dataset_id
        )
        news_ready = guard["status"] == "CLEAR" and calendar_status["connected"]
        return {
            "server_time": format_datetime(now),
            "pair": self.market_pair,
            "strategy": self.strategy_id,
            "auto_signals_enabled": self.auto_signals_enabled,
            "automatic_signals_active": bool(market_ready and news_ready and self.auto_signals_enabled),
            "market": {
                "provider": "Twelve Data",
                "configured": self.market_provider.configured,
                "connected": market_connected,
                "active_dataset_is_provider": provider_dataset_active,
                "paused_by_user": self._user_paused,
                "active_source": source,
                "active_pair": pair,
                "latest_candle": latest.to_dict() if latest else None,
                "latest_bar_age_seconds": latest_bar_age_seconds,
                "data_fresh": market_data_fresh,
                "max_bar_age_seconds": self.max_bar_age_seconds,
                "candle_count": candle_count,
                "refresh_seconds": self.refresh_seconds,
                "last_sync": format_datetime(last_market_sync) if last_market_sync else None,
                "last_success": format_datetime(last_market_success) if last_market_success else None,
                "candles_added_last_sync": self._last_added_candles,
                "last_error": market_error,
            },
            "calendar": {**calendar_status, "guard": guard},
            "auto_status": auto_message,
            "latest_auto_signal": auto_signal,
            "limits": {
                "entry_late_seconds": self.max_entry_delay_seconds,
                "news_blackout_before_minutes": int(self.calendar.blackout_before.total_seconds() // 60),
                "news_blackout_after_minutes": int(self.calendar.blackout_after.total_seconds() // 60),
            },
        }
