from __future__ import annotations

from .domain import Candle, Decision


# Research catalogue from public educational material. These are candidates,
# not evidence of profitability and not all are implemented as executable rules.
STRATEGY_CATALOG = [
    ("EMA crossover trend", "trend"),
    ("5-8-13 SMA ribbon", "trend"),
    ("Higher-timeframe trend + M1 entry", "trend"),
    ("Moving-average pullback", "trend"),
    ("Trend-filtered RSI pullback", "trend"),
    ("Ichimoku Cloud", "trend"),
    ("RSI 50-line continuation", "momentum"),
    ("RSI 30/70 turn", "momentum"),
    ("RSI divergence", "momentum"),
    ("Stochastic turn", "momentum"),
    ("MACD crossover", "momentum"),
    ("MACD + RSI confirmation", "momentum"),
    ("Bollinger squeeze breakout", "breakout"),
    ("Bollinger-band breakout", "breakout"),
    ("Keltner-channel breakout", "breakout"),
    ("ATR volatility breakout", "breakout"),
    ("Opening-range breakout", "breakout"),
    ("Breakout and retest", "breakout"),
    ("Support bounce", "range"),
    ("Resistance rejection", "range"),
    ("Range-bound trading", "range"),
    ("Bollinger bounce", "range"),
    ("RSI + support/resistance reversal", "range"),
    ("Stochastic + support/resistance", "range"),
    ("Inside-bar breakout", "pattern"),
    ("Bull-flag continuation", "pattern"),
    ("Bear-flag continuation", "pattern"),
    ("Triangle breakout", "pattern"),
    ("Head-and-shoulders break", "pattern"),
    ("RSI/MACD momentum scalping", "scalping"),
]

EXECUTABLE_STRATEGIES = [
    {
        "id": "orbix_confluence",
        "name": "Orbix Confluence Gate",
        "category": "regime-aware trend continuation",
        "description": "Original 6-check rule set: EMA regime, multi-bar momentum, RSI band, directional ADX, candle quality, and prior-range breakout. Requires 5 checks; a prequential model vetoes disagreement when trained. Abstains in extreme volatility.",
        "prototype": True,
    },
    {
        "id": "ml_consensus",
        "name": "Orbix ML + trend agreement",
        "category": "machine learning",
        "description": "Only signals when the model estimate and EMA direction agree.",
        "prototype": True,
    },
    {
        "id": "trend_pullback",
        "name": "Trend pullback",
        "category": "trend",
        "description": "EMA direction, RSI range, and candle direction must agree.",
        "prototype": True,
    },
    {
        "id": "range_reversal",
        "name": "Range reversal",
        "category": "range",
        "description": "Bollinger-band stretch plus an RSI turn.",
        "prototype": True,
    },
    {
        "id": "breakout",
        "name": "20-candle breakout",
        "category": "breakout",
        "description": "A close beyond the previous 20-candle high or low.",
        "prototype": True,
    },
]


def strategy_catalog_payload() -> dict:
    approximate_prototypes = {"Trend-filtered RSI pullback", "Bollinger bounce"}
    candidates = [
        {
            "number": index + 1,
            "name": name,
            "category": category,
            "status": "prototype approximation" if name in approximate_prototypes else "research",
        }
        for index, (name, category) in enumerate(STRATEGY_CATALOG)
    ]
    return {"executable": EXECUTABLE_STRATEGIES, "candidates": candidates}


