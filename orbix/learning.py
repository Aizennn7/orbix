from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from .calibration import (
    accuracy,
    brier_score,
    expected_calibration_error,
    fit_validated,
    log_loss,
    skill_score,
)
from .metrics import wilson_interval
from .model import OnlineLogisticModel


UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Prequential tracking
# ---------------------------------------------------------------------------
@dataclass
class PrequentialTracker:
    """Test-then-train record of every resolved outcome.

    Each entry stores the probability that was issued *before* the outcome was
    known. That makes the metrics prequential: no row is ever scored with a
    model that has already seen it.
    """

    capacity: int = 600
    entries: deque = field(default_factory=deque)
    total_seen: int = 0

    def observe(
        self,
        probability: float | None,
        label: int | None,
        *,
        correct: bool | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        if probability is None or label not in (0, 1):
            return
        probability = float(min(1.0, max(0.0, probability)))
        entry = {
            "probability": probability,
            "label": int(label),
            "correct": bool((probability >= 0.5) == bool(label)) if correct is None else bool(correct),
            "timestamp": (timestamp or _now()).isoformat(),
        }
        self.entries.append(entry)
        self.total_seen += 1
        while len(self.entries) > self.capacity:
            self.entries.popleft()

    def _rows(self, window: int | None = None) -> list[dict]:
        rows = list(self.entries)
        return rows[-window:] if window else rows

    def _points(self, window: int | None = None) -> list[tuple[float, int]]:
        return [(row["probability"], row["label"]) for row in self._rows(window)]

    def summary(self, window: int | None = None) -> dict:
        rows = self._rows(window)
        points = [(row["probability"], row["label"]) for row in rows]
        if not points:
            return {
                "samples": 0,
                "total_seen": self.total_seen,
                "accuracy": None,
                "brier": None,
                "log_loss": None,
                "ece": None,
                "skill_vs_coin_flip": None,
                "majority_baseline_accuracy": None,
            }
        brier = brier_score(points)
        labels = [label for _, label in points]
        base_rate = sum(labels) / len(labels)
        correct = sum(1 for row in rows if row["correct"])
        return {
            "samples": len(points),
            "total_seen": self.total_seen,
            "correct": correct,
            "accuracy": accuracy(points),
            "accuracy_ci95": wilson_interval(correct, len(points)),
            "brier": brier,
            "log_loss": log_loss(points),
            "ece": expected_calibration_error(points),
            "skill_vs_coin_flip": skill_score(brier),
            "majority_baseline_accuracy": max(base_rate, 1.0 - base_rate),
            "up_rate": base_rate,
        }

    def errors(self) -> list[float]:
        return [0.0 if row["correct"] else 1.0 for row in self.entries]


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------
@dataclass
class DriftMonitor:
    """Two-window error comparison.

    Compares the recent error rate with the preceding window of equal size.
    It reports a direction, not a cause: a jump can come from a regime change,
    a data outage, or simply a losing streak.
    """

    window: int = 40
    threshold: float = 0.10
    state: str = "warming"
    last_shift_at: str | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=50))

    def _compute(self, errors: Sequence[float]) -> dict:
        needed = self.window * 2
        if len(errors) < needed:
            self.state = "warming"
            return {
                "state": self.state,
                "recent_error": (sum(errors[-self.window :]) / len(errors[-self.window :])) if errors else None,
                "reference_error": None,
                "change": None,
                "samples": len(errors),
                "needed": needed,
            }
        recent = errors[-self.window :]
        reference = errors[-needed : -self.window]
        recent_rate = sum(recent) / len(recent)
        reference_rate = sum(reference) / len(reference)
        change = recent_rate - reference_rate
        if change > self.threshold:
            self.state = "drift"
        elif change < -self.threshold:
            self.state = "improving"
        else:
            self.state = "stable"
        return {
            "state": self.state,
            "recent_error": recent_rate,
            "reference_error": reference_rate,
            "change": change,
            "samples": len(errors),
            "needed": needed,
        }

    def evaluate(self, errors: Sequence[float], *, now: datetime | None = None) -> dict:
        """Evaluate and record the transition (used once per resolved outcome)."""
        result = self._compute(errors)
        if result["state"] == "drift" and result["change"] is not None:
            self.last_shift_at = (now or _now()).isoformat()
        if result["state"] != "warming":
            self.history.append({
                "at": (now or _now()).isoformat(),
                "recent": round(result["recent_error"], 4),
                "reference": round(result["reference_error"], 4),
                "change": round(result["change"], 4),
                "state": self.state,
            })
        return result

    def peek(self, errors: Sequence[float]) -> dict:
        """Read the current state without mutating it (safe for status polls)."""
        return self._compute(errors)


