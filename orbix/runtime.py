from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import re
import threading

from .candle_store import CandleStore, TAIL_ROWS
from .domain import Candle, parse_datetime
from .engine import PredictionEngine
from .ledger import PaperLedger
from .market_data import BASE_PRICES, build_demo_candles
from .model import OnlineLogisticModel


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SIGNAL_WINDOW = 720
MODEL_TRAINING_WINDOW = 5_000
BACKTEST_WINDOW = 2_500


class Runtime:
    """Application state with a durable candle archive and a bounded hot window."""

    def __init__(self, data_dir: str | Path = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.model_dir = self.data_dir / "models"
        self.lock = threading.RLock()
        self.candle_store = CandleStore(self.data_dir / "candles.duckdb")
        self.ledger = PaperLedger(self.data_dir / "orbix.sqlite3")
        self.engine = PredictionEngine()
        self.pair = "EUR/USD"
        self.source = "SYNTHETIC DEMO"
        self.candles = build_demo_candles(self.pair)
        self.candle_count = len(self.candles)
        self._dataset_id: str | None = None
        self.models: dict[str, OnlineLogisticModel] = {}
        self._restore_active_dataset()

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
            self.candle_store.set_active_demo(pair)

    def model_path(self, pair: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", pair).strip("_") or "pair"
        return self.model_dir / f"{safe}_{self.dataset_id()[:16]}.json"

    def import_csv_file(self, csv_path: str | Path, pair: str) -> dict:
        result = self.candle_store.import_csv_file(csv_path, pair, minimum=120)
        with self.lock:
            self.pair = result["pair"]
            self.source = "IMPORTED CSV"
            self.candles = result["recent"][-SIGNAL_WINDOW:]
            self.candle_count = int(result["count"])
            self._dataset_id = result["dataset_id"]
            self.models.clear()
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

    def get_or_train_model(self, pair: str) -> OnlineLogisticModel:
        if pair != self.pair:
            raise ValueError("The model pair must match the currently loaded candle data")
        cached = self.models.get(pair)
        if cached and cached.trained:
            return cached
        path = self.model_path(pair)
        try:
            model = OnlineLogisticModel.load(path)
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            model = None
        if model is None or not model.trained:
            history = self.training_candles()
            _, model, _ = self.engine.generate_signal(
                history,
                pair,
                strategy_id="ml_consensus",
                source=self.source,
            )
        if model is None or not model.trained:
            raise ValueError("Not enough labeled candles to train a prediction model")
        self.models[pair] = model
        return model

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
            signal_model = learning_model if strategy_id in {"ml_consensus", "orbix_confluence"} else None
            signal, _, features = self.engine.generate_signal(
                candles=self.candles,
                pair=pair,
                strategy_id=strategy_id,
                source=self.source,
                horizon=horizon,
                model=signal_model,
                generated_at=generated_at,
                is_live=is_live,
            )
            if safety_filter_reason:
                signal = replace(signal, side="NO SIGNAL", reason=safety_filter_reason)
            if learning_model is not None and self.source == "IMPORTED CSV":
                learning_model.save(self.model_path(pair))
            self.ledger.record(signal, features, dataset_id=self.dataset_id())
            return signal.to_dict()

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
            probability_before = model.predict_up(features)
            correct_before = (probability_before >= 0.5) == bool(label)
            model.learn_one(features, int(label))
            model.save(self.model_path(record["pair"]))
            self.models[record["pair"]] = model
            self.ledger.record_learning_event(
                signal_id=record["signal_id"],
                pair=record["pair"],
                dataset_id=record["dataset_id"],
                label=int(label),
                probability_up=probability_before,
                correct=correct_before,
                model_updated=True,
            )
            return {
                "learned": True,
                "probability_up_before_update": probability_before,
                "prediction_correct_before_update": correct_before,
                "training_samples": model.training_samples,
            }

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
                "training_samples": learning.get("training_samples"),
            })
        return outcomes

    def storage_summary(self) -> dict:
        summary = self.candle_store.summary()
        summary["active_window_in_memory"] = len(self.candles)
        summary["signal_window_limit"] = SIGNAL_WINDOW
        summary["model_training_window"] = MODEL_TRAINING_WINDOW
        summary["backtest_window"] = BACKTEST_WINDOW
        return summary

    def learning_summary(self) -> dict:
        summary = self.ledger.learning_summary()
        model = self.models.get(self.pair)
        if model is None and self.source == "IMPORTED CSV" and self._dataset_id:
            try:
                model = OnlineLogisticModel.load(self.model_path(self.pair))
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                model = None
        summary.update({
            "source": self.source,
            "enabled_for_imported_csv": self.source == "IMPORTED CSV",
            "model_ready": bool(model and model.trained),
            "training_samples": model.training_samples if model and model.trained else 0,
            "update_policy": "One test-before-learn update per resolved, non-tied imported-data signal outcome.",
        })
        return summary


runtime = Runtime()
