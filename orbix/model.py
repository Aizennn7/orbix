from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .calibration import (
    IsotonicCalibrator,
    accuracy,
    brier_score,
    expected_calibration_error,
    log_loss,
    skill_score,
)
from .domain import Candle
from .features import FEATURE_LABELS, FEATURE_NAMES, FEATURE_VERSION, compute_features


_PROBABILITY_FLOOR = 1e-3
_STANDARDISED_LIMIT = 8.0


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))
    return np.clip(probabilities, _PROBABILITY_FLOOR, 1.0 - _PROBABILITY_FLOOR)


def _sigmoid(value: float) -> float:
    probability = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, value))))
    return max(_PROBABILITY_FLOOR, min(1.0 - _PROBABILITY_FLOOR, float(probability)))


@dataclass
class OnlineLogisticModel:
    """Explainable logistic model with batch fit, guarded online updates, and calibration.

    Every score is the sum of a feature value times a weight, so any signal can
    be explained bar by bar. The model is deliberately small: with a few
    thousand one-minute samples, a larger network would memorise noise and
    would not be auditable.
    """

    feature_names: tuple[str, ...] = FEATURE_NAMES
    feature_version: int = FEATURE_VERSION
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    training_samples: int = 0
    calibrator: IsotonicCalibrator = field(default_factory=IsotonicCalibrator)
    calibration_report: dict | None = None
    validation_log_loss: float | None = None
    trained_at: str | None = None

    @property
    def trained(self) -> bool:
        return bool(self.weights) and self.training_samples > 0

    # ------------------------------------------------------------------ fitting
    def fit(
        self,
        samples: Iterable[tuple[dict[str, float], int]],
        epochs: int = 160,
        learning_rate: float = 0.05,
        l2: float = 0.002,
        validation_fraction: float = 0.15,
        min_samples: int = 30,
        patience: int = 25,
    ) -> "OnlineLogisticModel":
        rows = [
            (features, int(label))
            for features, label in samples
            if all(name in features for name in self.feature_names)
        ]
        if len(rows) < min_samples:
            raise ValueError(f"At least {min_samples} labelled candles are needed to train the model")
        labels = [label for _, label in rows]
        if any(label not in (0, 1) for label in labels):
            raise ValueError("Model labels must be 0 (down) or 1 (up)")

        X = np.asarray([[float(features[name]) for name in self.feature_names] for features, _ in rows], dtype=float)
        y = np.asarray(labels, dtype=float)

        # Fit scalers on the training portion only so the validation tail stays
        # an honest check rather than being standardised with its own data.
        validation_count = int(len(rows) * validation_fraction) if len(rows) >= 200 else 0
        fit_slice = slice(0, len(rows) - validation_count) if validation_count else slice(0, len(rows))
        fitting = X[fit_slice]
        self.means = [float(value) for value in fitting.mean(axis=0)]
        self.scales = [float(value) if value > 1e-8 else 1e-8 for value in fitting.std(axis=0)]

        # Winsorise standardized inputs so one abnormal bar cannot dominate the
        # score or its explanation. The same bound is applied at inference.
        Z = self._standardise(X)
        train_Z, train_y = (Z[: len(rows) - validation_count], y[: len(rows) - validation_count]) if validation_count else (Z, y)
        validation_Z, validation_y = (Z[len(rows) - validation_count :], y[len(rows) - validation_count :]) if validation_count else (None, None)

        positives = float(train_y.sum())
        negatives = float(len(train_y) - positives)
        weights = np.zeros(len(self.feature_names), dtype=float)
        bias = math.log((positives + 1.0) / (negatives + 1.0))

        # Adam keeps convergence fast and stable across the mixed feature scales
        # in this set (percentages, ratios, and sine/cosine terms).
        m_weights = np.zeros_like(weights)
        v_weights = np.zeros_like(weights)
        m_bias = 0.0
        v_bias = 0.0
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        best_loss = float("inf")
        best_weights = weights.copy()
        best_bias = bias
        best_validation: float | None = None
        stale = 0

        for epoch in range(1, max(1, epochs) + 1):
            logits = train_Z @ weights + bias
            probabilities = _sigmoid_array(logits)
            error = probabilities - train_y
            gradient_weights = (train_Z.T @ error) / len(train_y) + l2 * weights
            gradient_bias = float(error.mean())

            m_weights = beta1 * m_weights + (1 - beta1) * gradient_weights
            v_weights = beta2 * v_weights + (1 - beta2) * (gradient_weights ** 2)
            m_bias = beta1 * m_bias + (1 - beta1) * gradient_bias
            v_bias = beta2 * v_bias + (1 - beta2) * (gradient_bias ** 2)
            correction1 = 1 - beta1 ** epoch
            correction2 = 1 - beta2 ** epoch
            weights = weights - learning_rate * (m_weights / correction1) / (np.sqrt(v_weights / correction2) + epsilon)
            bias = bias - learning_rate * (m_bias / correction1) / (math.sqrt(v_bias / correction2) + epsilon)

            if epoch % 5 == 0 or epoch == 1:
                reference_Z = validation_Z if validation_Z is not None else train_Z
                reference_y = validation_y if validation_y is not None else train_y
                loss = _binary_log_loss(_sigmoid_array(reference_Z @ weights + bias), reference_y)
                if loss < best_loss - 1e-6:
                    best_loss = loss
                    best_weights = weights.copy()
                    best_bias = bias
                    best_validation = loss if validation_Z is not None else None
                    stale = 0
                else:
                    stale += 1
                    if stale >= patience:
                        break

        self.weights = [float(value) for value in best_weights]
        self.bias = float(best_bias)
        self.training_samples = len(rows)
        self.validation_log_loss = best_validation
        return self

    # --------------------------------------------------------------- inference
    def _standardise(self, values: np.ndarray) -> np.ndarray:
        if not self.means or not self.scales:
            return np.clip(values, -_STANDARDISED_LIMIT, _STANDARDISED_LIMIT)
        scaled = (values - np.asarray(self.means)) / np.asarray(self.scales)
        return np.clip(scaled, -_STANDARDISED_LIMIT, _STANDARDISED_LIMIT)

    def _vector(self, features: dict[str, float]) -> np.ndarray:
        values = np.asarray([float(features[name]) for name in self.feature_names], dtype=float)
        return self._standardise(values)

    def predict_up(self, features: dict[str, float]) -> float:
        if not self.trained:
            raise ValueError("Model has not been trained")
        return _sigmoid(self.bias + float(np.dot(np.asarray(self.weights), self._vector(features))))

    def predict_calibrated(self, features: dict[str, float]) -> float:
        """Raw score passed through the stored isotonic calibration when fitted."""
        return self.calibrator.transform(self.predict_up(features))

    def predict_many(self, feature_rows: Sequence[dict[str, float]]) -> np.ndarray:
        if not self.trained:
            raise ValueError("Model has not been trained")
        matrix = np.asarray(
            [[float(row[name]) for name in self.feature_names] for row in feature_rows],
            dtype=float,
        )
        return _sigmoid_array(self._standardise(matrix) @ np.asarray(self.weights) + self.bias)

    def contributions(self, features: dict[str, float], top: int = 6) -> list[dict]:
        """Signed per-feature contributions, largest absolute first."""
        if not self.trained:
            return []
        vector = self._vector(features)
        rows = []
        for name, weight, value in zip(self.feature_names, self.weights, vector):
            rows.append({
                "feature": name,
                "label": FEATURE_LABELS.get(name, name),
                "value": round(float(value), 4),
                "weight": round(float(weight), 4),
                "contribution": round(float(weight * value), 4),
            })
        rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        return rows[: max(1, top)]

    # ----------------------------------------------------------------- learning
    def learn_one(
        self,
        features: dict[str, float],
        label: int,
        learning_rate: float = 0.025,
        l2: float = 0.001,
    ) -> None:
        """Update once after an explicitly confirmed paper outcome."""
        if not self.trained:
            raise ValueError("Train the model before applying an online update")
        if label not in (0, 1):
            raise ValueError("Model labels must be 0 (down) or 1 (up)")
        vector = self._vector(features)
        probability = _sigmoid(self.bias + float(np.dot(np.asarray(self.weights), vector)))
        error = probability - label
        self.bias -= learning_rate * error
        weights = np.asarray(self.weights) - learning_rate * (error * vector + l2 * np.asarray(self.weights))
        self.weights = [float(value) for value in weights]
        self.training_samples += 1

    def snapshot(self) -> dict:
        return {
            "weights": list(self.weights),
            "bias": self.bias,
            "training_samples": self.training_samples,
        }

    def restore(self, snapshot: dict) -> None:
        self.weights = [float(value) for value in snapshot["weights"]]
        self.bias = float(snapshot["bias"])
        self.training_samples = int(snapshot["training_samples"])

    def clone(self) -> "OnlineLogisticModel":
        return OnlineLogisticModel.from_dict(self.to_dict())

    def fit_calibration(self, points: Iterable[tuple[float, int]]) -> IsotonicCalibrator:
        self.calibrator = IsotonicCalibrator.fit(points)
        return self.calibrator

    # -------------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        return {
            "feature_names": list(self.feature_names),
            "feature_version": self.feature_version,
            "means": self.means,
            "scales": self.scales,
            "weights": self.weights,
            "bias": self.bias,
            "training_samples": self.training_samples,
            "validation_log_loss": self.validation_log_loss,
            "trained_at": self.trained_at,
            "calibration": self.calibrator.to_dict(),
            "calibration_report": self.calibration_report,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "OnlineLogisticModel":
        version = int(payload.get("feature_version", 1))
        if version != FEATURE_VERSION:
            raise ValueError(
                f"Saved model uses feature version {version}; version {FEATURE_VERSION} is required. Retrain required."
            )
        names = tuple(payload.get("feature_names", FEATURE_NAMES))
        if names != tuple(FEATURE_NAMES):
            raise ValueError("Saved model feature order does not match this build; retraining is required")
        model = cls(feature_names=names)
        model.feature_version = version
        model.means = [float(value) for value in payload.get("means", [])]
        model.scales = [float(value) for value in payload.get("scales", [])]
        model.weights = [float(value) for value in payload.get("weights", [])]
        model.bias = float(payload.get("bias", 0.0))
        model.training_samples = int(payload.get("training_samples", 0))
        model.validation_log_loss = payload.get("validation_log_loss")
        model.trained_at = payload.get("trained_at")
        model.calibrator = IsotonicCalibrator.from_dict(payload.get("calibration"))
        report = payload.get("calibration_report")
        model.calibration_report = dict(report) if isinstance(report, dict) else None
        if model.weights and len(model.weights) != len(model.feature_names):
            raise ValueError("Saved model feature count does not match its weights")
        if model.weights and (len(model.means) != len(model.feature_names) or len(model.scales) != len(model.feature_names)):
            raise ValueError("Saved model standardisation does not match its feature count")
        return model

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "OnlineLogisticModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _binary_log_loss(probabilities: np.ndarray, labels: np.ndarray) -> float:
    clipped = np.clip(probabilities, 1e-9, 1 - 1e-9)
    return float(-np.mean(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped)))


