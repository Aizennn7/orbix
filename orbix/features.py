from __future__ import annotations

import math

from .domain import Candle


# Bumped whenever the model input changes. Saved models with a different
# version are rejected and retrained instead of being silently misinterpreted.
FEATURE_VERSION = 2

FEATURE_NAMES = (
    "return_1",
    "return_3",
    "return_5",
    "return_10",
    "ema_gap",
    "ema_slope",
    "rsi_scaled",
    "rsi_slope",
    "range_pct",
    "body_pct",
    "wick_upper",
    "wick_lower",
    "volatility_10",
    "volatility_ratio",
    "atr_ratio",
    "adx_scaled",
    "di_spread",
    "close_position",
    "body_atr_ratio",
    "volume_ratio_log",
    "vwap_dev",
    "range_position_20",
    "streak_scaled",
    "autocorr_20",
    "hour_sin",
    "hour_cos",
)

def _mean(values) -> float:
    # statistics.mean is exact and therefore slow on floats; these indicators
    # only need ordinary float precision.
    items = values if isinstance(values, (list, tuple)) else list(values)
    return sum(items) / len(items)


def _pstdev(values) -> float:
    centre = _mean(values)
    return math.sqrt(sum((value - centre) ** 2 for value in values) / len(values))


def _median(values) -> float:
    items = sorted(values)
    middle = len(items) // 2
    return items[middle] if len(items) % 2 else (items[middle - 1] + items[middle]) / 2.0


FEATURE_LABELS = {
    "return_1": "1-bar return",
    "return_3": "3-bar return",
    "return_5": "5-bar return",
    "return_10": "10-bar return",
    "ema_gap": "EMA 5/20 gap",
    "ema_slope": "EMA gap slope",
    "rsi_scaled": "RSI (centred)",
    "rsi_slope": "RSI slope",
    "range_pct": "Candle range",
    "body_pct": "Candle body",
    "wick_upper": "Upper wick",
    "wick_lower": "Lower wick",
    "volatility_10": "10-bar volatility",
    "volatility_ratio": "Short/long volatility",
    "atr_ratio": "ATR vs its average",
    "adx_scaled": "ADX (scaled)",
    "di_spread": "+DI minus -DI",
    "close_position": "Close inside range",
    "body_atr_ratio": "Body vs ATR",
    "volume_ratio_log": "Log volume ratio",
    "vwap_dev": "Distance from VWAP",
    "range_position_20": "Position in 20-bar range",
    "streak_scaled": "Win streak",
    "autocorr_20": "Return autocorrelation",
    "hour_sin": "Hour of day (sin)",
    "hour_cos": "Hour of day (cos)",
}


def _ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = _mean(values[:period])
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
        result[index] = _mean(true_ranges[index - period + 1 : index + 1])
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
            adx[index] = _mean(value for value in dx[dx_start : index + 1] if value is not None)
    return adx, plus_di, minus_di


def _rolling_std(values: list[float], end: int, period: int) -> float | None:
    start = end - period + 1
    if start < 0:
        return None
    window = values[start : end + 1]
    if len(window) != period:
        return None
    return _pstdev(window)


def _autocorrelation(values: list[float]) -> float:
    """Lag-1 autocorrelation; 0.0 when the series has no variance."""
    if len(values) < 10:
        return 0.0
    left = values[:-1]
    right = values[1:]
    left_mean = _mean(left)
    right_mean = _mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    if denominator <= 1e-15:
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))