def decide(
    strategy_id: str,
    candle: Candle,
    previous_close: float,
    features: dict[str, float] | None,
    probability_up: float | None,
    buy_threshold: float = 0.57,
    sell_threshold: float = 0.43,
    probability_calibrated: bool | None = None,
    uncertainty: float | None = None,
    max_uncertainty: float = 0.10,
    calibration_flat: bool = False,
) -> Decision:
    if features is None:
        return Decision("NO SIGNAL", "Not enough completed candles to calculate indicators.", probability_up)
    if uncertainty is not None and uncertainty > max_uncertainty:
        return Decision(
            "NO SIGNAL",
            f"Ensemble disagreement is {uncertainty:.3f}, wider than the {max_uncertainty:.2f} stability limit; abstaining instead of guessing.",
            probability_up,
        )
    if calibration_flat:
        return Decision(
            "NO SIGNAL",
            "Model calibration is flat: the score has no usable rank signal on recorded outcomes, so the gate abstains instead of trading a constant probability.",
            probability_up,
        )

    ema_fast = features["ema_fast"]
    ema_slow = features["ema_slow"]
    rsi = features["rsi"]
    rsi_previous = features["rsi_previous"]
    close = candle.close

    if strategy_id == "orbix_confluence":
        atr_pct = features["atr_pct"]
        if atr_pct <= 0.0 or atr_pct > 0.004:
            return Decision(
                "NO SIGNAL",
                "Orbix Confluence Gate abstains: one-minute volatility is outside its pre-set operating band.",
                probability_up,
            )

        checks = {
            "trend": {
                "BUY": ema_fast > ema_slow and close > ema_fast,
                "SELL": ema_fast < ema_slow and close < ema_fast,
            },
            "momentum": {
                "BUY": features["return_1"] > 0 and features["return_3"] > 0,
                "SELL": features["return_1"] < 0 and features["return_3"] < 0,
            },
            "RSI band": {
                "BUY": 50.0 <= rsi <= 68.0,
                "SELL": 32.0 <= rsi <= 50.0,
            },
            "directional ADX": {
                "BUY": features["adx"] >= 18.0 and features["plus_di"] > features["minus_di"],
                "SELL": features["adx"] >= 18.0 and features["minus_di"] > features["plus_di"],
            },
            "candle quality": {
                "BUY": features["body_atr_ratio"] >= 0.12 and features["body_atr_ratio"] <= 1.5 and features["close_position"] >= 0.65,
                "SELL": features["body_atr_ratio"] >= 0.12 and features["body_atr_ratio"] <= 1.5 and features["close_position"] <= 0.35,
            },
            "20-bar breakout": {
                "BUY": close > features["prior_high_20"],
                "SELL": close < features["prior_low_20"],
            },
        }
        scores = {side: sum(criteria[side] for criteria in checks.values()) for side in ("BUY", "SELL")}
        side = max(scores, key=scores.get)
        score = scores[side]
        model_agrees = probability_up is None or (
            probability_up >= 0.53 if side == "BUY" else probability_up <= 0.47
        )
        if score >= 5 and checks["trend"][side] and checks["directional ADX"][side] and model_agrees:
            aligned = ", ".join(name for name, criteria in checks.items() if criteria[side])
            if probability_up is None:
                learned_guard = "; model not ready, six-rule gate only"
            elif probability_calibrated:
                learned_guard = f"; calibrated model read {probability_up:.2f} agrees"
            else:
                learned_guard = f"; raw model read {probability_up:.2f} agrees (not yet calibrated)"
            return Decision(
                side,
                f"Orbix Confluence Gate {score}/6: {aligned}{learned_guard}. Rule score is not a calibrated probability.",
                probability_up,
            )
        if score >= 5 and not model_agrees:
            label = "calibrated" if probability_calibrated else "raw"
            return Decision(
                "NO SIGNAL",
                f"Orbix Confluence Gate abstains: {side.lower()} rules passed {score}/6, but the prequential model vetoed disagreement ({label} model read {probability_up:.2f}).",
                probability_up,
            )
        return Decision(
            "NO SIGNAL",
            f"Orbix Confluence Gate abstains: strongest side passed {score}/6 checks; at least 5/6 plus trend and ADX alignment are required.",
            probability_up,
        )

    if strategy_id == "ml_consensus":
        if probability_up is None:
            return Decision("NO SIGNAL", "The model is not ready; wait for more data.", None)
        basis = "calibrated" if probability_calibrated else "raw"
        if probability_up >= buy_threshold and ema_fast > ema_slow and features["return_1"] > 0:
            return Decision(
                "BUY",
                f"Model leans up ({basis} probability {probability_up:.2f}) and the short-term EMA trend agrees.",
                probability_up,
            )
        if probability_up <= sell_threshold and ema_fast < ema_slow and features["return_1"] < 0:
            return Decision(
                "SELL",
                f"Model leans down ({basis} probability {probability_up:.2f}) and the short-term EMA trend agrees.",
                probability_up,
            )
        return Decision("NO SIGNAL", "Model and trend filters do not agree strongly enough.", probability_up)

    if strategy_id == "trend_pullback":
        bullish_reclaim = previous_close <= features["ema_fast_previous"] and close > ema_fast
        bearish_reclaim = previous_close >= features["ema_fast_previous"] and close < ema_fast
        if ema_fast > ema_slow and bullish_reclaim and 45.0 <= rsi <= 68.0:
            return Decision("BUY", "Uptrend pullback reclaimed the fast EMA with RSI confirmation.", probability_up)
        if ema_fast < ema_slow and bearish_reclaim and 32.0 <= rsi <= 55.0:
            return Decision("SELL", "Downtrend pullback rejected the fast EMA with RSI confirmation.", probability_up)
        return Decision("NO SIGNAL", "No confirmed trend pullback and EMA reclaim is present.", probability_up)

    if strategy_id == "range_reversal":
        if close <= features["bb_lower"] and rsi < 35.0 and rsi > rsi_previous:
            return Decision("BUY", "Price stretched below its recent range while RSI started turning up.", probability_up)
        if close >= features["bb_upper"] and rsi > 65.0 and rsi < rsi_previous:
            return Decision("SELL", "Price stretched above its recent range while RSI started turning down.", probability_up)
        return Decision("NO SIGNAL", "No confirmed range-edge reversal is present.", probability_up)

    if strategy_id == "breakout":
        if close > features["prior_high_20"]:
            return Decision("BUY", "Close broke above the previous 20-candle high.", probability_up)
        if close < features["prior_low_20"]:
            return Decision("SELL", "Close broke below the previous 20-candle low.", probability_up)
        return Decision("NO SIGNAL", "Price is still inside the recent 20-candle range.", probability_up)

    raise ValueError(f"Unknown strategy: {strategy_id}")
