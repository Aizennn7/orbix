from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from .domain import Candle, Signal, parse_datetime
from .features import compute_features
from .model import BootstrapEnsemble, OnlineLogisticModel, train_from_candles
from .prediction import Prediction, build_prediction
from .strategies import decide, EXECUTABLE_STRATEGIES


class PredictionEngine:
    """Signal-only prediction engine. It never places broker orders."""

    def generate_signal(
        self,
        candles: list[Candle],
        pair: str,
        strategy_id: str = "ml_consensus",
        source: str = "SYNTHETIC DEMO",
        horizon: int = 1,
        model: OnlineLogisticModel | None = None,
        generated_at: datetime | None = None,
        is_live: bool = False,
        ensemble: BootstrapEnsemble | None = None,
        max_uncertainty: float = 0.10,
    ) -> tuple[Signal, OnlineLogisticModel | None, dict[str, float] | None]:
        if len(candles) < 40:
            raise ValueError("At least 40 completed candles are required")
        if strategy_id not in {item["id"] for item in EXECUTABLE_STRATEGIES}:
            raise ValueError(f"Strategy '{strategy_id}' is a research candidate, not an executable prototype")
        if horizon < 1 or horizon > 60:
            raise ValueError("Expiry must be between 1 and 60 candles")

        feature_rows = compute_features(candles)
        index = len(candles) - 1
        latest = candles[index]
        previous_close = candles[index - 1].close
        current_features = feature_rows[index]
        prediction: Prediction | None = None
        decision_probability: float | None = None
        fitted_model = model
        calibrated = False

        if current_features is None:
            decision = decide(strategy_id, latest, previous_close, None, None)
        else:
            if fitted_model is None and strategy_id == "ml_consensus":
                fitted_model = train_from_candles(candles, feature_rows, horizon=horizon, end_index=index)
            if fitted_model is not None and fitted_model.trained:
                prediction = build_prediction(
                    fitted_model,
                    current_features,
                    ensemble=ensemble,
                    max_uncertainty=max_uncertainty,
                )
                decision_probability = prediction.probability_up
                calibrated = prediction.calibrated
            uses_model = strategy_id in {"ml_consensus", "orbix_confluence", "orbix_cadence"}
            decision = decide(
                strategy_id,
                latest,
                previous_close,
                current_features,
                decision_probability,
                probability_calibrated=calibrated if decision_probability is not None else None,
                uncertainty=prediction.uncertainty if uses_model and prediction else None,
                max_uncertainty=max_uncertainty,
                calibration_flat=prediction.calibration_flat if uses_model and prediction else False,
            )

        # Candle timestamps are opens. Once the latest candle is complete, the
        # signal is ready at the next minute boundary; its target is evaluated
        # one configured horizon later (one minute later for the default M1).
        entry_time = latest.timestamp + timedelta(minutes=1)
        prediction_time = entry_time + timedelta(minutes=horizon)
        actual_generated_at = parse_datetime(generated_at) if generated_at is not None else entry_time
        signal = Signal(
            signal_id=uuid.uuid4().hex,
            pair=pair,
            strategy=strategy_id,
            side=decision.side,
            entry_price=latest.close,
            generated_at=actual_generated_at,
            expires_at=prediction_time,
            probability_up=decision.probability_up,
            source=source,
            reason=decision.reason,
            entry_time=entry_time,
            is_live=is_live,
            probability_raw=prediction.raw_probability if prediction else None,
            probability_calibrated=prediction.calibrated if prediction else False,
            uncertainty=prediction.uncertainty if prediction else None,
            contributions=tuple(prediction.contributions) if prediction else (),
        )
        return signal, fitted_model, current_features

    def raw_shadow(
        self,
        candles: list[Candle],
        pair: str,
        strategy_id: str = "ml_consensus",
        *,
        model: OnlineLogisticModel | None = None,
        horizon: int = 1,
        buy_threshold: float = 0.57,
        sell_threshold: float = 0.43,
        production_probability: float | None = None,
    ) -> tuple[dict, dict[str, float]] | None:
        """Counterfactual raw read for a bar the flat-calibration gate skipped.

        This is learning-only. It deliberately uses the uncalibrated score and
        ignores the ensemble/calibration gates, so the engine can keep
        collecting outcomes while a flat map blocks live signals. It is never
        sent, stored as a paper signal, or counted in the live record.
        """
        if model is None or not model.trained or len(candles) < 40:
            return None
        rows = compute_features(candles)
        index = len(candles) - 1
        features = rows[index]
        if features is None:
            return None
        latest = candles[index]
        raw = model.predict_up(features)
        decision = decide(
            strategy_id,
            latest,
            candles[index - 1].close,
            features,
            raw,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            probability_calibrated=False,
            uncertainty=None,
            calibration_flat=False,
        )
        if decision.side == "NO SIGNAL":
            return None
        entry_time = latest.timestamp + timedelta(minutes=1)
        shadow = {
            "shadow_id": uuid.uuid4().hex,
            "pair": pair,
            "strategy": strategy_id,
            "side": decision.side,
            "entry_price": latest.close,
            "entry_time": entry_time.isoformat(),
            "expires_at": (entry_time + timedelta(minutes=horizon)).isoformat(),
            "probability_up": float(raw),
            "probability_calibrated": production_probability,
            "reason": (
                f"Shadow read (not traded): raw score {raw:.3f}. "
                f"{decision.reason}"
            ),
        }
        return shadow, features
