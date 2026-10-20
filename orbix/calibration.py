from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Sequence


MIN_CALIBRATION_POINTS = 60
MIN_BIN_SIZE = 20
_CLIP = (0.01, 0.99)


def _clip(value: float) -> float:
    return max(_CLIP[0], min(_CLIP[1], float(value)))


@dataclass
class IsotonicCalibrator:
    """Monotone, explainable probability calibration fitted by PAVA.

    A logistic model trained by gradient descent is not automatically
    calibrated, and drift makes its probabilities drift too. Isotonic
    regression learns the mapping from raw score to observed frequency
    without assuming a shape, so a stored 60% means roughly 60% on the
    data it was fitted on. That is a statement about the past, not a
    promise about the next candle.
    """

    thresholds: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    samples: int = 0
    method: str = "isotonic (PAVA)"

    @property
    def fitted(self) -> bool:
        return bool(self.thresholds) and bool(self.values) and self.samples > 0

    @classmethod
    def fit(
        cls,
        points: Iterable[tuple[float, int]],
        min_bin_size: int = MIN_BIN_SIZE,
        minimum_points: int = MIN_CALIBRATION_POINTS,
    ) -> "IsotonicCalibrator":
        rows = sorted((_clip(p), int(y)) for p, y in points if y in (0, 1) and math.isfinite(float(p)))
        if len(rows) < max(minimum_points, min_bin_size * 2):
            return cls()

        # Equal-count bins keep the fit stable when scores cluster near 0.5.
        bin_count = max(2, min(10, len(rows) // min_bin_size))
        size = len(rows) / bin_count
        bins: list[tuple[float, float, int]] = []
        for index in range(bin_count):
            start = int(round(index * size))
            end = len(rows) if index == bin_count - 1 else int(round((index + 1) * size))
            chunk = rows[start:end]
            if not chunk:
                continue
            bins.append((
                sum(p for p, _ in chunk) / len(chunk),
                sum(y for _, y in chunk) / len(chunk),
                len(chunk),
            ))
        if len(bins) < 2:
            return cls()

        # Pool-adjacent-violators: merge neighbouring bins while the fitted
        # frequency decreases, weighting each block by its sample count.
        blocks: list[tuple[float, float, int]] = []
        for centre, frequency, weight in bins:
            blocks.append((centre, frequency, weight))
            while len(blocks) > 1 and blocks[-2][1] > blocks[-1][1]:
                c2, f2, w2 = blocks.pop()
                c1, f1, w1 = blocks.pop()
                total = w1 + w2
                blocks.append(((c1 * w1 + c2 * w2) / total, (f1 * w1 + f2 * w2) / total, total))

        thresholds = [round(_clip(block[0]), 6) for block in blocks]
        values = [round(_clip(block[1]), 6) for block in blocks]
        return cls(thresholds=thresholds, values=values, samples=len(rows))

    def transform(self, probability: float) -> float:
        if not self.fitted:
            return _clip(probability)
        score = _clip(probability)
        xs, ys = self.thresholds, self.values
        if score <= xs[0]:
            return ys[0]
        if score >= xs[-1]:
            return ys[-1]
        for index in range(1, len(xs)):
            if score <= xs[index]:
                left, right = xs[index - 1], xs[index]
                if right - left <= 1e-12:
                    return ys[index]
                ratio = (score - left) / (right - left)
                return _clip(ys[index - 1] + ratio * (ys[index] - ys[index - 1]))
        return ys[-1]

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "thresholds": self.thresholds,
            "values": self.values,
            "samples": self.samples,
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "IsotonicCalibrator":
        if not payload:
            return cls()
        thresholds = [float(value) for value in payload.get("thresholds", [])]
        values = [float(value) for value in payload.get("values", [])]
        if len(thresholds) != len(values):
            return cls()
        return cls(
            thresholds=thresholds,
            values=values,
            samples=int(payload.get("samples", 0)),
            method=str(payload.get("method", "isotonic (PAVA)")),
        )


def fit_validated(
    points: Sequence[tuple[float, int]],
    *,
    minimum_for_holdout: int = 140,
    train_fraction: float = 0.7,
) -> tuple[IsotonicCalibrator, dict]:
    """Fit isotonic calibration and, when enough rows exist, validate it.

    With fewer than ``minimum_for_holdout`` rows the fit uses every point and is
    reported as unvalidated. With enough rows it fits the older
    ``train_fraction`` and only accepts the mapping if Brier score and expected
    calibration error do not worsen on the newer rows.
    """
    rows = [(float(probability), int(label)) for probability, label in points if label in (0, 1)]
    if len(rows) < minimum_for_holdout:
        calibrator = IsotonicCalibrator.fit(rows)
        if not calibrator.fitted:
            return calibrator, {"accepted": False, "validated_on": 0, "samples": len(rows), "reason": "Not enough points."}
        calibrated = [(calibrator.transform(probability), label) for probability, label in rows]
        return calibrator, {
            "accepted": True,
            "samples": len(rows),
            "validated_on": 0,
            "ece_before": expected_calibration_error(rows),
            "ece_after": expected_calibration_error(calibrated),
            "brier_before": brier_score(rows),
            "brier_after": brier_score(calibrated),
            "bins": len(calibrator.thresholds),
            "reason": f"Fewer than {minimum_for_holdout} outcomes, so the fit was not split for validation.",
        }

    split = max(60, int(len(rows) * train_fraction))
    training, evaluation = rows[:split], rows[split:]
    calibrator = IsotonicCalibrator.fit(training)
    if not calibrator.fitted:
        return calibrator, {"accepted": False, "validated_on": 0, "samples": len(training), "reason": "Fit produced no bins."}
    calibrated = [(calibrator.transform(probability), label) for probability, label in evaluation]
    raw_brier = brier_score(evaluation)
    raw_ece = expected_calibration_error(evaluation)
    calibrated_brier = brier_score(calibrated)
    calibrated_ece = expected_calibration_error(calibrated)
    accepted = bool(
        raw_brier is not None
        and raw_ece is not None
        and calibrated_brier is not None
        and calibrated_ece is not None
        and calibrated_brier <= raw_brier + 1e-9
        and calibrated_ece <= raw_ece + 1e-9
    )
    return calibrator, {
        "accepted": accepted,
        "samples": len(training),
        "validated_on": len(evaluation),
        "ece_before": raw_ece,
        "ece_after": calibrated_ece,
        "brier_before": raw_brier,
        "brier_after": calibrated_brier,
        "bins": len(calibrator.thresholds),
        "reason": (
            "Held-out calibration improved both Brier score and ECE."
            if accepted
            else "Held-out calibration did not improve both Brier score and ECE; the previous mapping was kept."
        ),
    }


def brier_score(points: Sequence[tuple[float, int]]) -> float | None:
    if not points:
        return None
    return sum((_clip(p) - y) ** 2 for p, y in points) / len(points)


def log_loss(points: Sequence[tuple[float, int]]) -> float | None:
    if not points:
        return None
    total = 0.0
    for probability, label in points:
        p = min(1.0 - 1e-9, max(1e-9, _clip(probability)))
        total -= math.log(p) if label == 1 else math.log(1.0 - p)
    return total / len(points)


def accuracy(points: Sequence[tuple[float, int]]) -> float | None:
    if not points:
        return None
    return sum(1 for p, y in points if (p >= 0.5) == bool(y)) / len(points)


def reliability_bins(points: Sequence[tuple[float, int]], bins: int = 10) -> list[dict]:
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(max(1, bins))]
    for probability, label in points:
        index = min(bins - 1, max(0, int(_clip(probability) * bins)))
        buckets[index].append((_clip(probability), label))
    result = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        result.append({
            "lower": index / bins,
            "upper": (index + 1) / bins,
            "count": len(bucket),
            "mean_predicted": sum(p for p, _ in bucket) / len(bucket),
            "observed": sum(y for _, y in bucket) / len(bucket),
        })
    return result


def expected_calibration_error(points: Sequence[tuple[float, int]], bins: int = 10) -> float | None:
    if not points:
        return None
    total = 0.0
    for bucket in reliability_bins(points, bins):
        total += (bucket["count"] / len(points)) * abs(bucket["observed"] - bucket["mean_predicted"])
    return total


def skill_score(brier: float | None) -> float | None:
    """Brier skill against the always-50% baseline (Brier 0.25).

    Positive means better than a coin flip given the same labels; negative
    means worse. It is not a trading edge, because it ignores payout,
    spread, and abstention cost.
    """
    if brier is None:
        return None
    return 1.0 - (brier / 0.25)
