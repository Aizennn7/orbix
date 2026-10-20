from __future__ import annotations

from .calibration import (
    IsotonicCalibrator,
    brier_score,
    expected_calibration_error,
    fit_validated,
    log_loss,
)
from .domain import Candle
from .features import compute_features
from .metrics import wilson_interval
from .model import BootstrapEnsemble, OnlineLogisticModel, build_training_samples
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
    use_calibration: bool = True,
    include_uncertainty: bool = True,
    max_uncertainty: float = 0.10,
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
    ensemble: BootstrapEnsemble | None = None
    uses_learned_model = strategy_id in {"ml_consensus", "orbix_confluence"}
    if uses_learned_model:
        training = build_training_samples(candles, rows, horizon=horizon, end_index=test_start - 1)
        model = OnlineLogisticModel().fit(training)
        if include_uncertainty and len(training) >= 60:
            ensemble = BootstrapEnsemble.fit(training)

    wins = losses = ties = 0
    uncertainty_abstentions = 0
    uncertainty_blocked = 0
    flat_abstentions = 0
    flat_blocked = 0
    ensemble_bars = 0
    net_units = 0.0
    trades: list[dict] = []
    predictions: list[dict] = []
    issued_raw: dict[int, float] = {}
    calibration_points: list[tuple[float, int]] = []
    calibrator = IsotonicCalibrator()
    calibration_validation: dict | None = None
    calibration_fitted_at = 0
    last_retrained = test_start
    for index in range(test_start, len(candles) - horizon):
        # Online test-then-learn: update only with a label whose expiry has passed.
        if model is not None:
            matured_index = index - horizon
            if matured_index >= test_start and rows[matured_index] is not None:
                matured_entry = candles[matured_index].close
                matured_exit = candles[matured_index + horizon].close
                if matured_exit != matured_entry:
                    matured_label = int(matured_exit > matured_entry)
                    model.learn_one(rows[matured_index], matured_label)
                    if use_calibration and matured_index in issued_raw:
                        calibration_points.append((issued_raw[matured_index], matured_label))
            if index - last_retrained >= retrain_every:
                training = build_training_samples(candles, rows, horizon=horizon, end_index=index)
                model = OnlineLogisticModel().fit(training)
                ensemble = BootstrapEnsemble.fit(training) if include_uncertainty and len(training) >= 60 else None
                last_retrained = index
            # Refit only from outcomes that had matured before this decision.
            if use_calibration and len(calibration_points) >= 60:
                bucket = len(calibration_points) // 20
                if bucket != calibration_fitted_at:
                    fitted, validation = fit_validated(calibration_points)
                    if validation.get("accepted") and fitted.fitted:
                        calibrator = fitted
                    calibration_validation = validation
                    calibration_fitted_at = bucket

        feature = rows[index]
        if feature is None:
            continue
        raw_probability = model.predict_up(feature) if model is not None and model.trained else None
        if raw_probability is not None:
            issued_raw[index] = raw_probability
        probability = calibrator.transform(raw_probability) if (raw_probability is not None and calibrator.fitted) else raw_probability
        exit_price = candles[index + horizon].close
        entry = candles[index].close
        if raw_probability is not None and exit_price != entry:
            predictions.append({
                "raw": raw_probability,
                "calibrated": probability,
                "label": int(exit_price > entry),
                "calibrator_active": calibrator.fitted,
            })
        spread = None
        if uses_learned_model and ensemble is not None and ensemble.ready:
            _, spread = ensemble.predict(feature)
        if spread is not None:
            ensemble_bars += 1
        calibration_flat = uses_learned_model and bool(calibrator.fitted and calibrator.flat)
        decision_options = {
            "buy_threshold": threshold,
            "sell_threshold": 1.0 - threshold,
            "uncertainty": spread,
            "max_uncertainty": max_uncertainty,
            "calibration_flat": calibration_flat,
        }
        decision = decide(
            strategy_id,
            candles[index],
            candles[index - 1].close,
            feature,
            probability,
            **decision_options,
        )
        if decision.side == "NO SIGNAL":
            if spread is not None and spread > max_uncertainty:
                uncertainty_abstentions += 1
                # The gate runs first, so re-evaluate without it to report how
                # many would-be signals the spread actually removed.
                decision_options["uncertainty"] = None
                if decide(
                    strategy_id,
                    candles[index],
                    candles[index - 1].close,
                    feature,
                    probability,
                    **decision_options,
                ).side != "NO SIGNAL":
                    uncertainty_blocked += 1
                decision_options["uncertainty"] = spread
            elif calibration_flat and decision.reason.startswith("Model calibration is flat"):
                flat_abstentions += 1
                decision_options["calibration_flat"] = False
                if decide(
                    strategy_id,
                    candles[index],
                    candles[index - 1].close,
                    feature,
                    probability,
                    **decision_options,
                ).side != "NO SIGNAL":
                    flat_blocked += 1
                decision_options["calibration_flat"] = True
            continue
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
            "probability_raw": raw_probability,
            "calibrated": calibrator.fitted,
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
    paired = [item for item in predictions if item["calibrator_active"]]
    raw_points = [(item["raw"], item["label"]) for item in paired]
    calibrated_points = [(item["calibrated"], item["label"]) for item in paired]
    raw_brier = brier_score(raw_points)
    calibrated_brier = brier_score(calibrated_points)
    calibration_report = {
        "method": "isotonic (PAVA), prequential",
        "fitted": calibrator.fitted,
        "fitted_on_outcomes": calibrator.samples,
        "calibration_points_available": len(calibration_points),
        "scored_predictions": len(predictions),
        "raw_brier": raw_brier,
        "calibrated_brier": calibrated_brier,
        "raw_ece": expected_calibration_error(raw_points),
        "calibrated_ece": expected_calibration_error(calibrated_points),
        "raw_log_loss": log_loss(raw_points),
        "calibrated_log_loss": log_loss(calibrated_points),
        "levels": calibrator.levels,
        "flat": calibrator.flat,
        "last_validation": calibration_validation,
        "note": (
            "The calibrator at each step used only outcomes that had already matured. "
            "Raw and calibrated metrics use the same later rows, once a calibrator existed; "
            "earlier steps were uncalibrated and help only the trade statistics."
        ),
    }
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
        "probability": "calibrated when available" if use_calibration else "raw",
        "calibration": calibration_report,
        "uncertainty_gate": {
            "enabled": bool(uses_learned_model and include_uncertainty),
            "limit": max_uncertainty,
            "evaluated": ensemble_bars,
            "abstentions": uncertainty_abstentions,
            "blocked_signals": uncertainty_blocked,
            "abstention_rate": round(uncertainty_abstentions / ensemble_bars, 4) if ensemble_bars else 0.0,
        },
        "calibration_gate": {
            "flat": bool(use_calibration and calibrator.fitted and calibrator.flat),
            "abstentions": flat_abstentions,
            "blocked_signals": flat_blocked,
        },
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