# ---------------------------------------------------------------------------
# Versioned model registry
# ---------------------------------------------------------------------------
@dataclass
class ModelRegistry:
    """Small on-disk version store so a bad update can be rolled back."""

    root: Path
    keep: int = 12

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _read_index(self) -> dict:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("versions"), list):
                return payload
        except (OSError, ValueError):
            pass
        return {"versions": [], "active": None}

    def _write_index(self, payload: dict) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.index_path)

    def save(self, model: OnlineLogisticModel, *, reason: str, metrics: dict | None = None) -> dict:
        index = self._read_index()
        sequence = len(index["versions"]) + 1
        version = f"v{sequence:04d}"
        while (self.root / f"{version}.json").exists():
            sequence += 1
            version = f"v{sequence:04d}"
        model.trained_at = _now().isoformat()
        model.save(self.root / f"{version}.json")
        entry = {
            "version": version,
            "created_at": model.trained_at,
            "reason": reason,
            "training_samples": model.training_samples,
            "feature_version": model.feature_version,
            "validation_log_loss": model.validation_log_loss,
            "calibrated": model.calibrator.fitted,
            "metrics": metrics or {},
        }
        index["versions"].append(entry)
        index["active"] = version
        self._prune(index)
        self._write_index(index)
        return entry

    def _prune(self, index: dict) -> None:
        versions = index["versions"]
        if len(versions) <= self.keep:
            return
        for entry in versions[: len(versions) - self.keep]:
            if entry["version"] == index.get("active"):
                continue
            path = self.root / f"{entry['version']}.json"
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
        index["versions"] = versions[-self.keep :]

    def list_versions(self, limit: int = 10) -> list[dict]:
        index = self._read_index()
        versions = list(reversed(index["versions"]))
        for entry in versions:
            entry["active"] = entry["version"] == index.get("active")
        return versions[: max(1, limit)]

    def active_version(self) -> str | None:
        return self._read_index().get("active")

    def load(self, version: str | None = None) -> OnlineLogisticModel | None:
        index = self._read_index()
        target = version or index.get("active")
        if not target:
            return None
        path = self.root / f"{target}.json"
        if not path.exists():
            return None
        try:
            return OnlineLogisticModel.load(path)
        except (ValueError, KeyError, TypeError):
            return None

    def previous_version(self) -> str | None:
        index = self._read_index()
        versions = index["versions"]
        if len(versions) < 2:
            return None
        active = index.get("active")
        for entry in reversed(versions):
            if entry["version"] != active:
                return entry["version"]
        return None

    def set_active(self, version: str) -> dict:
        index = self._read_index()
        for entry in index["versions"]:
            if entry["version"] == version:
                index["active"] = version
                self._write_index(index)
                return entry
        raise ValueError(f"Model version {version} was not found")


