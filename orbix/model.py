from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Iterable

from .domain import Candle
from .features import FEATURE_NAMES, compute_features


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


@dataclass
class OnlineLogisticModel:
    """Small, explainable logistic model with batch-fit and explicit online updates."""

    feature_names: tuple[str, ...] = FEATURE_NAMES
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    training_samples: int = 0

    @property
    def trained(self) -> bool:
        return bool(self.weights) and self.training_samples > 0

    def fit(
        self,
        samples: Iterable[tuple[dict[str, float], int]],
        epochs: int = 120,
        learning_rate: float = 0.18,
        l2: float = 0.002,
    ) -> "OnlineLogisticModel":
        rows = [(features, int(label)) for features, label in samples if all(name in features for name in self.feature_names)]
        if len(rows) < 30:
            raise ValueError("At least 30 labelled candles are needed to train the model")
        labels = [label for _, label in rows]
        if any(label not in (0, 1) for label in labels):
            raise ValueError("Model labels must be 0 (down) or 1 (up)")

        columns = [[float(features[name]) for features, _ in rows] for name in self.feature_names]
        self.means = [sum(column) / len(column) for column in columns]
        self.scales = []
        for column, average in zip(columns, self.means):
            variance = sum((value - average) ** 2 for value in column) / len(column)
            self.scales.append(max(math.sqrt(variance), 1e-9))

        self.weights = [0.0] * len(self.feature_names)
        positives = sum(labels)
        negatives = len(labels) - positives
        self.bias = math.log((positives + 1.0) / (negatives + 1.0))
        for _ in range(max(1, epochs)):
            gradients = [0.0] * len(self.weights)
            bias_gradient = 0.0
            for features, label in rows:
                vector = self._scaled(features)
                probability = _sigmoid(self.bias + sum(weight * value for weight, value in zip(self.weights, vector)))
                error = probability - label
                bias_gradient += error
                for index, value in enumerate(vector):
                    gradients[index] += error * value
            count = float(len(rows))
            self.bias -= learning_rate * bias_gradient / count
            self.weights = [
                weight - learning_rate * (gradient / count + l2 * weight)
                for weight, gradient in zip(self.weights, gradients)
            ]
        self.training_samples = len(rows)
        return self

    def _scaled(self, features: dict[str, float]) -> list[float]:
        if not self.means or not self.scales:
            return [float(features[name]) for name in self.feature_names]
        return [
            (float(features[name]) - average) / scale
            for name, average, scale in zip(self.feature_names, self.means, self.scales)
        ]

    def predict_up(self, features: dict[str, float]) -> float:
        if not self.trained:
            raise ValueError("Model has not been trained")
        vector = self._scaled(features)
        return _sigmoid(self.bias + sum(weight * value for weight, value in zip(self.weights, vector)))

    def learn_one(self, features: dict[str, float], label: int, learning_rate: float = 0.025, l2: float = 0.001) -> None:
        """Update once after an explicitly confirmed paper outcome."""
        if not self.trained:
            raise ValueError("Train the model before applying an online update")
        if label not in (0, 1):
            raise ValueError("Model labels must be 0 (down) or 1 (up)")
        vector = self._scaled(features)
        probability = _sigmoid(self.bias + sum(weight * value for weight, value in zip(self.weights, vector)))
        error = probability - label
        self.bias -= learning_rate * error
        self.weights = [
            weight - learning_rate * (error * value + l2 * weight)
            for weight, value in zip(self.weights, vector)
        ]
        self.training_samples += 1

    def to_dict(self) -> dict:
        return {
            "feature_names": list(self.feature_names),
            "means": self.means,
            "scales": self.scales,
            "weights": self.weights,
            "bias": self.bias,
            "training_samples": self.training_samples,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "OnlineLogisticModel":
        model = cls(feature_names=tuple(payload.get("feature_names", FEATURE_NAMES)))
        model.means = [float(value) for value in payload.get("means", [])]
        model.scales = [float(value) for value in payload.get("scales", [])]
        model.weights = [float(value) for value in payload.get("weights", [])]
        model.bias = float(payload.get("bias", 0.0))
        model.training_samples = int(payload.get("training_samples", 0))
        if model.weights and len(model.weights) != len(model.feature_names):
            raise ValueError("Saved model feature count does not match its weights")
        return model

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "OnlineLogisticModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def build_training_samples(
    candles: list[Candle],
    features: list[dict[str, float] | None] | None = None,
    horizon: int = 1,
    end_index: int | None = None,
) -> list[tuple[dict[str, float], int]]:
    """Create labels using future closes, while features use only past/current bars."""
    if horizon < 1:
        raise ValueError("Prediction horizon must be at least one candle")
    feature_rows = features or compute_features(candles)
    last_available = len(candles) - 1 if end_index is None else min(end_index, len(candles) - 1)
    last_entry = last_available - horizon
    samples: list[tuple[dict[str, float], int]] = []
    for index in range(20, last_entry + 1):
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
