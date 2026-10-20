from __future__ import annotations

import math
from statistics import mean, median, pstdev

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


def _adx(candles: list[Candle], period: int = 14) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calculate a transparent rolling ADX and its directional indicators."""
    true_ranges: list[float] = []
    positive_moves: list[float] = []
    negative_moves: list[float] = []
    for index, candle in enumerate(candles):
        previous = candles[index - 1] if index else candle
        upward = candle.high - previous.high
        downward = previous.low - candle.low
        positive_moves.append(upward if upward > downward and upward > 0 else 0.0)
        negative_moves.append(downward if downward > upward and downward > 0 else 0.0)
        true_ranges.append(max(candle.high - candle.low, abs(candle.high - previous.close), abs(candle.low - previous.close)))

    adx: list[float | None] = [None] * len(candles)
    plus_di: list[float | None] = [None] * len(candles)
    minus_di: list[float | None] = [None] * len(candles)
    dx: list[float | None] = [None] * len(candles)
    for index in range(period - 1, len(candles)):
        start = index - period + 1
        true_range = sum(true_ranges[start : index + 1])
        if true_range <= 0:
            continue
        positive = 100.0 * sum(positive_moves[start : index + 1]) / true_range
        negative = 100.0 * sum(negative_moves[start : index + 1]) / true_range
        plus_di[index] = positive
        minus_di[index] = negative
        denominator = positive + negative
        dx[index] = 100.0 * abs(positive - negative) / denominator if denominator else 0.0
        dx_start = index - period + 1
        if dx_start >= period - 1 and all(value is not None for value in dx[dx_start : index + 1]):
            adx[index] = mean(value for value in dx[dx_start : index + 1] if value is not None)
    return adx, plus_di, minus_di


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
    adx_values, plus_di_values, minus_di_values = _adx(candles, 14)
    returns: list[float] = [0.0]
    for index in range(1, len(closes)):
        returns.append((closes[index] / closes[index - 1]) - 1.0)

    result: list[dict[str, float] | None] = []
    for index, candle in enumerate(candles):
        if (
            index < 20
            or ema_fast[index] is None
            or ema_slow[index] is None
            or rsi_values[index] is None
            or atr_values[index] is None
            or adx_values[index] is None
            or plus_di_values[index] is None
            or minus_di_values[index] is None
        ):
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
        recent_volume = [max(0.0, item.volume) for item in candles[max(0, index - 19) : index + 1]]
        typical_volume = median(recent_volume) if recent_volume else 0.0
        volume_ratio = candle.volume / typical_volume if typical_volume > 0 else 1.0
        candle_range = candle.high - candle.low
        close_position = (candle.close - candle.low) / candle_range if candle_range > 0 else 0.5

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
            "adx": adx_values[index],
            "plus_di": plus_di_values[index],
            "minus_di": minus_di_values[index],
            "volume_ratio": volume_ratio,
            "close_position": close_position,
            "body_atr_ratio": abs(candle.close - candle.open) / max(atr_values[index], 1e-12),
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