@dataclass
class BootstrapEnsemble:
    """Bagged members used for uncertainty, not for extra accuracy claims.

    Disagreement between members is a useful warning that the current bar sits
    where the model is unstable. It is a variance estimate, not a probability
    that a signal will win.
    """

    members: list[OnlineLogisticModel] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return len(self.members) >= 2 and all(member.trained for member in self.members)

    def predict(self, features: dict[str, float]) -> tuple[float, float]:
        if not self.ready:
            raise ValueError("Ensemble needs at least two trained members")
        scores = np.asarray([member.predict_up(features) for member in self.members], dtype=float)
        return float(scores.mean()), float(scores.std())

    @classmethod
    def fit(
        cls,
        samples: Sequence[tuple[dict[str, float], int]],
        size: int = 3,
        seed: int = 20261020,
    ) -> "BootstrapEnsemble":
        if len(samples) < 60:
            raise ValueError("At least 60 labelled candles are needed for an ensemble")
        rng = np.random.default_rng(seed)
        members: list[OnlineLogisticModel] = []
        for _ in range(max(2, size)):
            indexes = rng.integers(0, len(samples), size=len(samples))
            subset = [samples[index] for index in indexes]
            try:
                members.append(OnlineLogisticModel().fit(subset))
            except ValueError:
                continue
        return cls(members=members)