def _streak(closes: list[float], index: int) -> int:
    streak = 0
    cursor = index
    while cursor >= 1:
        up = closes[cursor] > closes[cursor - 1]
        down = closes[cursor] < closes[cursor - 1]
        if not up and not down:
            break
        if streak == 0:
            streak = 1 if up else -1
        elif (up and streak > 0) or (down and streak < 0):
            streak += 1 if up else -1
        else:
            break
        cursor -= 1
    return streak


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
        return_10 = (closes[index] / closes[index - 10]) - 1.0 if index >= 10 else 0.0
        volatility = _rolling_std(returns, index, 10)
        long_volatility = _rolling_std(returns, index, 20)
        if volatility is None or long_volatility is None:
            result.append(None)
            continue
        recent_closes = closes[index - 19 : index + 1]
        band_mid = _mean(recent_closes)
        band_width = 2.0 * _pstdev(recent_closes)
        prior_high = max(item.high for item in candles[index - 20 : index])
        prior_low = min(item.low for item in candles[index - 20 : index])
        rsi_previous = rsi_values[index - 1] if rsi_values[index - 1] is not None else rsi_values[index]
        recent_volume = [max(0.0, item.volume) for item in candles[max(0, index - 19) : index + 1]]
        typical_volume = _median(recent_volume) if recent_volume else 0.0
        volume_ratio = candle.volume / typical_volume if typical_volume > 0 else 1.0
        candle_range = candle.high - candle.low
        close_position = (candle.close - candle.low) / candle_range if candle_range > 0 else 0.5
        atr_window = [value for value in atr_values[index - 19 : index + 1] if value is not None]
        atr_average = _mean(atr_window) if atr_window else atr_values[index]
        previous_gap = 0.0
        if ema_fast[index - 1] is not None and ema_slow[index - 1] is not None:
            previous_gap = (ema_fast[index - 1] - ema_slow[index - 1]) / previous_close
        current_gap = (ema_fast[index] - ema_slow[index]) / candle.close
        volume_weight = sum(
            max(0.0, item.volume) * item.close for item in candles[index - 19 : index + 1]
        )
        volume_total = sum(max(0.0, item.volume) for item in candles[index - 19 : index + 1])
        vwap = volume_weight / volume_total if volume_total > 0 else band_mid
        range_span = prior_high - prior_low
        range_position = ((close_position - 0.5) if range_span <= 0 else ((candle.close - prior_low) / range_span - 0.5))
        hour_fraction = candle.timestamp.hour + candle.timestamp.minute / 60.0
        angle = 2.0 * math.pi * hour_fraction / 24.0
        body = candle.close - candle.open
        streak = _streak(closes, index)

        feature = {
            # Model inputs (ordered in FEATURE_NAMES).
            "return_1": returns[index],
            "return_3": return_3,
            "return_5": return_5,
            "return_10": return_10,
            "ema_gap": current_gap,
            "ema_slope": (current_gap - previous_gap) * 1000.0,
            "rsi_scaled": (rsi_values[index] - 50.0) / 50.0,
            "rsi_slope": (rsi_values[index] - rsi_previous) / 10.0,
            "range_pct": candle_range / candle.close,
            "body_pct": body / previous_close,
            "wick_upper": (candle.high - max(candle.open, candle.close)) / candle.close,
            "wick_lower": (min(candle.open, candle.close) - candle.low) / candle.close,
            "volatility_10": volatility,
            "volatility_ratio": volatility / long_volatility if long_volatility > 0 else 1.0,
            "atr_ratio": atr_values[index] / atr_average if atr_average else 1.0,
            "adx_scaled": adx_values[index] / 100.0,
            "di_spread": (plus_di_values[index] - minus_di_values[index]) / 100.0,
            "close_position": close_position,
            "body_atr_ratio": abs(body) / max(atr_values[index], 1e-12),
            "volume_ratio_log": math.log1p(volume_ratio),
            "vwap_dev": (candle.close - vwap) / candle.close,
            "range_position_20": range_position,
            "streak_scaled": max(-1.0, min(1.0, streak / 5.0)),
            "autocorr_20": _autocorrelation(returns[index - 19 : index + 1]),
            "hour_sin": math.sin(angle),
            "hour_cos": math.cos(angle),
            # Strategy-facing values kept for the transparent rule sets.
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
