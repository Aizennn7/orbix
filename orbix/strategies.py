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
) -> Decision:
    if features is None:
        return Decision("NO SIGNAL", "Not enough completed candles to calculate indicators.", probability_up)

    ema_fast = features["ema_fast"]
    ema_slow = features["ema_slow"]
    rsi = features["rsi"]
    rsi_previous = features["rsi_previous"]
    close = candle.close

    if strategy_id == "ml_consensus":
        if probability_up is None:
            return Decision("NO SIGNAL", "The model is not ready; wait for more data.", None)
        if probability_up >= buy_threshold and ema_fast > ema_slow and features["return_1"] > 0:
            return Decision("BUY", "Model leans up and the short-term EMA trend agrees.", probability_up)
        if probability_up <= sell_threshold and ema_fast < ema_slow and features["return_1"] < 0:
            return Decision("SELL", "Model leans down and the short-term EMA trend agrees.", probability_up)
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
