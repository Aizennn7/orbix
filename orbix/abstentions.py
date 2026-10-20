from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc

# UTC hour buckets. These are session labels for reporting, not a claim that
# any session is more predictable.
_SESSION_BY_HOUR = (
    ("Asian", 0, 7),
    ("London", 7, 12),
    ("London/NY overlap", 12, 16),
    ("New York", 16, 21),
    ("Rollover", 21, 23),
    ("Asian", 23, 24),
)

SESSION_ORDER = ("London/NY overlap", "London", "New York", "Asian", "Rollover")

# Reason codes are derived from the strategy text so the ledger stays stable if
# wording changes slightly. Unknown wording falls back to "other" rather than
# being silently dropped.
_REASON_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("news_blackout", ("blackout", "high-impact event")),
    ("stale_bar", ("stale bar",)),
    ("duplicate_suppressed", ("duplicate suppressed", "already has a signal recorded")),
    ("volatility_band", ("volatility is outside",)),
    ("uncertainty", ("ensemble disagreement", "stability limit")),
    ("flat_calibration", ("calibration is flat", "no usable rank signal")),
    ("model_veto", ("model vetoed", "model disagrees", "model and trend filters do not agree")),
    ("model_not_ready", ("model is not ready",)),
    ("score_below_threshold", ("checks; at least 5/6", "strongest side passed")),
    ("insufficient_history", ("not enough completed candles", "at least 40 completed candles", "not enough labeled candles")),
    ("direction_conflict", ("do not agree strongly enough", "disagrees")),
    ("no_pullback", ("no confirmed trend pullback",)),
    ("no_reversal", ("no confirmed range-edge reversal",)),
    ("inside_range", ("still inside the recent 20-candle range",)),
    ("provider_unavailable", ("provider", "calendar", "not configured")),
)

REASON_LABELS = {
    "news_blackout": "High-impact news blackout",
    "stale_bar": "Stale / late bar skipped",
    "duplicate_suppressed": "Duplicate entry suppressed",
    "volatility_band": "Volatility outside operating band",
    "uncertainty": "Ensemble disagreement above limit",
    "flat_calibration": "Calibration flat — no rank signal",
    "model_veto": "Learned model veto",
    "model_not_ready": "Model not trained yet",
    "score_below_threshold": "Confluence score below 5/6",
    "insufficient_history": "Not enough closed candles",
    "direction_conflict": "Directions disagree",
    "no_pullback": "No confirmed pullback",
    "no_reversal": "No confirmed reversal",
    "inside_range": "Price inside 20-bar range",
    "provider_unavailable": "Provider or calendar unavailable",
    "other": "Other / unclassified",
}


def classify_reason(reason: str | None) -> str:
    text = str(reason or "").strip().casefold()
    if not text:
        return "other"
    for code, needles in _REASON_RULES:
        if any(needle in text for needle in needles):
            return code
    return "other"


def session_for(value: datetime | int | str | None) -> str:
    """Return the reporting session for a UTC datetime, hour, or ISO string."""
    if value is None:
        hour = datetime.now(UTC).hour
    elif isinstance(value, int):
        hour = value % 24
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "Unknown"
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        hour = moment.astimezone(UTC).hour
    else:
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        hour = moment.astimezone(UTC).hour
    for name, start, end in _SESSION_BY_HOUR:
        if start <= hour < end:
            return name
    return "Unknown"


def hour_bucket(value: datetime | str | None) -> int:
    if value is None:
        return datetime.now(UTC).hour
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).hour
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).hour


def is_no_signal(signal: Any) -> bool:
    side = getattr(signal, "side", None)
    return str(side or "").upper() == "NO SIGNAL"
