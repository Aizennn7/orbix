from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .backtest import run_walk_forward_backtest
from .domain import format_datetime
from .strategies import EXECUTABLE_STRATEGIES


UTC = timezone.utc
FUTURE_STRATEGY = "orbix_confluence"
FUTURE_PAYOUT = 0.80
MAX_REPLAY_BARS = 8
EVIDENCE_WINDOW = 900

# Liquidity ordering only. Turnover says how easily an instrument trades, not
# how predictable its next minute is.
PAIR_GUIDE: dict[str, dict[str, Any]] = {
    "EUR/USD": {
        "rank": 1,
        "liquidity": "Deepest FX pair in the BIS Triennial Survey; tightest typical spread.",
        "notes": "Best default for M1 cost control, not a prediction advantage.",
    },
    "USD/JPY": {
        "rank": 2,
        "liquidity": "Second most traded pair; active in the Asian session.",
        "notes": "Liquidity is concentrated in Tokyo/London hours.",
    },
    "GBP/USD": {
        "rank": 3,
        "liquidity": "Major pair with a wider typical spread than EUR/USD.",
        "notes": "More session-dependent volatility.",
    },
    "AUD/USD": {
        "rank": 4,
        "liquidity": "Liquid major, thinner outside Sydney/Tokyo hours.",
        "notes": "Spread and gap risk rise in the off-session.",
    },
    "USD/INR": {
        "rank": 5,
        "liquidity": "Managed, onshore-driven market with limited 24-hour depth.",
        "notes": "Not a typical M1 binary-options instrument.",
    },
}

_EVIDENCE_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}


def _evidence(runtime, dataset_id: str) -> dict[str, Any] | None:
    key = (dataset_id, FUTURE_STRATEGY, EVIDENCE_WINDOW)
    cached = _EVIDENCE_CACHE.get(key)
    if cached is not None:
        return cached
    candles = runtime.backtest_candles(EVIDENCE_WINDOW)
    if len(candles) < 120:
        return None
    try:
        report = run_walk_forward_backtest(
            candles, strategy_id=FUTURE_STRATEGY, horizon=1, payout=FUTURE_PAYOUT
        )
    except ValueError:
        # Not enough labelled candles to train; the page still loads without
        # manufacturing an evidence number.
        return None
    summary = {
        "test_candles": report["test_candles"],
        "signals": report["signals"],
        "decisive_trades": report["decisive_trades"],
        "win_rate": report["win_rate"],
        "win_rate_ci95": report["win_rate_ci95"],
        "break_even_win_rate": report["break_even_win_rate"],
        "net_units": report["net_units"],
        "max_drawdown_units": report["max_drawdown_units"],
        "signal_coverage": report["signal_coverage"],
        "assumed_payout": report["assumed_payout"],
        "window_candles": len(candles),
    }
    if len(_EVIDENCE_CACHE) > 4:
        _EVIDENCE_CACHE.clear()
    _EVIDENCE_CACHE[key] = summary
    return summary


def _replay(runtime, count: int) -> list[dict[str, Any]]:
    candles = runtime.candles
    if len(candles) < 40:
        return []
    model = runtime.models.get(runtime.pair)
    model = model if model is not None and getattr(model, "trained", False) else None
    start = max(40, len(candles) - max(1, min(count, MAX_REPLAY_BARS)))
    rows: list[dict[str, Any]] = []
    for index in range(start, len(candles)):
        try:
            signal, _, _ = runtime.engine.generate_signal(
                candles[: index + 1],
                runtime.pair,
                strategy_id=FUTURE_STRATEGY,
                source=runtime.source,
                horizon=1,
                model=model,
            )
        except ValueError:
            continue
        rows.append({
            "entry_time": format_datetime(signal.entry_time),
            "prediction_time": format_datetime(signal.prediction_time),
            "side": signal.side,
            "direction": {"BUY": "CALL", "SELL": "PUT"}.get(signal.side, "—"),
            "probability_up": signal.probability_up,
            "reason": signal.reason,
            "source": runtime.source,
            "replayed": True,
        })
    return rows


