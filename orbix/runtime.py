from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import re
import threading

from .candle_store import CandleStore, TAIL_ROWS
from .domain import Candle, parse_datetime
from .engine import PredictionEngine
from .features import FEATURE_NAMES, FEATURE_VERSION, compute_features
from .learning import ModelRegistry, SelfLearningEngine, engine_config_from_env
from .ledger import PaperLedger
from .market_data import BASE_PRICES, build_demo_candles
from .model import (
    BootstrapEnsemble,
    OnlineLogisticModel,
    build_training_samples,
    train_from_candles,
)
from .prediction import build_prediction


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SIGNAL_WINDOW = 720
MODEL_TRAINING_WINDOW = 5_000
BACKTEST_WINDOW = 2_500
VALIDATION_FRACTION = 0.15


def _safe_pair(pair: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", pair).strip("_") or "pair"


class Runtime:
    """Application state with a durable candle archive and a bounded hot window."""

    def __init__(self, data_dir: str | Path = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.model_dir = self.data_dir / "models"
        self.lock = threading.RLock()
        self.candle_store = CandleStore(self.data_dir / "candles.duckdb")
        self.ledger = PaperLedger(self.data_dir / "orbix.sqlite3")
        self.engine = PredictionEngine()
        self.settings = engine_config_from_env(os.environ)
        self.pair = "EUR/USD"
        self.source = "SYNTHETIC DEMO"
        self.candles = build_demo_candles(self.pair)
        self.candle_count = len(self.candles)
        self._dataset_id: str | None = None
        self.models: dict[str, OnlineLogisticModel] = {}
        self._ensembles: dict[str, BootstrapEnsemble | None] = {}
        self._learning: dict[str, SelfLearningEngine] = {}
        self._last_promotion_seen: dict[str, int] = {}
        self._restore_active_dataset()

    # ------------------------------------------------------------------ state
    def _restore_active_dataset(self) -> None:
        active = self.candle_store.active_state()
        if not active:
            return
        if active["source"] == "SYNTHETIC DEMO":
            pair = active["pair"]
            if pair in BASE_PRICES:
                self.pair = pair
                self.candles = build_demo_candles(pair)
                self.candle_count = len(self.candles)
            return
        if active["source"] != "IMPORTED CSV" or not active.get("dataset_id"):
            return
        try:
            recent = self.candle_store.load_latest(active["dataset_id"], SIGNAL_WINDOW)
        except Exception:
            return
        if len(recent) < 120:
            return
        self.pair = active["pair"]
        self.source = "IMPORTED CSV"
        self.candles = recent
        self.candle_count = int(active["candle_count"])
        self._dataset_id = active["dataset_id"]

    def dataset_id(self) -> str:
        if self.source == "IMPORTED CSV" and self._dataset_id:
            return self._dataset_id
        digest = sha256()
        digest.update(f"{self.pair}\0{self.source}\n".encode("utf-8"))
        for candle in self.candles:
            digest.update(
                (
                    f"{candle.timestamp.isoformat()}|{candle.open:.15g}|{candle.high:.15g}|"
                    f"{candle.low:.15g}|{candle.close:.15g}|{candle.volume:.15g}\n"
                ).encode("utf-8")
            )
        return digest.hexdigest()

    def activate_dataset(self, dataset_id: str) -> dict:
        with self.lock:
            active = self.candle_store.activate_dataset(dataset_id)
            recent = self.candle_store.load_latest(dataset_id, SIGNAL_WINDOW)
            if len(recent) < 120:
                raise ValueError("Archived dataset does not contain enough candles for signals")
            self.pair = active["pair"]
            self.source = "IMPORTED CSV"
            self.candles = recent
            self.candle_count = active["candle_count"]
            self._dataset_id = dataset_id
            self.models.clear()
            self._ensembles.clear()
            self._learning.clear()
            return active

    def set_demo_pair(self, pair: str) -> None:
        pair = pair.upper().strip()
        if pair not in BASE_PRICES:
            raise ValueError("Choose one of the supported demo pairs")
        with self.lock:
            if self.source != "SYNTHETIC DEMO":
                raise ValueError("Reset to demo data before switching away from imported CSV history")
            if pair != self.pair:
                self.pair = pair
                self.candles = build_demo_candles(pair)
                self.candle_count = len(self.candles)
                self._dataset_id = None
                self.models.clear()
                self._ensembles.clear()
                self._learning.clear()
                self.candle_store.set_active_demo(pair)

    def reset_demo(self, pair: str = "EUR/USD") -> None:
        pair = pair.upper().strip()
        if pair not in BASE_PRICES:
            raise ValueError("Choose one of the supported demo pairs")
        with self.lock:
            self.pair = pair
            self.source = "SYNTHETIC DEMO"
            self.candles = build_demo_candles(pair)
            self.candle_count = len(self.candles)
            self._dataset_id = None
            self.models.clear()
            self._ensembles.clear()
            self._learning.clear()
            self.candle_store.set_active_demo(pair)

    # ------------------------------------------------------------------ files
    def model_path(self, pair: str) -> Path:
        return self.model_dir / f"{_safe_pair(pair)}_{self.dataset_id()[:16]}.json"

    def _registry_root(self, pair: str) -> Path:
        return self.model_dir / f"{_safe_pair(pair)}_{self.dataset_id()[:16]}"

    def _registry(self, pair: str) -> ModelRegistry:
        return ModelRegistry(self._registry_root(pair))

    # ------------------------------------------------------------------ import
    def import_csv_file(self, csv_path: str | Path, pair: str) -> dict:
        result = self.candle_store.import_csv_file(csv_path, pair, minimum=120)
        with self.lock:
            self.pair = result["pair"]
            self.source = "IMPORTED CSV"
            self.candles = result["recent"][-SIGNAL_WINDOW:]
            self.candle_count = int(result["count"])
            self._dataset_id = result["dataset_id"]
            self.models.clear()
            self._ensembles.clear()
            self._learning.clear()
        return result

    def append_csv_file(self, csv_path: str | Path, pair: str | None = None) -> dict:
        with self.lock:
            if self.source != "IMPORTED CSV" or not self._dataset_id:
                raise ValueError("Import a CSV dataset before appending candles")
            result = self.candle_store.append_csv_file(csv_path, pair or self.pair, minimum=1)
            self.candles = self.candle_store.load_latest(self._dataset_id, SIGNAL_WINDOW)
            self.candle_count = int(result["candle_count"])
            outcomes = self._resolve_matured_outcomes()
            result["auto_resolved"] = outcomes
            result["automatic_model_updates"] = sum(1 for outcome in outcomes if outcome["learned"])
            return result

    def training_candles(self, limit: int = MODEL_TRAINING_WINDOW) -> list[Candle]:
        if self.source == "IMPORTED CSV" and self._dataset_id:
            return self.candle_store.load_latest(self._dataset_id, min(limit, TAIL_ROWS))
        return self.candles[-min(limit, len(self.candles)):]

    def backtest_candles(self, limit: int = BACKTEST_WINDOW) -> list[Candle]:
        if self.source == "IMPORTED CSV" and self._dataset_id:
            return self.candle_store.load_latest(self._dataset_id, min(limit, TAIL_ROWS))
        return self.candles[-min(limit, len(self.candles)):]

    # ------------------------------------------------------------------ model
    def _load_champion(self, pair: str) -> OnlineLogisticModel | None:
        try:
            model = OnlineLogisticModel.load(self.model_path(pair))
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return None
        return model if model.trained else None

    def champion_model(self, pair: str) -> OnlineLogisticModel | None:
        """Cached or on-disk champion, without training a new one."""
        return self.models.get(pair) or self._load_champion(pair)

    def _save_champion(self, pair: str, model: OnlineLogisticModel) -> None:
        if self.source == "IMPORTED CSV" and self._dataset_id:
            model.save(self.model_path(pair))

    def get_or_train_model(self, pair: str) -> OnlineLogisticModel:
        if pair != self.pair:
            raise ValueError("The model pair must match the currently loaded candle data")
        cached = self.models.get(pair)
        if cached and cached.trained:
            return cached
        model = self._load_champion(pair)
        if model is None:
            history = self.training_candles()
            features = compute_features(history)
            model = train_from_candles(history, features)
            if not model.trained:
                raise ValueError("Not enough labeled candles to train a prediction model")
            self._save_champion(pair, model)
            if self.source == "IMPORTED CSV" and self._dataset_id:
                registry = self._registry(pair)
                if registry.load() is None:
                    # No usable snapshot (first run, or a model saved with an
                    # older feature set), so record this fit as the baseline.
                    registry.save(model, reason="bootstrap fit on completed candles")
        self.models[pair] = model
        return model

    def get_ensemble(self, pair: str) -> BootstrapEnsemble | None:
        """Bootstrap members for a stability estimate, rebuilt only on retrain."""
        if self.source != "IMPORTED CSV" or self.settings["ensemble_size"] < 2:
            return None
        if pair in self._ensembles:
            return self._ensembles[pair]
        ensemble: BootstrapEnsemble | None = None
        try:
            history = self.training_candles(min(MODEL_TRAINING_WINDOW, 3_000))
            samples = build_training_samples(history, compute_features(history))
            if len(samples) >= 60:
                ensemble = BootstrapEnsemble.fit(samples, size=self.settings["ensemble_size"])
        except (ValueError, FloatingPointError):
            ensemble = None
        self._ensembles[pair] = ensemble
        return ensemble

    # --------------------------------------------------------------- learning
    def _learning_key(self, pair: str) -> str:
        return f"{pair}::{self.dataset_id()}"

    def learning_engine(self, pair: str) -> SelfLearningEngine:
        key = self._learning_key(pair)
        engine = self._learning.get(key)
        if engine is not None:
            return engine
        registry = self._registry(pair)
        engine = SelfLearningEngine(
            pair=pair,
            dataset_id=self.dataset_id(),
            registry=registry,
            min_samples=self.settings["min_learning_samples"],
            max_degradation=self.settings["max_degradation"],
            recalibrate_every=self.settings["recalibrate_every"],
        )
        for row in self.ledger.learning_history(600):
            try:
                moment = parse_datetime(row["created_at"]) if row.get("created_at") else None
            except (ValueError, TypeError):
                moment = None
            engine.tracker.observe(
                row.get("probability_up"),
                row.get("label"),
                correct=None if row.get("correct") is None else bool(row["correct"]),
                timestamp=moment,
            )
            if row.get("model_updated"):
                engine.accepted_updates += 1
        engine.updates_since_calibration = engine.accepted_updates
        for row in self.ledger.learning_replay(engine.replay_capacity):
            engine.replay.append((row["features"], int(row["label"])))
        engine.version = registry.active_version()
        self._learning[key] = engine
        self._last_promotion_seen.setdefault(key, engine.tracker.total_seen)
        return engine

    def learn_from_record(self, record: dict) -> dict:
        with self.lock:
            label = record.get("direction_label")
            features = record.get("features") or {}
            if label is None or not features:
                self.ledger.record_learning_event(
                    signal_id=record["signal_id"],
                    pair=record["pair"],
                    dataset_id=record["dataset_id"],
                    label=None,
                    probability_up=None,
                    correct=None,
                    model_updated=False,
                )
                return {"learned": False, "reason": "No directional label was available (flat/tied outcome)."}
            if record["source"] != "IMPORTED CSV":
                return {"learned": False, "reason": "Synthetic demo outcomes are excluded from learning."}

            try:
                model = self.get_or_train_model(record["pair"])
            except ValueError as exc:
                self.ledger.record_learning_event(
                    signal_id=record["signal_id"],
                    pair=record["pair"],
                    dataset_id=record["dataset_id"],
                    label=int(label),
                    probability_up=None,
                    correct=None,
                    model_updated=False,
                )
                return {"learned": False, "reason": str(exc)}

            engine = self.learning_engine(record["pair"])
            result = engine.observe(
                model,
                features,
                int(label),
                issued_probability=record.get("probability_up"),
            )
            self.ledger.record_learning_event(
                signal_id=record["signal_id"],
                pair=record["pair"],
                dataset_id=record["dataset_id"],
                label=int(label),
                probability_up=result["issued_probability"],
                correct=result["issued_correct"],
                model_updated=bool(result["accepted"]),
            )
            calibration = None
            if model.calibrator.fitted:
                if engine.should_recalibrate():
                    calibration = engine.recalibrate(model)
            elif len(engine.replay) >= 60:
                # Refit only from resolved real outcomes, never from training rows.
                calibration = engine.recalibrate(model, force=True)
            self._save_champion(record["pair"], model)
            self.models[record["pair"]] = model
            return {
                "learned": bool(result["accepted"]),
                "evaluated": True,
                "accepted": bool(result["accepted"]),
                "reason": result["reason"],
                "probability_up_before_update": result["probability_up_before_update"],
                "prediction_correct_before_update": result["prediction_correct_before_update"],
                "training_samples": model.training_samples,
                "drift": result["drift"],
                "calibration": calibration,
            }

    # ------------------------------------------------------------------ signals
    def generate_signal(
        self,
        pair: str,
        strategy_id: str = "ml_consensus",
        horizon: int = 1,
        *,
        generated_at: datetime | None = None,
        is_live: bool = False,
        safety_filter_reason: str | None = None,
    ) -> dict:
        pair = pair.upper().strip()
        with self.lock:
            if self.source == "SYNTHETIC DEMO":
                self.set_demo_pair(pair)
            elif pair != self.pair:
                raise ValueError(f"Imported CSV data is for {self.pair}; reset to demo to switch pairs")

            # Keep a pre-outcome model ready for every imported-data signal. The
            # selected non-ML strategy still decides the signal on its own rules.
            learning_model = None
            if self.source == "IMPORTED CSV" or strategy_id == "ml_consensus":
                try:
                    learning_model = self.get_or_train_model(pair)
                except ValueError:
                    if strategy_id == "ml_consensus":
                        raise
            ensemble = self.get_ensemble(pair) if learning_model is not None else None
            signal, _, features = self.engine.generate_signal(
                candles=self.candles,
                pair=pair,
                strategy_id=strategy_id,
                source=self.source,
                horizon=horizon,
                model=learning_model,
                generated_at=generated_at,
                is_live=is_live,
                ensemble=ensemble,
                max_uncertainty=self.settings["max_uncertainty"],
            )
            if safety_filter_reason:
                signal = replace(signal, side="NO SIGNAL", reason=safety_filter_reason)
            if learning_model is not None and self.source == "IMPORTED CSV":
                learning_model.save(self.model_path(pair))
            active_dataset = self.dataset_id()
            if signal.side == "NO SIGNAL":
                # Abstentions are logged so restraint can be measured, but they
                # never enter the paper signal or learning tables.
                self.ledger.record_abstention(
                    pair=pair,
                    strategy=strategy_id,
                    reason=signal.reason,
                    kind="gate",
                    entry_time=signal.entry_time,
                    source=self.source,
                    dataset_id=active_dataset,
                    now=signal.generated_at,
                )
            else:
                self.ledger.record(signal, features, dataset_id=active_dataset)
            return signal.to_dict()

    # ------------------------------------------------------- promotion/rollback
    def _holdout_metrics(self, model: OnlineLogisticModel, rows) -> dict:
        from .calibration import accuracy, brier_score, log_loss

        points = [(model.predict_up(features), label) for features, label in rows]
        return {
            "samples": len(points),
            "log_loss": log_loss(points),
            "brier": brier_score(points),
            "accuracy": accuracy(points),
        }

    def retrain_model(self, pair: str) -> dict:
        """Fit a challenger and promote it only when it wins on unseen candles."""
        pair = pair.upper().strip()
        with self.lock:
            if self.source != "IMPORTED CSV" or not self._dataset_id:
                raise ValueError("Import real candle history before retraining the model")
            if pair != self.pair:
                raise ValueError(f"Imported CSV data is for {self.pair}; reset to demo to switch pairs")
            history = self.training_candles()
            if len(history) < 240:
                raise ValueError("At least 240 completed candles are needed for a validated retrain")
            features = compute_features(history)
            fraction = min(0.4, max(0.05, self.settings["validation_fraction"] or VALIDATION_FRACTION))
            split = max(120, int(len(history) * (1.0 - fraction)))
            training_samples = build_training_samples(history, features, end_index=split - 1)
            holdout_samples = build_training_samples(
                history,
                features,
                end_index=len(history) - 1,
                start_index=split,
            )
            if len(training_samples) < 120 or len(holdout_samples) < 40:
                raise ValueError(
                    "Not enough matured candles to hold out a validation slice; append more completed history first."
                )
            challenger = OnlineLogisticModel().fit(
                training_samples,
                validation_fraction=0.0,
            )
            champion = self.models.get(pair) or self._load_champion(pair)
            challenger_metrics = self._holdout_metrics(challenger, holdout_samples)
            champion_metrics = self._holdout_metrics(champion, holdout_samples) if champion else None

            promoted = False
            reason = "Challenger did not beat the champion on the held-out candles; the champion was kept."
            if champion_metrics is None or champion_metrics["log_loss"] is None:
                promoted = True
                reason = "No earlier champion existed, so the validated challenger became the active model."
            elif (
                challenger_metrics["log_loss"] is not None
                and challenger_metrics["log_loss"] < champion_metrics["log_loss"] - 1e-4
                and challenger_metrics["brier"] is not None
                and challenger_metrics["brier"] <= champion_metrics["brier"]
            ):
                promoted = True
                reason = "Challenger improved held-out log loss and did not worsen Brier score."

            registry = self._registry(pair)
            version = registry.active_version()
            if promoted:
                engine = self.learning_engine(pair)
                if len(engine.replay) >= 60:
                    # A challenger starts without a calibrator; fit one from
                    # stored real outcomes before snapshotting it.
                    engine.recalibrate(challenger, force=True)
                entry = registry.save(
                    challenger,
                    reason=f"validated retrain ({challenger_metrics['samples']} held-out candles)",
                    metrics={
                        "challenger": challenger_metrics,
                        "champion": champion_metrics,
                    },
                )
                version = entry["version"]
                challenger.save(self.model_path(pair))
                self.models[pair] = challenger
                self._ensembles.pop(pair, None)
                engine.register_promotion(version)
                self._last_promotion_seen[self._learning_key(pair)] = engine.tracker.total_seen
            else:
                self.models[pair] = champion  # type: ignore[assignment]

            return {
                "status": "promoted" if promoted else "kept_champion",
                "promoted": promoted,
                "reason": reason,
                "version": version,
                "active_version": registry.active_version(),
                "holdout_candles": challenger_metrics["samples"],
                "challenger": challenger_metrics,
                "champion": champion_metrics,
                "note": (
                    "Validation compares both models on candles the challenger never trained on. "
                    "The champion may already have learned from some of those candles online, so the "
                    "gate is deliberately conservative; it cannot prove future accuracy."
                ),
            }

    def rollback_model(self, pair: str) -> dict:
        pair = pair.upper().strip()
        with self.lock:
            if self.source != "IMPORTED CSV" or not self._dataset_id:
                raise ValueError("Model versions exist only for imported candle datasets")
            if pair != self.pair:
                raise ValueError(f"Imported CSV data is for {self.pair}; reset to demo to switch pairs")
            registry = self._registry(pair)
            active = registry.active_version()
            previous = None
            model = None
            # Walk backwards past any snapshot saved with an older feature set,
            # which cannot be interpreted by this build.
            for entry in registry.list_versions(limit=registry.keep):
                if entry["version"] == active:
                    continue
                candidate = registry.load(entry["version"])
                if candidate is not None:
                    previous = entry["version"]
                    model = candidate
                    break
            if previous is None or model is None:
                raise ValueError("No earlier validated model version is available to restore")
            registry.set_active(previous)
            self.models[pair] = model
            self._ensembles.pop(pair, None)
            engine = self.learning_engine(pair)
            if len(engine.replay) >= 60:
                engine.recalibrate(model, force=True)
            model.save(self.model_path(pair))
            engine.register_rollback(previous)
            return {
                "status": "rolled_back",
                "active_version": previous,
                "training_samples": model.training_samples,
                "validation_log_loss": model.validation_log_loss,
                "note": "Rolled back to a previously validated snapshot. New outcomes continue to be tracked.",
            }

    def learning_maintenance(self) -> dict:
        """Periodic calibration refit plus drift-triggered, validated retrain."""
        with self.lock:
            if self.source != "IMPORTED CSV" or not self._dataset_id:
                return {"status": "skipped", "reason": "Learning maintenance runs only on imported candle data."}
            model = self.models.get(self.pair) or self._load_champion(self.pair)
            if model is None:
                return {"status": "skipped", "reason": "No trained champion exists yet."}
            engine = self.learning_engine(self.pair)
            drift = engine.drift.peek(engine.tracker.errors())
            result: dict = {"drift": drift, "recalibrated": False, "retrained": False}
            if len(engine.replay) >= 60 and (not model.calibrator.fitted or engine.should_recalibrate()):
                report = engine.recalibrate(model, force=True)
                if report:
                    result["calibration"] = report
                    if report.get("accepted"):
                        self._save_champion(self.pair, model)
                        result["recalibrated"] = True
            # Guarded online updates move the champion, so rebuild the
            # bootstrap members on the slow maintenance cadence.
            self._ensembles.pop(self.pair, None)
            key = self._learning_key(self.pair)
            learned_since = engine.tracker.total_seen - self._last_promotion_seen.get(key, 0)
            if drift["state"] == "drift" and learned_since >= 100:
                try:
                    result["retrain"] = self.retrain_model(self.pair)
                    result["retrained"] = True
                except ValueError as exc:
                    result["retrain_error"] = str(exc)
                # One attempt per 100 newly resolved outcomes, promoted or not.
                self._last_promotion_seen[key] = engine.tracker.total_seen
            return result

    # ------------------------------------------------------------------ status
    def model_card(self, pair: str) -> dict:
        pair = pair.upper().strip()
        with self.lock:
            if self.source == "SYNTHETIC DEMO":
                self.set_demo_pair(pair)
            elif pair != self.pair:
                raise ValueError(f"Imported CSV data is for {self.pair}; reset to demo to switch pairs")
            try:
                model = self.get_or_train_model(pair)
            except ValueError as exc:
                return {
                    "pair": pair,
                    "source": self.source,
                    "dataset_id": self.dataset_id(),
                    "model_ready": False,
                    "reason": str(exc),
                    "feature_version": FEATURE_VERSION,
                    "feature_count": len(FEATURE_NAMES),
                    "versions": [],
                    "active_version": None,
                    "note": "The model needs at least 30 labelled completed candles.",
                }
            features = compute_features(self.candles)
            latest_features = features[-1] if features else None
            ensemble = self.get_ensemble(pair)
            prediction = (
                build_prediction(
                    model,
                    latest_features,
                    ensemble=ensemble,
                    max_uncertainty=self.settings["max_uncertainty"],
                )
                if latest_features
                else None
            )
            engine = self.learning_engine(pair) if self.source == "IMPORTED CSV" else None
            registry = self._registry(pair) if self.source == "IMPORTED CSV" else None
            return {
                "pair": pair,
                "source": self.source,
                "dataset_id": self.dataset_id(),
                "model_ready": model.trained,
                "feature_version": model.feature_version,
                "feature_count": len(model.feature_names),
                "training_samples": model.training_samples,
                "validation_log_loss": model.validation_log_loss,
                "trained_at": model.trained_at,
                "calibrated": model.calibrator.fitted,
                "calibration_samples": model.calibrator.samples,
                "calibration_report": engine.calibration_report if engine else None,
                "prediction": prediction.to_dict() if prediction else None,
                "uncertainty_limit": self.settings["max_uncertainty"],
                "ensemble_members": len(ensemble.members) if ensemble and ensemble.ready else 0,
                "drift": engine.drift.peek(engine.tracker.errors()) if engine else {"state": "untracked"},
                "prequential": engine.tracker.summary() if engine else None,
                "versions": registry.list_versions(limit=8) if registry else [],
                "active_version": registry.active_version() if registry else None,
                "by_strategy": self.ledger.learning_by_strategy(dataset_id=self.dataset_id()),
                "note": (
                    "Trained on synthetic demo candles for interface testing only. This model never learns from outcomes "
                    "and its probabilities are not market evidence."
                    if self.source == "SYNTHETIC DEMO"
                    else (
                        "Probabilities are calibrated only on resolved, non-synthetic outcomes. "
                        "A calibration fit describes the past; it is not a guarantee for the next candle."
                    )
                ),
            }

    def storage_summary(self) -> dict:
        summary = self.candle_store.summary()
        summary["active_window_in_memory"] = len(self.candles)
        summary["signal_window_limit"] = SIGNAL_WINDOW
        summary["model_training_window"] = MODEL_TRAINING_WINDOW
        summary["backtest_window"] = BACKTEST_WINDOW
        return summary

    def abstention_summary(self, days: int = 7, limit: int = 25) -> dict:
        return self.ledger.abstention_summary(days=days, limit=limit)

    def learning_summary(self) -> dict:
        summary = self.ledger.learning_summary()
        try:
            summary["abstentions"] = self.ledger.abstention_summary(days=7, limit=0)
        except Exception:
            summary["abstentions"] = None
        model = self.models.get(self.pair)
        if model is None and self.source == "IMPORTED CSV" and self._dataset_id:
            model = self._load_champion(self.pair)
        engine = None
        if self.source == "IMPORTED CSV" and self._dataset_id:
            try:
                engine = self.learning_engine(self.pair)
            except Exception:
                engine = None
        registry = self._registry(self.pair) if engine else None
        summary.update({
            "source": self.source,
            "enabled_for_imported_csv": self.source == "IMPORTED CSV",
            "model_ready": bool(model and model.trained),
            "training_samples": model.training_samples if model and model.trained else 0,
            "update_policy": "One test-before-learn update per resolved, non-tied imported-data signal outcome.",
            "feature_version": FEATURE_VERSION,
            "feature_count": len(FEATURE_NAMES),
            "calibrated": bool(model and model.calibrator.fitted),
            "calibration_samples": model.calibrator.samples if model else 0,
            "validation_log_loss": model.validation_log_loss if model else None,
            "drift": engine.drift.peek(engine.tracker.errors()) if engine else {"state": "untracked"},
            "learning_engine": engine.status() if engine else None,
            "versions": registry.list_versions(limit=8) if registry else [],
            "active_version": registry.active_version() if registry else None,
            "by_strategy": self.ledger.learning_by_strategy(),
            "settings": {
                "ensemble_size": self.settings["ensemble_size"],
                "max_uncertainty": self.settings["max_uncertainty"],
                "max_update_degradation": self.settings["max_degradation"],
                "recalibrate_every": self.settings["recalibrate_every"],
                "min_learning_samples": self.settings["min_learning_samples"],
            },
        })
        return summary

    def _resolve_matured_outcomes(self) -> list[dict]:
        if self.source != "IMPORTED CSV" or not self._dataset_id:
            return []
        candle_by_timestamp = {candle.timestamp: candle for candle in self.candles}
        outcomes: list[dict] = []
        for pending in self.ledger.list_pending(pair=self.pair, dataset_id=self._dataset_id):
            expires_at = parse_datetime(pending["expires_at"])
            # A candle timestamp is its open time; its close is the outcome at expiry.
            target_timestamp = expires_at - timedelta(minutes=1)
            target = candle_by_timestamp.get(target_timestamp)
            if target is None:
                target = self.candle_store.get_candle(self._dataset_id, target_timestamp)
            if target is None:
                continue
            try:
                resolved = self.ledger.resolve(pending["signal_id"], target.close)
            except ValueError:
                continue
            learning = self.learn_from_record(resolved)
            outcomes.append({
                "signal_id": pending["signal_id"],
                "outcome": resolved["outcome"],
                "learned": learning["learned"],
                "reason": learning.get("reason"),
                "training_samples": learning.get("training_samples"),
            })
        return outcomes