def build_training_samples(
    candles: list[Candle],
    features: list[dict[str, float] | None] | None = None,
    horizon: int = 1,
    end_index: int | None = None,
    start_index: int = 20,
) -> list[tuple[dict[str, float], int]]:
    """Create labels using future closes, while features use only past/current bars."""
    if horizon < 1:
        raise ValueError("Prediction horizon must be at least one candle")
    feature_rows = features or compute_features(candles)
    last_available = len(candles) - 1 if end_index is None else min(end_index, len(candles) - 1)
    last_entry = last_available - horizon
    samples: list[tuple[dict[str, float], int]] = []
    for index in range(max(start_index, 20), last_entry + 1):
        row = feature_rows[index]
        if row is None:
            continue
        current = candles[index].close
        future = candles[index + horizon].close
        if future == current:
            continue
        samples.append((row, 1 if future > current else 0))
    return samples


def train_from_candles(
    candles: list[Candle],
    features: list[dict[str, float] | None] | None = None,
    horizon: int = 1,
    end_index: int | None = None,
) -> OnlineLogisticModel:
    return OnlineLogisticModel().fit(build_training_samples(candles, features, horizon, end_index))


def calibration_report(model: OnlineLogisticModel, samples: Sequence[tuple[dict[str, float], int]]) -> dict:
    """Measure calibration and skill on an explicitly supplied sample set."""
    if not samples:
        return {}
    raw = [(model.predict_up(features), label) for features, label in samples]
    calibrated = [(model.predict_calibrated(features), label) for features, label in samples]
    raw_brier = brier_score(raw)
    calibrated_brier = brier_score(calibrated)
    return {
        "samples": len(samples),
        "raw": {
            "brier": raw_brier,
            "log_loss": log_loss(raw),
            "accuracy": accuracy(raw),
            "ece": expected_calibration_error(raw),
            "skill_vs_coin_flip": skill_score(raw_brier),
        },
        "calibrated": {
            "brier": calibrated_brier,
            "log_loss": log_loss(calibrated),
            "accuracy": accuracy(calibrated),
            "ece": expected_calibration_error(calibrated),
            "skill_vs_coin_flip": skill_score(calibrated_brier),
        },
        "is_calibrated": model.calibrator.fitted,
    }
