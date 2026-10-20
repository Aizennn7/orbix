from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from .domain import Candle, Signal, parse_datetime
from .features import compute_features
from .model import OnlineLogisticModel, train_from_candles
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
        if current_features is None:
            decision = decide(strategy_id, latest, previous_close, None, None)
            fitted_model = model
        else:
            fitted_model = model
            if fitted_model is None and strategy_id == "ml_consensus":
                fitted_model = train_from_candles(candles, feature_rows, horizon=horizon, end_index=index)
            probability = fitted_model.predict_up(current_features) if fitted_model and fitted_model.trained else None
            decision = decide(strategy_id, latest, previous_close, current_features, probability)

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
        )
        return signal, fitted_model, current_features
