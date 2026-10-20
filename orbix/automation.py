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
from .telegram import TelegramError, TelegramNotifier


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
        self.auto_imported = _env_bool("ORBIX_AUTO_IMPORTED", True)
        self.imported_max_entry_delay = _env_int("ORBIX_IMPORTED_MAX_ENTRY_DELAY_SECONDS", 120, 1, 3600)
        self.telegram = TelegramNotifier(
            os.getenv("ORBIX_TELEGRAM_BOT_TOKEN"),
            os.getenv("ORBIX_TELEGRAM_CHAT_ID"),
        )
        self.telegram_timezone_name = os.getenv("ORBIX_TELEGRAM_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
        try:
            self.telegram_timezone = ZoneInfo(self.telegram_timezone_name)
            self._telegram_timezone_error: str | None = None
        except ZoneInfoNotFoundError:
            self.telegram_timezone = ZoneInfo("UTC")
            self._telegram_timezone_error = f"Unknown Telegram timezone '{self.telegram_timezone_name}'; using UTC."
        self._telegram_auto_post_enabled = _env_bool("ORBIX_TELEGRAM_AUTO_POST", False)
        self._telegram_signal_ids: list[str] = []
        self._telegram_result_ids: list[str] = []
        self._telegram_inflight_signal_ids: set[str] = set()
        self._telegram_inflight_result_ids: set[str] = set()
        self._telegram_last_error: str | None = self._telegram_timezone_error
        self._telegram_last_sent_at: datetime | None = None
        self._telegram_last_message_id: int | str | None = None
        self.telegram_daily_cap = _env_int("ORBIX_TELEGRAM_DAILY_CAP", 40, 1, 500)
        self.telegram_cooldown_seconds = _env_int("ORBIX_TELEGRAM_COOLDOWN_SECONDS", 60, 0, 3600)
        self._telegram_daily_date: str | None = None
        self._telegram_daily_count = 0
        self._telegram_message_ids: dict[str, int] = {}
        self._telegram_edited_result_ids: list[str] = []
        self._telegram_last_skip_reason: str | None = None
        self._auto_signal_history: list[dict[str, Any]] = []
        requested_strategy = os.getenv("ORBIX_AUTO_STRATEGY", "orbix_cadence").strip()
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
        self._last_imported_dataset_id: str | None = None
        self._last_imported_timestamp: datetime | None = None
        self._last_dataset_learning: dict[str, Any] | None = None
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
            self._telegram_auto_post_enabled = bool(
                payload.get("telegram_auto_post_enabled", self._telegram_auto_post_enabled)
            )
            self._telegram_signal_ids = [str(item) for item in payload.get("telegram_signal_ids", [])][-500:]
            self._telegram_result_ids = [str(item) for item in payload.get("telegram_result_ids", [])][-500:]
            self._auto_signal_history = [
                item for item in payload.get("auto_signal_history", []) if isinstance(item, dict)
            ][-20:]
            sent_at = payload.get("telegram_last_sent_at")
            self._telegram_last_sent_at = parse_datetime(sent_at) if sent_at else None
            self._telegram_daily_date = payload.get("telegram_daily_date") or None
            self._telegram_daily_count = int(payload.get("telegram_daily_count") or 0)
            self._telegram_edited_result_ids = [
                str(item) for item in payload.get("telegram_edited_result_ids", [])
            ][-500:]
            raw_ids = payload.get("telegram_message_ids") or {}
            if isinstance(raw_ids, dict):
                self._telegram_message_ids = {
                    str(key): int(value)
                    for key, value in raw_ids.items()
                    if str(value).lstrip("-").isdigit()
                }
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
        with self._state_lock:
            payload = {
                "provider": "twelve_data",
                "pair": self.market_pair,
                "dataset_id": self._feed_dataset_id,
                "paused_by_user": self._user_paused,
                "last_analyzed_timestamp": format_datetime(self._last_analyzed_timestamp)
                if self._last_analyzed_timestamp else None,
                "telegram_auto_post_enabled": self._telegram_auto_post_enabled,
                "telegram_signal_ids": self._telegram_signal_ids[-500:],
                "telegram_result_ids": self._telegram_result_ids[-500:],
                "telegram_last_sent_at": format_datetime(self._telegram_last_sent_at)
                if self._telegram_last_sent_at else None,
                "telegram_daily_date": self._telegram_daily_date,
                "telegram_daily_count": self._telegram_daily_count,
                "telegram_message_ids": {
                    signal_id: self._telegram_message_ids[signal_id]
                    for signal_id in list(self._telegram_message_ids)[-200:]
                },
                "telegram_edited_result_ids": self._telegram_edited_result_ids[-500:],
                "auto_signal_history": self._auto_signal_history[-20:],
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

    def _entry_is_too_late(
        self, entry_time: datetime, now: datetime, max_delay: int | None = None
    ) -> bool:
        delay = (now - entry_time).total_seconds()
        limit = self.max_entry_delay_seconds if max_delay is None else max(1, int(max_delay))
        return delay < 0 or delay > limit

    def _record_skip_abstention(
        self,
        entry_time: datetime | None,
        reason: str,
        *,
        kind: str = "operational",
    ) -> None:
        """Log why a closed bar produced no signal. Never raises into the loop."""
        try:
            dataset_id = self.runtime.dataset_id()
            self.runtime.ledger.record_abstention(
                pair=self.market_pair,
                strategy=self.strategy_id,
                reason=reason,
                kind=kind,
                entry_time=entry_time,
                source=self.runtime.source,
                dataset_id=dataset_id,
            )
        except Exception:
            # A logging failure must never stop the feed.
            pass

    def _journal(self, detail: str, pair: str, entry_time: datetime | None) -> None:
        """Write one human-readable line to the trading journal. Never raises."""
        try:
            self.runtime.ledger.record_engine_event(
                pair=pair,
                dataset_id=self.runtime.dataset_id(),
                kind="journal",
                detail=detail,
                at=format_datetime(entry_time) if entry_time else None,
            )
        except Exception:
            pass

    def _journal_once(
        self,
        tag: str,
        detail: str,
        pair: str,
        entry_time: datetime | None,
    ) -> None:
        """Write a meaningful pass once per day (or once per cool-off)."""
        try:
            moment = entry_time or datetime.now(UTC)
            day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            if self.runtime.ledger.has_engine_event(
                "journal",
                since=format_datetime(day_start),
                contains=f"[{tag}]",
                dataset_id=self.runtime.dataset_id(),
            ):
                return
            self._journal(f"[{tag}] {detail}", pair, entry_time)
        except Exception:
            pass

    def _analyze_new_bar(
        self,
        latest: Candle,
        now: datetime,
        *,
        pair: str | None = None,
        is_live: bool = True,
        require_news: bool = True,
        max_delay: int | None = None,
        use_feed_marker: bool = True,
    ) -> None:
        pair = (pair or self.market_pair).upper()
        entry_time = latest.timestamp + timedelta(minutes=1)
        if use_feed_marker and self._last_analyzed_timestamp == latest.timestamp:
            return
        if self._entry_is_too_late(entry_time, now, max_delay):
            if use_feed_marker:
                self._last_analyzed_timestamp = latest.timestamp
            self._last_auto_status = (
                f"Skipped stale bar: its entry boundary passed by more than "
                f"{max_delay if max_delay is not None else self.max_entry_delay_seconds} seconds. "
                "Waiting for the next completed M1 candle."
            )
            self._record_skip_abstention(entry_time, self._last_auto_status)
            self._save_marker()
            return

        if use_feed_marker:
            self._last_analyzed_timestamp = latest.timestamp
        guard = self.calendar.evaluate(pair, now)
        if require_news and guard["status"] != "CLEAR":
            self._last_auto_status = f"Automatic signal paused: {guard['message']}"
            self._record_skip_abstention(entry_time, self._last_auto_status)
            self._save_marker()
            return

        dataset_id = self.runtime.dataset_id()
        if self.runtime.ledger.has_signal_for_time(pair, self.strategy_id, dataset_id, entry_time):
            self._last_auto_status = "This strategy already has a signal recorded for the current entry time; duplicate suppressed."
            self._record_skip_abstention(entry_time, self._last_auto_status)
            self._save_marker()
            return

        # A disciplined trader is not at the screen every minute. This gate can
        # only remove trades: sessions, a daily budget, spacing, cool-offs, a
        # loss stop, and memory of contexts that have lost. It never adds a
        # signal, and the dataset learner keeps learning from every bar.
        verdict = self.runtime.discipline.assess(
            pair=pair,
            strategy=self.strategy_id,
            entry_time=entry_time,
        )
        if not verdict.allowed:
            self._last_auto_status = verdict.note
            # Being asleep, waiting out the clock, or having used the day's
            # budget are not per-bar setup decisions. They stay in the status
            # instead of flooding the abstention ledger. Meaningful passes are
            # journaled once, not on every minute of the wait.
            if verdict.journal:
                tag = verdict.code
                if verdict.cooldown_until:
                    tag += f":{verdict.cooldown_until[11:16]}"
                self._journal_once(tag, verdict.note, pair, entry_time)
            self._save_marker()
            return

        try:
            signal = self.runtime.generate_signal(
                pair,
                self.strategy_id,
                horizon=1,
                generated_at=now,
                is_live=is_live,
            )
            strategy_name = next((item["name"] for item in EXECUTABLE_STRATEGIES if item["id"] == self.strategy_id), self.strategy_id)
            if signal["side"] != "NO SIGNAL":
                trade = self.runtime.discipline.describe(signal, verdict)
                signal["discipline"] = trade
                self._journal(trade["note"], pair, entry_time)
            signal["heading"] = f"{strategy_name} · {signal['side']}"
            signal["automatic"] = True
            signal["automation_generated_at"] = format_datetime(now)
            signal["news_guard"] = guard
            signal["paper_only"] = not is_live
            self._latest_auto_signal = signal
            with self._state_lock:
                self._auto_signal_history.append({
                    "signal_id": signal["signal_id"],
                    "pair": signal["pair"],
                    "side": signal["side"],
                    "entry_time": signal["entry_time"],
                    "prediction_time": signal["prediction_time"],
                    "probability_up": signal.get("probability_up"),
                    "reason": signal.get("reason"),
                    "source": signal.get("source"),
                    "heading": signal.get("heading"),
                })
                self._auto_signal_history = self._auto_signal_history[-20:]
            mode = "paper-only imported analysis" if not is_live else "automatic analysis"
            self._last_auto_status = (
                f"Completed {mode} for {pair}; latest result: {signal['side']}. "
                f"Entry {signal['entry_time']} → prediction {signal['prediction_time']}."
            )
        except (ValueError, RuntimeError) as exc:
            self._last_auto_status = f"Automatic signal failed safely: {str(exc)[:240]}"
        self._save_marker()
        self._maybe_post_automatic_signal()

    def _dataset_scan(self) -> dict[str, Any]:
        """Watch the active imported dataset that is not a provider feed.

        A static historical file has no new bars; the scanner records that state
        once and stays idle. When a streaming append adds a fresh bar, the same
        paper-only pipeline runs prediction, outcome resolution, and every-bar
        learning without any manual click. Imported signals are never live and
        are never auto-posted to Telegram.
        """
        if not self.auto_imported:
            return {"status": "disabled", "reason": "ORBIX_AUTO_IMPORTED is false."}
        now = datetime.now(UTC)
        with self.runtime.lock:
            source = self.runtime.source
            dataset_id = self.runtime._dataset_id
            pair = self.runtime.pair
            latest = self.runtime.candles[-1] if self.runtime.candles else None
        if source != "IMPORTED CSV" or not dataset_id or latest is None:
            return {"status": "skipped", "reason": "No imported dataset is active."}
        if dataset_id == self._feed_dataset_id:
            return {"status": "provider_owned", "reason": "The provider loop already analyzes this dataset."}

        if dataset_id != self._last_imported_dataset_id:
            self._last_imported_dataset_id = dataset_id
            self._last_imported_timestamp = None

        learning = self.runtime.learn_dataset_outcomes()
        with self._state_lock:
            self._last_dataset_learning = learning

        if self._last_imported_timestamp == latest.timestamp:
            return {"status": "waiting", "dataset_learning": learning}
        if not self.auto_signals_enabled:
            return {"status": "disabled", "reason": "ORBIX_AUTO_SIGNALS is false."}

        entry_time = latest.timestamp + timedelta(minutes=1)
        age = (now - entry_time).total_seconds()
        if age < -5:
            return {"status": "future_bar", "dataset_learning": learning}
        if age > self.max_bar_age_seconds:
            self._last_imported_timestamp = latest.timestamp
            self._last_auto_status = (
                f"Imported dataset {dataset_id[:8]} is historical (latest bar "
                f"{format_datetime(latest.timestamp)}); paper automation is watching for a new bar."
            )
            return {"status": "historical", "dataset_learning": learning}

        self._last_imported_timestamp = latest.timestamp
        self._analyze_new_bar(
            latest,
            now,
            pair=pair,
            is_live=False,
            require_news=False,
            max_delay=self.imported_max_entry_delay,
            use_feed_marker=False,
        )
        with self._state_lock:
            status = self._last_auto_status
        return {"status": "analyzed", "auto_status": status, "dataset_learning": learning}

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
                        self._post_outcome_results(result.get("auto_resolved") or [])

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

    # ---------------------------------------------------------------- Telegram
    TELEGRAM_SIDE_LABEL = {"BUY": "CALL (BUY)", "SELL": "PUT (SELL)", "NO SIGNAL": "ABSTAIN"}

    def telegram_message_text(self, signal: dict[str, Any]) -> str:
        side = str(signal.get("side", "NO SIGNAL"))
        source = str(signal.get("source") or "UNKNOWN")
        probability = signal.get("probability_up")
        if probability is not None:
            basis = "calibrated on resolved outcomes" if signal.get("probability_calibrated") else "raw model estimate, not calibrated"
            probability_text = f"{float(probability) * 100:.1f}% up ({basis})"
        else:
            probability_text = "not available (rule gate only)"
        entry = self._telegram_time(signal.get("entry_time") or signal.get("generated_at"))
        expiry = self._telegram_time(signal.get("prediction_time") or signal.get("expires_at"))
        horizon = signal.get("horizon") or 1
        reason = str(signal.get("reason") or "No reason recorded.").strip()
        if len(reason) > 260:
            reason = reason[:257] + "..."
        return (
            "ORBIX — M1 RESEARCH SIGNAL\n"
            f"{signal.get('pair', 'UNKNOWN')} · {self.TELEGRAM_SIDE_LABEL.get(side, side)}\n"
            f"Entry: {entry}\n"
            f"Expiry: {expiry} ({horizon} min)\n"
            f"Data: {source}\n"
            f"Model: {probability_text}\n"
            f"Rule: {reason}\n"
            f"ID: {signal.get('signal_id', '—')}\n\n"
            "Research signal only. No order was placed. M1 calls can lose; no accuracy or outcome is guaranteed."
        )

    def telegram_batch_text(self, signals: list[dict[str, Any]], *, demo_included: bool = False) -> str:
        rows = []
        for signal in signals:
            side = str(signal.get("side", ""))
            direction = {"BUY": "CALL", "SELL": "PUT"}.get(side, "ABSTAIN")
            rows.append(
                f"M1; {signal.get('pair', 'UNKNOWN')}; {self._telegram_time(signal.get('entry_time'), clock_only=True)}; {direction}"
            )
        header = [
            f"ORBIX — M1 BATCH ({len(signals)})",
            f"Entry clock: {self.telegram_timezone_name}",
            "",
        ]
        footer = [
            "",
            f"Strategy: {self.strategy_id}",
            "Research signals only. No orders were placed. Calls can lose; no accuracy or outcome is guaranteed.",
        ]
        if demo_included:
            footer.insert(0, "WARNING: this batch contains SYNTHETIC DEMO data and is not market evidence.")
        return "\n".join(header + rows + footer)

    def telegram_result_text(self, record: dict[str, Any], learned: bool | None = None) -> str:
        side = str(record.get("side", ""))
        outcome = str(record.get("outcome") or "UNRESOLVED")
        exit_price = record.get("exit_price")
        entry = self._telegram_time(record.get("entry_time") or record.get("generated_at"))
        expiry = self._telegram_time(record.get("expires_at"), clock_only=True)
        learning = (
            "Model updated once after expiry." if learned
            else "No model update for this outcome."
        )
        exit_text = f"{float(exit_price):.6g}" if exit_price is not None else "not recorded"
        return (
            "ORBIX — M1 PAPER RESULT\n"
            f"{record.get('pair', 'UNKNOWN')} · {self.TELEGRAM_SIDE_LABEL.get(side, side)}\n"
            f"Entry {entry} → expiry {expiry}\n"
            f"Outcome: {outcome} · paper exit {exit_text}\n"
            f"{learning}\n\n"
            "Paper result from closed candles only. Not a live trade and not a prediction of future results."
        )

    def telegram_result_edit_text(self, record: dict[str, Any], learned: bool | None = None) -> str:
        """Full replacement text: the original signal plus its resolved result."""
        side = str(record.get("side", ""))
        outcome = str(record.get("outcome") or "UNRESOLVED")
        exit_price = record.get("exit_price")
        expiry = self._telegram_time(record.get("expires_at"), clock_only=True)
        exit_text = f"{float(exit_price):.6g}" if exit_price is not None else "not recorded"
        learning = (
            "Model updated once after expiry." if learned
            else "No model update for this outcome."
        )
        original = self.telegram_message_text(record)
        return (
            f"{original}\n"
            "———\n"
            f"RESULT: {outcome} · {side} · paper exit {exit_text}\n"
            f"Expiry {expiry}\n"
            f"{learning}\n"
            "Edited in place from closed candles. Not a live trade."
        )

    def _telegram_daily_usage(self, now: datetime | None = None) -> int:
        """Messages posted to the channel today (UTC). Resets at 00:00 UTC."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        day = current.date().isoformat()
        with self._state_lock:
            if self._telegram_daily_date != day:
                self._telegram_daily_date = day
                self._telegram_daily_count = 0
            return self._telegram_daily_count

    def _telegram_auto_gate(self, now: datetime | None = None) -> tuple[bool, str | None]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._state_lock:
            if not self.telegram.configured:
                return False, "Telegram credentials are not configured."
            if not self._telegram_auto_post_enabled:
                return False, "Automatic posting is off."
            used = self._telegram_daily_usage(current)
            if used >= self.telegram_daily_cap:
                return False, (
                    f"Daily cap reached ({used}/{self.telegram_daily_cap}); automatic posting resumes after 00:00 UTC."
                )
            if self._telegram_last_sent_at is not None and self.telegram_cooldown_seconds > 0:
                elapsed = (current - self._telegram_last_sent_at).total_seconds()
                if elapsed < self.telegram_cooldown_seconds:
                    wait = int(self.telegram_cooldown_seconds - elapsed) + 1
                    return False, f"Cooldown active; next automatic post in about {wait}s."
            return True, None

    def _telegram_note_send(
        self,
        message_id: int | str | None,
        *,
        now: datetime | None = None,
        signal_id: str | None = None,
        count_message: bool = True,
    ) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._state_lock:
            if count_message:
                self._telegram_daily_usage(current)
                self._telegram_daily_count += 1
            self._telegram_last_sent_at = current
            self._telegram_last_message_id = message_id
            self._telegram_last_error = None
            if signal_id and message_id not in (None, ""):
                try:
                    self._telegram_message_ids[str(signal_id)] = int(message_id)
                except (TypeError, ValueError):
                    pass
                self._telegram_message_ids = {
                    key: self._telegram_message_ids[key]
                    for key in list(self._telegram_message_ids)[-200:]
                }

    def _telegram_time(self, value: Any, *, clock_only: bool = False) -> str:
        if not value:
            return "—"
        try:
            moment = parse_datetime(value) if isinstance(value, str) else value
        except ValueError:
            return str(value)
        if moment is None:
            return "—"
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        local = moment.astimezone(self.telegram_timezone)
        return local.strftime("%H:%M") if clock_only else f"{local.strftime('%H:%M')} {self.telegram_timezone_name}"

    def telegram_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "configured": self.telegram.configured,
                "target_chat": self.telegram.chat_id or None,
                "timezone": self.telegram_timezone_name,
                "auto_post_enabled": self._telegram_auto_post_enabled and self.telegram.configured,
                "sent_signal_count": len(self._telegram_signal_ids),
                "sent_result_count": len(self._telegram_result_ids),
                "sent_signal_ids": self._telegram_signal_ids[-20:],
                "sent_result_ids": self._telegram_result_ids[-20:],
                "edited_result_ids": self._telegram_edited_result_ids[-20:],
                "last_sent_at": format_datetime(self._telegram_last_sent_at) if self._telegram_last_sent_at else None,
                "last_message_id": self._telegram_last_message_id,
                "last_error": self._telegram_last_error,
                "can_auto_post": self.telegram.configured,
                "daily_cap": self.telegram_daily_cap,
                "sent_today": self._telegram_daily_usage(),
                "cooldown_seconds": self.telegram_cooldown_seconds,
                "edited_result_count": len(self._telegram_edited_result_ids),
                "auto_post_block_reason": self._telegram_last_skip_reason,
            }

    def set_telegram_auto_post(self, enabled: bool) -> dict[str, Any]:
        if enabled and not self.telegram.configured:
            raise TelegramError(
                "Configure ORBIX_TELEGRAM_BOT_TOKEN and ORBIX_TELEGRAM_CHAT_ID before enabling automatic posting."
            )
        with self._state_lock:
            self._telegram_auto_post_enabled = bool(enabled)
        self._save_marker()
        return self.telegram_status()

    def send_telegram_test(self) -> dict[str, Any]:
        result = self.telegram.send_message(
            "ORBIX — Telegram channel connected.\n"
            "This channel is now the configured target for M1 research signals. "
            "Automatic posting stays off until you enable it on the Telegram page. "
            "No orders are executed and no accuracy is promised."
        )
        with self._state_lock:
            self._telegram_last_error = None
            self._telegram_last_sent_at = datetime.now(UTC)
            self._telegram_last_message_id = result.get("message_id")
        self._save_marker()
        return {"status": "sent", **result}

    def _telegram_candidates(self) -> list[dict[str, Any]]:
        pool: list[dict[str, Any]] = []
        with self._state_lock:
            history = list(reversed(self._auto_signal_history))
        pool.extend(history)
        try:
            rows = self.runtime.ledger.list_recent(40)
        except Exception:
            rows = []
        for row in rows:
            pool.append({
                **row,
                "entry_time": row.get("entry_time") or row.get("generated_at"),
                "prediction_time": row.get("expires_at"),
                "heading": f"{row.get('strategy', 'signal')} · {row.get('side')}",
            })
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in pool:
            signal_id = str(item.get("signal_id") or "")
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            deduped.append(item)
        return deduped

    def send_telegram_batch(
        self,
        *,
        signal_ids: list[str] | None = None,
        limit: int = 5,
        include_demo: bool = False,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 10))
        if not dry_run and not self.telegram.configured:
            raise TelegramError(
                "Set ORBIX_TELEGRAM_BOT_TOKEN and ORBIX_TELEGRAM_CHAT_ID on the server before sending."
            )
        if not dry_run and not force:
            used = self._telegram_daily_usage()
            if used >= self.telegram_daily_cap:
                raise TelegramError(
                    f"Daily cap reached ({used}/{self.telegram_daily_cap}). "
                    "Manual and automatic sends resume after 00:00 UTC, or pass an explicit replay."
                )
        candidates = self._telegram_candidates()
        if signal_ids:
            wanted = {str(item) for item in signal_ids}
            candidates = [item for item in candidates if str(item.get("signal_id")) in wanted]
        with self._state_lock:
            already_sent = set(self._telegram_signal_ids)
            inflight = set(self._telegram_inflight_signal_ids)

        selected: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for item in candidates:
            signal_id = str(item.get("signal_id"))
            if str(item.get("side")) not in {"BUY", "SELL"}:
                skipped.append({"signal_id": signal_id, "reason": "abstained (NO SIGNAL)"})
                continue
            if str(item.get("source")) == "SYNTHETIC DEMO" and not include_demo:
                skipped.append({"signal_id": signal_id, "reason": "synthetic demo data; enable demo transmission explicitly"})
                continue
            if signal_id in already_sent and not force:
                skipped.append({"signal_id": signal_id, "reason": "already sent"})
                continue
            if signal_id in inflight:
                skipped.append({"signal_id": signal_id, "reason": "send already in progress"})
                continue
            selected.append(item)
            if len(selected) >= limit:
                break

        if not selected:
            return {
                "status": "nothing_to_send",
                "sent": [],
                "skipped": skipped[:12],
                "message": "No unsent, non-abstained signals are available. Generate a signal first.",
            }

        demo_included = any(str(item.get("source")) == "SYNTHETIC DEMO" for item in selected)
        text = (
            self.telegram_batch_text(selected, demo_included=demo_included)
            if len(selected) > 1
            else self.telegram_message_text(selected[0])
        )
        ids = [str(item.get("signal_id")) for item in selected]
        if dry_run:
            return {"status": "preview", "sent": [], "preview_ids": ids, "text": text, "skipped": skipped[:12]}

        with self._state_lock:
            self._telegram_inflight_signal_ids.update(ids)
        try:
            result = self.telegram.send_message(text)
        except TelegramError as exc:
            with self._state_lock:
                self._telegram_inflight_signal_ids.difference_update(ids)
                self._telegram_last_error = str(exc)[:300]
            raise
        with self._state_lock:
            self._telegram_inflight_signal_ids.difference_update(ids)
            for signal_id in ids:
                if signal_id not in self._telegram_signal_ids:
                    self._telegram_signal_ids.append(signal_id)
            self._telegram_signal_ids = self._telegram_signal_ids[-500:]
        self._telegram_note_send(
            result.get("message_id"),
            signal_id=ids[0] if len(ids) == 1 else None,
        )
        self._save_marker()
        return {
            "status": "sent",
            "sent": ids,
            "skipped": skipped[:12],
            "single_message": len(ids) == 1,
            **result,
        }

    def _maybe_post_automatic_signal(self) -> None:
        with self._state_lock:
            enabled = self._telegram_auto_post_enabled and self.telegram.configured
            signal = dict(self._latest_auto_signal) if self._latest_auto_signal else None
        if not enabled or not signal:
            return
        if str(signal.get("side")) not in {"BUY", "SELL"}:
            with self._state_lock:
                self._telegram_last_skip_reason = "Latest automatic read abstained; nothing was posted."
            return
        if str(signal.get("source")) == "SYNTHETIC DEMO":
            with self._state_lock:
                self._telegram_last_skip_reason = "Latest automatic read used synthetic demo data; not posted."
            return
        if not bool(signal.get("is_live", True)):
            with self._state_lock:
                self._telegram_last_skip_reason = (
                    "Paper-only imported signal; automatic Telegram posting is limited to live provider signals."
                )
            return
        signal_id = str(signal.get("signal_id"))
        with self._state_lock:
            if signal_id in self._telegram_signal_ids or signal_id in self._telegram_inflight_signal_ids:
                return
        allowed, reason = self._telegram_auto_gate()
        if not allowed:
            with self._state_lock:
                self._telegram_last_skip_reason = reason
            return
        with self._state_lock:
            self._telegram_inflight_signal_ids.add(signal_id)
        try:
            result = self.telegram.send_message(self.telegram_message_text(signal))
        except TelegramError as exc:
            with self._state_lock:
                self._telegram_inflight_signal_ids.discard(signal_id)
                self._telegram_last_error = str(exc)[:300]
            self._save_marker()
            return
        with self._state_lock:
            self._telegram_inflight_signal_ids.discard(signal_id)
            if signal_id not in self._telegram_signal_ids:
                self._telegram_signal_ids.append(signal_id)
            self._telegram_signal_ids = self._telegram_signal_ids[-500:]
            self._telegram_last_skip_reason = None
        self._telegram_note_send(result.get("message_id"), signal_id=signal_id)
        self._save_marker()

    def _post_outcome_results(self, outcomes: list[dict[str, Any]]) -> None:
        if not outcomes or not self.telegram.configured:
            return
        for outcome in outcomes:
            signal_id = str(outcome.get("signal_id") or "")
            if not signal_id:
                continue
            with self._state_lock:
                if signal_id not in self._telegram_signal_ids or signal_id in self._telegram_result_ids:
                    continue
                if signal_id in self._telegram_inflight_result_ids:
                    continue
                self._telegram_inflight_result_ids.add(signal_id)
            edited = False
            try:
                record = self.runtime.ledger.get(signal_id)
                if not record:
                    raise TelegramError("Signal record was not found for the result message.")
                learned = bool(outcome.get("learned"))
                with self._state_lock:
                    message_id = self._telegram_message_ids.get(signal_id)
                result = None
                if message_id is not None:
                    try:
                        result = self.telegram.edit_message_text(
                            message_id, self.telegram_result_edit_text(record, learned=learned)
                        )
                        edited = True
                    except TelegramError:
                        # Older posts, deleted messages, and permission changes
                        # fall back to a new result message instead of failing.
                        result = None
                if result is None:
                    result = self.telegram.send_message(
                        self.telegram_result_text(record, learned=learned)
                    )
            except TelegramError as exc:
                with self._state_lock:
                    self._telegram_inflight_result_ids.discard(signal_id)
                    self._telegram_last_error = str(exc)[:300]
                continue
            with self._state_lock:
                self._telegram_inflight_result_ids.discard(signal_id)
                self._telegram_result_ids.append(signal_id)
                self._telegram_result_ids = self._telegram_result_ids[-500:]
                if edited:
                    self._telegram_edited_result_ids.append(signal_id)
                    self._telegram_edited_result_ids = self._telegram_edited_result_ids[-500:]
                self._telegram_last_error = None
                self._telegram_last_sent_at = datetime.now(UTC)
                self._telegram_last_message_id = result.get("message_id")
        self._save_marker()

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
        # Imported datasets are watched even without a provider key. The scan is
        # a no-op for static historical files and drives streaming appends.
        next_dataset = time.monotonic() + 5.0 if self.auto_imported else float("inf")
        # Calibration refit and drift-triggered retraining are checked on a slow
        # schedule; both are no-ops unless real imported outcomes exist.
        next_learning = time.monotonic() + 900.0
        while True:
            now = time.monotonic()
            if self.auto_imported and now >= next_dataset:
                try:
                    await asyncio.to_thread(self._dataset_scan)
                except Exception:
                    pass
                next_dataset = time.monotonic() + 30.0
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
            if now >= next_learning:
                try:
                    await asyncio.to_thread(self.runtime.learning_maintenance)
                except Exception:
                    pass
                next_learning = time.monotonic() + 900.0
            deadlines = [
                deadline for deadline in (next_market, next_calendar, next_dataset, next_learning)
                if deadline != float("inf")
            ]
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
        paper_enabled = bool(
            self.auto_imported
            and self.auto_signals_enabled
            and source == "IMPORTED CSV"
            and not provider_dataset_active
        )
        paper_active = bool(paper_enabled and latest is not None)
        return {
            "server_time": format_datetime(now),
            "pair": self.market_pair,
            "strategy": self.strategy_id,
            "auto_signals_enabled": self.auto_signals_enabled,
            "auto_imported": self.auto_imported,
            "automatic_signals_active": bool(market_ready and news_ready and self.auto_signals_enabled),
            "paper_automation_enabled": paper_enabled,
            "paper_automation_active": paper_active,
            "paper_automation_note": (
                "Imported datasets are watched for newly appended fresh bars. Signals are paper-only, "
                "the news guard is recorded but not required, and Telegram auto-posting is disabled."
                if paper_enabled else None
            ),
            "latest_imported_bar": latest.to_dict() if (latest and paper_enabled) else None,
            "imported_bar_age_seconds": latest_bar_age_seconds if paper_enabled else None,
            "imported_max_entry_delay_seconds": self.imported_max_entry_delay,
            "dataset_learning": self.runtime.dataset_learning_status(),
            "last_dataset_learning": self._last_dataset_learning,
            "discipline": self.runtime.discipline.status(
                pair=self.runtime.pair,
                reference=(
                    latest.timestamp + timedelta(minutes=1)
                    if latest is not None and source == "IMPORTED CSV"
                    else now
                ),
                apply_rules=source != "SYNTHETIC DEMO",
            ),
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
            "recent_auto_signals": list(self._auto_signal_history[-10:]),
            "telegram": self.telegram_status(),
            "limits": {
                "entry_late_seconds": self.max_entry_delay_seconds,
                "news_blackout_before_minutes": int(self.calendar.blackout_before.total_seconds() // 60),
                "news_blackout_after_minutes": int(self.calendar.blackout_after.total_seconds() // 60),
            },
        }

    def preflight(self) -> dict[str, Any]:
        """Configuration and warm-up checks for live operation.

        This says whether the pieces are wired and how much real evidence has
        accumulated. It is deliberately not an accuracy estimate.
        """
        state = self.status()
        market = state["market"]
        calendar = state["calendar"]
        telegram = state["telegram"]
        guard = calendar["guard"]
        with self.runtime.lock:
            pair = self.runtime.pair
            source = self.runtime.source
            model = self.runtime.champion_model(pair)
            settings = dict(self.runtime.settings)
        try:
            resolved = int(self.runtime.ledger.learning_summary().get("resolved_outcomes_seen") or 0)
        except Exception:
            resolved = 0
        update_starts = int(settings.get("min_learning_samples", 30))
        calibration_starts = 60
        checks: list[dict[str, Any]] = []

        def check(identifier: str, label: str, required: bool, status: str, detail: str) -> None:
            checks.append({
                "id": identifier,
                "label": label,
                "required": required,
                "status": status,
                "detail": detail,
            })

        check(
            "market_provider",
            "Market provider",
            True,
            "ready" if market["configured"] else "blocked",
            "Twelve Data key is configured." if market["configured"]
            else "No Twelve Data key configured; only imported CSV history can drive signals.",
        )
        check(
            "provider_dataset",
            "Active provider dataset",
            True,
            "ready" if market["active_dataset_is_provider"] else "blocked",
            "The active candle dataset came from the provider feed." if market["active_dataset_is_provider"]
            else f"The active dataset is {source}, not the provider feed.",
        )
        age = market.get("latest_bar_age_seconds")
        check(
            "freshness",
            "Latest completed bar",
            True,
            "ready" if market["data_fresh"] else "blocked",
            f"Latest bar is {age:.0f}s old (limit {market['max_bar_age_seconds']}s)." if age is not None
            else "No completed candle is loaded.",
        )
        check(
            "calendar",
            "Economic calendar",
            True,
            "ready" if calendar["connected"] else "blocked",
            calendar.get("last_error") or (
                "Calendar is connected." if calendar["connected"]
                else "No Finnhub calendar key configured; news risk cannot be cleared."
            ),
        )
        check(
            "news_guard",
            "News guard",
            True,
            "ready" if guard["status"] == "CLEAR" else "blocked",
            guard["message"] if guard.get("message") else f"Guard status: {guard['status']}.",
        )
        check(
            "scheduler",
            "Automatic signals",
            True,
            "ready" if self.auto_signals_enabled else "blocked",
            "Automatic signals are enabled." if self.auto_signals_enabled
            else "ORBIX_AUTO_SIGNALS is false.",
        )
        check(
            "paper_automation",
            "Imported paper automation",
            False,
            "ready" if self.auto_imported else "warning",
            "Watches imported datasets for newly appended fresh bars; signals are paper-only and never auto-posted."
            if self.auto_imported
            else "ORBIX_AUTO_IMPORTED is false.",
        )
        model_ready = bool(model is not None and model.trained)
        check(
            "model",
            "Champion model",
            True,
            "ready" if model_ready else "blocked",
            f"Feature set v{model.feature_version}, {model.training_samples:,} training rows." if model_ready
            else "No trained champion yet. Import history, then retrain or generate a signal.",
        )
        if model_ready and model.calibrator.fitted and not model.calibrator.flat:
            calibration_status = "ready"
            calibration_detail = (
                f"Isotonic map with {model.calibrator.levels} levels from "
                f"{model.calibrator.samples:,} resolved outcomes."
            )
        elif model_ready and model.calibrator.flat:
            calibration_status = "blocked"
            shadow = self.runtime.ledger.shadow_summary(dataset_id=self.runtime.dataset_id())
            calibration_detail = (
                "Calibration collapsed to one level: the score has no usable rank signal, so the gate "
                f"abstains. {shadow['resolved']:,} shadow outcomes have been collected for learning; "
                "they are not traded, and the map can recover when the raw score regains rank structure."
            )
        else:
            calibration_status = "warning"
            remaining = max(0, calibration_starts - resolved)
            calibration_detail = (
                f"Raw probabilities. {resolved:,} of {calibration_starts} resolved outcomes needed "
                f"for a calibration fit ({remaining:,} to go)."
            )
        check("calibration", "Probability calibration", False, calibration_status, calibration_detail)
        check(
            "telegram",
            "Telegram delivery",
            False,
            "ready" if telegram["configured"] else "warning",
            f"Configured for {telegram.get('target_chat')}." if telegram["configured"]
            else "Optional channel delivery is not configured.",
        )

        ready = all(item["status"] == "ready" for item in checks if item["required"])
        shadow = self.runtime.ledger.shadow_summary(dataset_id=self.runtime.dataset_id())
        return {
            "generated_at": format_datetime(datetime.now(UTC)),
            "pair": pair,
            "source": source,
            "ready": ready,
            "checks": checks,
            "shadow": shadow,
            "dataset_learning": self.runtime.dataset_learning_status(),
            "warmup": {
                "resolved_outcomes": resolved,
                "updates_start_after": update_starts,
                "calibration_starts_after": calibration_starts,
                "to_updates": max(0, update_starts - resolved),
                "to_calibration": max(0, calibration_starts - resolved),
                "calibration_state": (
                    "flat" if model_ready and model.calibrator.flat
                    else "fitted" if model_ready and model.calibrator.fitted
                    else "raw"
                ),
            },
            "note": (
                "Preflight checks configuration and warm-up only. Readiness is not a "
                "performance forecast, and calibration describes past outcomes."
            ),
        }