# ---------------------------------------------------------------------------
# Self-learning engine
# ---------------------------------------------------------------------------
@dataclass
class SelfLearningEngine:
    """Guarded online learning on top of one champion model.

    Three rules keep it honest:
    1. Only resolved, non-synthetic outcomes are learned from, once each.
    2. An update is rolled back if it makes recent history worse.
    3. Calibration is refitted from real resolved outcomes, not from training
       data the model already saw.
    """

    pair: str
    dataset_id: str
    registry: ModelRegistry
    min_samples: int = 30
    replay_capacity: int = 200
    max_degradation: float = 0.02
    recalibrate_every: int = 50
    tracker: PrequentialTracker = field(default_factory=PrequentialTracker)
    drift: DriftMonitor = field(default_factory=DriftMonitor)
    replay: deque = field(default_factory=deque)
    accepted_updates: int = 0
    rejected_updates: int = 0
    promotions: int = 0
    rollbacks: int = 0
    recalibrations: int = 0
    calibration_samples: int = 0
    calibration_report: dict | None = None
    last_calibration_at: str | None = None
    last_update_at: str | None = None
    last_rejection_reason: str | None = None
    updates_since_calibration: int = 0
    version: str | None = None

    def observe(
        self,
        model: OnlineLogisticModel,
        features: dict[str, float],
        label: int,
        probability: float | None = None,
        *,
        issued_probability: float | None = None,
        timestamp: datetime | None = None,
    ) -> dict:
        if label not in (0, 1):
            raise ValueError("Learning labels must be 0 (down) or 1 (up)")
        if not features:
            raise ValueError("Learning requires the stored feature row")
        if not model.trained:
            raise ValueError("Model must be trained before learning")

        prediction_before = model.predict_up(features)
        # Prefer the probability that was actually issued with the signal: the
        # champion may have changed between issue and resolution.
        issued = float(issued_probability) if issued_probability is not None else (
            float(probability) if probability is not None else prediction_before
        )
        correct_before = (prediction_before >= 0.5) == bool(label)
        issued_correct = (issued >= 0.5) == bool(label)
        self.tracker.observe(issued, int(label), correct=issued_correct, timestamp=timestamp)
        self.replay.append((dict(features), int(label)))
        while len(self.replay) > self.replay_capacity:
            self.replay.popleft()

        drift_state = self.drift.evaluate(self.tracker.errors(), now=timestamp)
        warming_up = self.tracker.total_seen < self.min_samples

        before_loss = self._replay_log_loss(model)
        snapshot = model.snapshot()
        model.learn_one(features, int(label))
        after_loss = self._replay_log_loss(model)

        accepted = True
        reason = "Update accepted: replay log loss did not degrade."
        if (
            len(self.replay) >= max(20, self.min_samples)
            and before_loss is not None
            and after_loss is not None
            and after_loss > before_loss * (1.0 + self.max_degradation)
        ):
            model.restore(snapshot)
            accepted = False
            self.rejected_updates += 1
            self.last_rejection_reason = (
                f"Update reverted: replay log loss rose from {before_loss:.4f} to {after_loss:.4f}."
            )
            reason = self.last_rejection_reason
        if accepted:
            self.accepted_updates += 1
            self.updates_since_calibration += 1

        self.last_update_at = (timestamp or _now()).isoformat()
        return {
            "learned": accepted,
            "evaluated": True,
            "warming_up": warming_up,
            "accepted": accepted,
            "reason": reason,
            "probability_up_before_update": prediction_before,
            "prediction_correct_before_update": correct_before,
            "issued_probability": issued,
            "issued_correct": issued_correct,
            "training_samples": model.training_samples,
            "replay_log_loss_before": before_loss,
            "replay_log_loss_after": after_loss if accepted else before_loss,
            "drift": drift_state,
            "rejected_updates": self.rejected_updates,
        }

    def _replay_log_loss(self, model: OnlineLogisticModel) -> float | None:
        if not self.replay:
            return None
        points = [(model.predict_up(features), label) for features, label in self.replay]
        return log_loss(points)

    # ------------------------------------------------------------- calibration
    def recalibrate(self, model: OnlineLogisticModel, *, force: bool = False) -> dict | None:
        """Refit calibration from stored real outcomes using the current champion.

        Scores come from replaying stored feature rows through the champion, so
        the calibrator always maps raw model scores. The probabilities in the
        tracker may already be calibrated and are used only for performance
        statistics.
        """
        if not force and self.updates_since_calibration < self.recalibrate_every:
            return None
        pairs = list(self.replay)
        if len(pairs) < 60:
            return None

        def points_for(rows) -> list[tuple[float, int]]:
            return [(model.predict_up(features), label) for features, label in rows]

        self.updates_since_calibration = 0
        calibrator, report = fit_validated(points_for(pairs))
        self.calibration_report = report
        if not report.get("accepted") or not calibrator.fitted:
            return report

        model.calibrator = calibrator
        self.recalibrations += 1
        self.calibration_samples = report["samples"]
        self.last_calibration_at = _now().isoformat()
        return report

    def should_recalibrate(self) -> bool:
        return self.updates_since_calibration >= self.recalibrate_every

    # ------------------------------------------------------- champion/challenger
    def compare(self, champion: OnlineLogisticModel, challenger: OnlineLogisticModel) -> dict:
        if not self.replay:
            return {"samples": 0, "champion_log_loss": None, "challenger_log_loss": None, "challenger_better": False}
        pairs = list(self.replay)
        champion_loss = log_loss([(champion.predict_up(f), y) for f, y in pairs])
        challenger_loss = log_loss([(challenger.predict_up(f), y) for f, y in pairs])
        return {
            "samples": len(pairs),
            "champion_log_loss": champion_loss,
            "challenger_log_loss": challenger_loss,
            "challenger_better": bool(
                champion_loss is not None and challenger_loss is not None and challenger_loss < champion_loss - 1e-4
            ),
        }

    def register_promotion(self, version: str) -> None:
        self.promotions += 1
        self.version = version

    def register_rollback(self, version: str | None) -> None:
        self.rollbacks += 1
        self.version = version

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "pair": self.pair,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "tracker": self.tracker.summary(),
            "tracker_recent": self.tracker.summary(window=100),
            "drift": self.drift.peek(self.tracker.errors()),
            "counts": {
                "accepted_updates": self.accepted_updates,
                "rejected_updates": self.rejected_updates,
                "promotions": self.promotions,
                "rollbacks": self.rollbacks,
                "recalibrations": self.recalibrations,
            },
            "replay_samples": len(self.replay),
            "calibration": self.calibration_report,
            "last_calibration_at": self.last_calibration_at,
            "last_update_at": self.last_update_at,
            "last_rejection_reason": self.last_rejection_reason,
            "updates_since_calibration": self.updates_since_calibration,
            "recalibrate_every": self.recalibrate_every,
            "versions": self.registry.list_versions(limit=8),
            "active_version": self.registry.active_version(),
        }


def engine_config_from_env(environ) -> dict[str, Any]:
    def read_int(name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(str(environ.get(name, default)).strip())
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    def read_float(name: str, default: float, low: float, high: float) -> float:
        try:
            value = float(str(environ.get(name, default)).strip())
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    return {
        "ensemble_size": read_int("ORBIX_ENSEMBLE_SIZE", 3, 2, 5),
        "max_uncertainty": read_float("ORBIX_MAX_UNCERTAINTY", 0.10, 0.0, 0.5),
        "max_degradation": read_float("ORBIX_MAX_UPDATE_DEGRADATION", 0.02, 0.0, 0.5),
        "recalibrate_every": read_int("ORBIX_RECALIBRATE_EVERY", 50, 10, 1000),
        "min_learning_samples": read_int("ORBIX_MIN_LEARNING_SAMPLES", 30, 10, 500),
        "validation_fraction": read_float("ORBIX_VALIDATION_FRACTION", 0.15, 0.0, 0.4),
    }