def build_future_plan(runtime, automation, count: int = 6, now: datetime | None = None) -> dict[str, Any]:
    """Forward schedule for one pair plus an honest replay of recent closed bars.

    The plan never invents a direction for a candle that has not closed. An
    upcoming minute is listed as ARMED or BLOCKED; the direction is only filled
    in once the bar closes and the six-check gate has actually run.
    """
    count = max(1, min(int(count), MAX_REPLAY_BARS))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    status = automation.status()
    pair = runtime.pair
    dataset_id = runtime.dataset_id()
    latest = runtime.candles[-1] if runtime.candles else None

    guard = status["calendar"]["guard"]
    market = status["market"]
    reasons: list[str] = []
    if not market.get("configured"):
        reasons.append("No server-side market provider is configured (ORBIX_TWELVE_DATA_API_KEY).")
    if not market.get("active_dataset_is_provider"):
        reasons.append("The active dataset is not the provider feed.")
    if not market.get("data_fresh"):
        reasons.append("The latest completed bar is older than the freshness limit.")
    if guard.get("status") != "CLEAR":
        reasons.append(f"News risk is not clear: {guard.get('status')}.")
    ready = not reasons and bool(status.get("automatic_signals_active"))

    base = current.replace(second=0, microsecond=0)
    windows = []
    for offset in range(1, count + 1):
        entry = base + timedelta(minutes=offset)
        windows.append({
            "index": offset,
            "entry_time": format_datetime(entry),
            "prediction_time": format_datetime(entry + timedelta(minutes=1)),
            "status": "ARMED" if ready else "BLOCKED",
            "direction": None,
            "note": (
                "Direction is revealed only after the previous M1 candle closes and the gate runs."
                if ready else "Not available until live data and the event calendar are ready."
            ),
        })

    guide = PAIR_GUIDE.get(pair, {"rank": None, "liquidity": "No liquidity note for this pair.", "notes": ""})
    alternatives = [
        {
            "pair": name,
            "active": name == pair,
            "liquidity_rank": info["rank"],
            "liquidity": info["liquidity"],
            "notes": info["notes"],
        }
        for name, info in sorted(PAIR_GUIDE.items(), key=lambda item: item[1]["rank"])
    ]
    for item in alternatives:
        item["has_local_data"] = item["pair"] == pair

    evidence = _evidence(runtime, dataset_id)
    return {
        "generated_at": format_datetime(current),
        "strategy": FUTURE_STRATEGY,
        "strategy_name": next(
            (item["name"] for item in EXECUTABLE_STRATEGIES if item["id"] == FUTURE_STRATEGY),
            FUTURE_STRATEGY,
        ),
        "focus": {
            "pair": pair,
            "liquidity_rank": guide["rank"],
            "why": guide["liquidity"],
            "notes": guide["notes"],
            "accuracy_note": (
                "No pair is more likely to be directionally accurate. Liquidity lowers spread and gap cost; "
                "it does not raise the hit rate. Any '95% accuracy' screenshot is marketing, not a measured edge."
            ),
            "single_pair_rule": (
                "Only the active pair is analysed so the gate never mixes two candle streams. "
                "Switch pairs by importing that pair's CSV from the Data page."
            ),
        },
        "data": {
            "source": runtime.source,
            "pair": pair,
            "candles": runtime.candle_count,
            "latest_candle": latest.to_dict() if latest else None,
            "live": bool(market.get("data_fresh") and market.get("active_dataset_is_provider")),
            "note": "Synthetic demo data is practice data and never market evidence." if runtime.source == "SYNTHETIC DEMO" else "",
        },
        "readiness": {"ready": ready, "reasons": reasons},
        "windows": windows,
        "replay": _replay(runtime, count),
        "evidence": evidence,
        "alternatives": alternatives,
        "disclaimer": (
            "Research and paper testing only. Signals can lose. The six-check gate is not a calibrated probability, "
            "and the replay rows are historical re-runs, not predictions."
        ),
    }
