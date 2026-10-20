from __future__ import annotations

from .domain import Candle
from .features import compute_features
from .metrics import wilson_interval
from .model import OnlineLogisticModel, train_from_candles
from .strategies import decide, EXECUTABLE_STRATEGIES


TARGET_WIN_RATE = 0.95
TARGET_WINDOW = 20


def run_walk_forward_backtest(
    candles: list[Candle],
    strategy_id: str = "ml_consensus",
    horizon: int = 1,
    payout: float = 0.80,
    threshold: float = 0.57,
    retrain_every: int = 60,
) -> dict:
    """Chronological dry-run test; model sees only outcomes that have matured."""
    if len(candles) < 120:
        raise ValueError("At least 120 candles are required for a walk-forward test")
    if strategy_id not in {item["id"] for item in EXECUTABLE_STRATEGIES}:
        raise ValueError(f"Strategy '{strategy_id}' is not executable yet")
    if horizon < 1 or horizon > 60:
        raise ValueError("Expiry must be between 1 and 60 candles")
    if not 0.0 < payout <= 1.0:
        raise ValueError("Assumed payout must be greater than 0 and no more than 1")
    if not 0.5 < threshold < 1.0:
        raise ValueError("Signal threshold must be greater than 0.5 and less than 1")
    if retrain_every < 1:
        raise ValueError("Retraining interval must be at least one candle")

    rows = compute_features(candles)
    test_start = max(60, int(len(candles) * 0.60))
    model: OnlineLogisticModel | None = None
    if strategy_id == "ml_consensus":
        model = train_from_candles(candles, rows, horizon=horizon, end_index=test_start - 1)

    wins = losses = ties = 0
    net_units = 0.0
    trades: list[dict] = []
    last_retrained = test_start
    for index in range(test_start, len(candles) - horizon):
        # Online test-then-learn: update only with a label whose expiry has passed.
        if model is not None:
            matured_index = index - horizon
            if matured_index >= test_start and rows[matured_index] is not None:
                matured_entry = candles[matured_index].close
                matured_exit = candles[matured_index + horizon].close
                if matured_exit != matured_entry:
                    model.learn_one(rows[matured_index], int(matured_exit > matured_entry))
            if index - last_retrained >= retrain_every:
                model = train_from_candles(candles, rows, horizon=horizon, end_index=index)
                last_retrained = index

        feature = rows[index]
        if feature is None:
            continue
        probability = model.predict_up(feature) if model is not None and model.trained else None
        decision = decide(
            strategy_id,
            candles[index],
            candles[index - 1].close,
            feature,
            probability,
            buy_threshold=threshold,
            sell_threshold=1.0 - threshold,
        )
        if decision.side == "NO SIGNAL":
            continue

        entry = candles[index].close
        exit_price = candles[index + horizon].close
        if exit_price == entry:
            result = "TIE"
            ties += 1
        elif (decision.side == "BUY" and exit_price > entry) or (decision.side == "SELL" and exit_price < entry):
            result = "WIN"
            wins += 1
            net_units += payout
        else:
            result = "LOSS"
            losses += 1
            net_units -= 1.0
        trades.append({
            "timestamp": candles[index].timestamp.isoformat(),
            "side": decision.side,
            "entry": entry,
            "exit": exit_price,
            "result": result,
            "probability_up": probability,
        })

    signal_count = wins + losses + ties
    decisive_count = wins + losses
    test_candles = max(0, len(candles) - horizon - test_start)
    break_even = 1.0 / (1.0 + payout)
    last_20 = [trade for trade in trades if trade["result"] in {"WIN", "LOSS"}][-TARGET_WINDOW:]
    last_20_wins = sum(trade["result"] == "WIN" for trade in last_20)

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_loss_streak = 0
    max_loss_streak = 0
    for trade in trades:
        if trade["result"] == "WIN":
            equity += payout
            current_loss_streak = 0
        elif trade["result"] == "LOSS":
            equity -= 1.0
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    last_20_count = len(last_20)
    return {
        "strategy": strategy_id,
        "candles": len(candles),
        "test_candles": test_candles,
        "signals": signal_count,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "decisive_trades": decisive_count,
        "win_rate": (wins / decisive_count) if decisive_count else None,
        "win_rate_ci95": wilson_interval(wins, decisive_count),
        "net_units": round(net_units, 4),
        "net_units_per_decisive_trade": round(net_units / decisive_count, 6) if decisive_count else None,
        "signal_coverage": round(signal_count / test_candles, 6) if test_candles else 0.0,
        "max_drawdown_units": round(max_drawdown, 4),
        "max_loss_streak": max_loss_streak,
        "assumed_payout": payout,
        "break_even_win_rate": break_even,
        "horizon_candles": horizon,
        "threshold": threshold,
        "accuracy_target": TARGET_WIN_RATE,
        "target_window": TARGET_WINDOW,
        "last_20_decisive": {
            "trades": last_20_count,
            "wins": last_20_wins,
            "losses": last_20_count - last_20_wins,
            "win_rate": last_20_wins / last_20_count if last_20_count else None,
            "win_rate_ci95": wilson_interval(last_20_wins, last_20_count),
            "target_met": last_20_count == TARGET_WINDOW and last_20_wins >= 19,
        },
        "note": (
            "Historical walk-forward estimate only. The 19/20 figure is a sample target, not a guarantee; "
            "small samples have wide uncertainty. Payout is an assumption, not a live platform quote."
        ),
        "recent_trades": trades[-12:],
    }
