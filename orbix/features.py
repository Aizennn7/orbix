from __future__ import annotations

import math
from statistics import mean, pstdev

from .domain import Candle


FEATURE_NAMES = (
    "return_1",
    "return_3",
    "return_5",
    "ema_gap",
    "rsi_scaled",
    "range_pct",
    "body_pct",
    "volatility_10",
)


def _ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = mean(values[:period])
    result[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        result[index] = current
    return result


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def score(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        relative_strength = gain / loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    result[period] = score(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = ((average_gain * (period - 1)) + max(change, 0.0)) / period
        average_loss = ((average_loss * (period - 1)) + max(-change, 0.0)) / period
        result[index] = score(average_gain, average_loss)
    return result


def _atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1].close if index else candle.close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    result: list[float | None] = [None] * len(candles)
    for index in range(period - 1, len(candles)):
        result[index] = mean(true_ranges[index - period + 1 : index + 1])
    return result


def _rolling_std(values: list[float], end: int, period: int) -> float | None:
    start = end - period + 1
    if start < 0:
        return None
    window = values[start : end + 1]
    if len(window) != period:
        return None
    return pstdev(window)


def compute_features(candles: list[Candle]) -> list[dict[str, float] | None]:
    """Compute indicators from current and past candles only (no future data)."""
    closes = [candle.close for candle in candles]
    ema_fast = _ema(closes, 5)
    ema_slow = _ema(closes, 20)
    rsi_values = _rsi(closes, 14)
    atr_values = _atr(candles, 14)
    returns: list[float] = [0.0]
    for index in range(1, len(closes)):
        returns.append((closes[index] / closes[index - 1]) - 1.0)

    result: list[dict[str, float] | None] = []
    for index, candle in enumerate(candles):
        if index < 20 or ema_fast[index] is None or ema_slow[index] is None or rsi_values[index] is None or atr_values[index] is None:
            result.append(None)
            continue

        previous_close = closes[index - 1]
        return_3 = (closes[index] / closes[index - 3]) - 1.0 if index >= 3 else 0.0
        return_5 = (closes[index] / closes[index - 5]) - 1.0 if index >= 5 else 0.0
        volatility = _rolling_std(returns, index, 10)
        if volatility is None:
            result.append(None)
            continue
        recent_closes = closes[index - 19 : index + 1]
        band_mid = mean(recent_closes)
        band_width = 2.0 * pstdev(recent_closes)
        prior_high = max(item.high for item in candles[index - 20 : index])
        prior_low = min(item.low for item in candles[index - 20 : index])
        rsi_previous = rsi_values[index - 1] if rsi_values[index - 1] is not None else rsi_values[index]

        feature = {
            "return_1": returns[index],
            "return_3": return_3,
            "return_5": return_5,
            "ema_gap": (ema_fast[index] - ema_slow[index]) / candle.close,
            "rsi_scaled": (rsi_values[index] - 50.0) / 50.0,
            "range_pct": (candle.high - candle.low) / candle.close,
            "body_pct": (candle.close - candle.open) / previous_close,
            "volatility_10": volatility,
            "ema_fast": ema_fast[index],
            "ema_slow": ema_slow[index],
            "ema_fast_previous": ema_fast[index - 1] if ema_fast[index - 1] is not None else ema_fast[index],
            "ema_slow_previous": ema_slow[index - 1] if ema_slow[index - 1] is not None else ema_slow[index],
            "rsi": rsi_values[index],
            "rsi_previous": rsi_previous,
            "atr": atr_values[index],
            "atr_pct": atr_values[index] / candle.close,
            "bb_mid": band_mid,
            "bb_upper": band_mid + band_width,
            "bb_lower": band_mid - band_width,
            "prior_high_20": prior_high,
            "prior_low_20": prior_low,
        }
        if not all(math.isfinite(value) for value in feature.values()):
            result.append(None)
        else:
            result.append(feature)
    return result
