from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any


UTC = timezone.utc


def parse_datetime(value: Any) -> datetime:
    """Parse an ISO timestamp; assume UTC when the input has no timezone."""
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Candle:
    """One completed OHLCV price candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        ts = parse_datetime(self.timestamp)
        object.__setattr__(self, "timestamp", ts)
        numbers = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(float(number)) for number in numbers):
            raise ValueError("Candle prices and volume must be finite numbers")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("Candle prices must be greater than zero")
        if self.volume < 0:
            raise ValueError("Candle volume cannot be negative")
        if self.low > min(self.open, self.close):
            raise ValueError("Candle low must not be above its open or close")
        if self.high < max(self.open, self.close):
            raise ValueError("Candle high must not be below its open or close")
        if self.low > self.high:
            raise ValueError("Candle low must not be above its high")

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "Candle":
        timestamp = row.get("timestamp", row.get("time", row.get("date")))
        if timestamp is None:
            raise ValueError("Each candle needs a timestamp column")
        try:
            return cls(
                timestamp=parse_datetime(timestamp),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid candle row: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": format_datetime(self.timestamp),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    side: str
    reason: str
    probability_up: float | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    pair: str
    strategy: str
    side: str
    entry_price: float
    generated_at: datetime
    expires_at: datetime
    probability_up: float | None
    source: str
    reason: str

    @property
    def entry_time(self) -> datetime:
        """The signal becomes actionable at the next candle boundary."""
        return self.generated_at

    @property
    def prediction_time(self) -> datetime:
        """The time at which the predicted direction is evaluated."""
        return self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "pair": self.pair,
            "strategy": self.strategy,
            "side": self.side,
            # Keep the price for paper settlement/model labels, while the UI
            # emphasizes time-based entry and prediction timestamps.
            "entry_price": self.entry_price,
            "entry_time": format_datetime(self.entry_time),
            "generated_at": format_datetime(self.generated_at),
            "prediction_time": format_datetime(self.prediction_time),
            "expires_at": format_datetime(self.expires_at),
            "horizon": max(1, int((self.expires_at - self.generated_at).total_seconds() // 60)),
            "probability_up": self.probability_up,
            "source": self.source,
            "reason": self.reason,
            "is_live": False,
        }
