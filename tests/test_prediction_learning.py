from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest

from orbix.abstentions import classify_reason
from orbix.calibration import IsotonicCalibrator, brier_score, expected_calibration_error, fit_validated
from orbix.engine import PredictionEngine
from orbix.features import FEATURE_LABELS, FEATURE_NAMES, FEATURE_VERSION, compute_features
from orbix.learning import DriftMonitor, ModelRegistry, PrequentialTracker, SelfLearningEngine
from orbix.market_data import build_demo_candles
from orbix.model import OnlineLogisticModel, build_training_samples
from orbix.prediction import build_prediction
from orbix.runtime import Runtime


UTC = timezone.utc


def demo_samples(count: int = 700):
    candles = build_demo_candles("EUR/USD", count=count, now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    rows = compute_features(candles)
    return candles, rows, build_training_samples(candles, rows)


def write_history(path: Path, count: int, first: datetime) -> datetime:
    price = 1.1
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for index in range(count):
            timestamp = first + timedelta(minutes=index)
            opening = price
            drift = 0.00009 if index % 4 in (0, 1) else -0.00003
            close = price * (1.0 + drift + 0.00002 * math.sin(index / 7))
            writer.writerow([
                timestamp.isoformat().replace("+00:00", "Z"),
                format(opening, ".12f"),
                format(max(opening, close) + 0.00004, ".12f"),
                format(min(opening, close) - 0.00004, ".12f"),
                format(close, ".12f"),
                100,
            ])
            price = close
    return first + timedelta(minutes=count - 1)


class CalibrationTests(unittest.TestCase):
    def test_calibrator_waits_for_enough_points_and_stays_monotone(self) -> None:
        cold = IsotonicCalibrator.fit([(0.55, 1)] * 20)
        self.assertFalse(cold.fitted)
        self.assertAlmostEqual(cold.transform(0.7), 0.7)

        points = []
        for index in range(240):
            probability = 0.30 + 0.4 * (index / 239)
            label = 1 if index % 10 < round(probability * 10) else 0
            points.append((probability, label))
        fitted = IsotonicCalibrator.fit(points)
        self.assertTrue(fitted.fitted)
        values = [fitted.transform(0.30 + 0.4 * step / 20) for step in range(21)]
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(min(values), 0.01)
        self.assertLessEqual(max(values), 0.99)
        self.assertIsNotNone(brier_score(points))
        self.assertIsNotNone(expected_calibration_error(points))

    def test_validated_fit_rejects_a_mapping_that_degrades_newer_rows(self) -> None:
        probabilities = [0.40 + 0.20 * (index % 11) / 10 for index in range(200)]
        training = [(p, 1 if p > 0.50 else 0) for p in probabilities[:140]]
        reversed_rows = [(p, 0 if p > 0.50 else 1) for p in probabilities[140:]]
        calibrator, report = fit_validated(training + reversed_rows)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["samples"], 140)
        self.assertEqual(report["validated_on"], 60)
        self.assertGreater(report["brier_after"], report["brier_before"])
        self.assertIn("previous mapping", report["reason"])

    def test_calibration_round_trip(self) -> None:
        points = [(0.4 + 0.2 * (index % 5), index % 2) for index in range(200)]
        calibrator = IsotonicCalibrator.fit(points)
        restored = IsotonicCalibrator.from_dict(calibrator.to_dict())
        self.assertEqual(restored.thresholds, calibrator.thresholds)
        self.assertEqual(restored.values, calibrator.values)
        self.assertAlmostEqual(restored.transform(0.5), calibrator.transform(0.5))


class ModelTests(unittest.TestCase):
    def test_model_shape_guard_and_contributions(self) -> None:
        _, rows, samples = demo_samples()
        self.assertEqual(FEATURE_VERSION, 2)
        self.assertEqual(len(FEATURE_NAMES), 26)
        self.assertEqual(len(set(FEATURE_NAMES)), len(FEATURE_NAMES))
        self.assertEqual(set(FEATURE_LABELS), set(FEATURE_NAMES))
        model = OnlineLogisticModel().fit(samples)
        probability = model.predict_up(rows[-1])
        self.assertGreater(probability, 0.0)
        self.assertLess(probability, 1.0)
        contributions = model.contributions(rows[-1], top=5)
        self.assertEqual(len(contributions), 5)
        magnitudes = [abs(item["contribution"]) for item in contributions]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))
        self.assertTrue(all(item["label"] for item in contributions))

        payload = model.to_dict()
        self.assertEqual(payload["feature_version"], FEATURE_VERSION)
        restored = OnlineLogisticModel.from_dict(payload)
        self.assertAlmostEqual(restored.predict_up(rows[-1]), probability)

        stale = dict(payload)
        stale.pop("feature_version")
        with self.assertRaisesRegex(ValueError, "feature version"):
            OnlineLogisticModel.from_dict(stale)

    def test_prediction_uses_calibrator_and_reports_uncertainty(self) -> None:
        _, rows, samples = demo_samples()
        model = OnlineLogisticModel().fit(samples)
        raw = model.predict_up(rows[-1])

        class StableEnsemble:
            ready = True
            members = [object(), object(), object()]

            def predict(self, features):
                return raw, 0.02

        prediction = build_prediction(model, rows[-1], ensemble=StableEnsemble(), max_uncertainty=0.1)
        self.assertAlmostEqual(prediction.probability_up, raw)
        self.assertAlmostEqual(prediction.uncertainty, 0.02)
        self.assertEqual(prediction.ensemble_members, 3)
        self.assertFalse(prediction.calibrated)
        self.assertIsNone(prediction.abstain_reason)

        class DisagreeingEnsemble(StableEnsemble):
            def predict(self, features):
                return raw, 0.30

        warning = build_prediction(model, rows[-1], ensemble=DisagreeingEnsemble(), max_uncertainty=0.1)
        self.assertIsNotNone(warning.abstain_reason)
        self.assertIn("stability limit", warning.abstain_reason)

    def test_uncertainty_reason_has_a_stable_abstention_code(self) -> None:
        self.assertEqual(
            classify_reason(
                "Ensemble disagreement is 0.300, wider than the 0.10 stability limit; abstaining instead of guessing."
            ),
            "uncertainty",
        )

    def test_engine_signal_carries_raw_and_production_probability(self) -> None:
        candles, rows, samples = demo_samples(360)
        model = OnlineLogisticModel().fit(samples)
        signal, fitted, features = PredictionEngine().generate_signal(
            candles, "EUR/USD", "ml_consensus", "IMPORTED CSV", model=model
        )
        self.assertIs(fitted, model)
        self.assertIsNotNone(features)
        payload = signal.to_dict()
        self.assertIsNotNone(payload["probability_up"])
        self.assertIsNotNone(payload["probability_raw"])
        self.assertFalse(payload["probability_calibrated"])
        self.assertTrue(payload["contributions"])
        self.assertIn("probability", payload["reason"].lower())


class LearningEngineTests(unittest.TestCase):
    def test_tracker_and_drift_detection(self) -> None:
        tracker = PrequentialTracker(capacity=100)
        for _ in range(60):
            tracker.observe(0.7, 1)
        summary = tracker.summary()
        self.assertEqual(summary["samples"], 60)
        self.assertEqual(summary["accuracy"], 1.0)
        self.assertEqual(summary["correct"], 60)
        self.assertAlmostEqual(summary["accuracy_ci95"]["lower"], 0.9394, places=3)
        self.assertAlmostEqual(summary["brier"], 0.09)
        self.assertGreater(summary["skill_vs_coin_flip"], 0)

        monitor = DriftMonitor(window=20, threshold=0.1)
        self.assertEqual(monitor.peek([0.0] * 30)["state"], "warming")
        state = monitor.evaluate([0.0] * 20 + [1.0] * 20)
        self.assertEqual(state["state"], "drift")
        self.assertAlmostEqual(state["change"], 1.0)
        self.assertEqual(monitor.peek([1.0] * 20 + [0.0] * 20)["state"], "improving")

    def test_guarded_update_reverts_when_replay_worsens(self) -> None:
        _, rows, samples = demo_samples()
        model = OnlineLogisticModel().fit(samples)
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = SelfLearningEngine(
                pair="EUR/USD",
                dataset_id="dataset",
                registry=ModelRegistry(Path(temp_dir)),
                min_samples=5,
                max_degradation=0.0,
                recalibrate_every=10,
            )
            features = rows[-1]
            # Replay says "down" while the new outcome says "up", so a positive
            # update strictly worsens replay loss and must be rolled back.
            engine.replay.extend([(dict(features), 0)] * 40)
            before = model.predict_up(features)
            result = engine.observe(model, features, 1, before)
            self.assertTrue(result["evaluated"])
            self.assertFalse(result["accepted"])
            self.assertEqual(engine.rejected_updates, 1)
            self.assertAlmostEqual(model.predict_up(features), before)
            self.assertIn("reverted", result["reason"])

    def test_accepted_updates_calibrate_from_resolved_outcomes(self) -> None:
        _, rows, samples = demo_samples()
        model = OnlineLogisticModel().fit(samples)
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = SelfLearningEngine(
                pair="EUR/USD",
                dataset_id="dataset",
                registry=ModelRegistry(Path(temp_dir)),
                min_samples=10,
                recalibrate_every=10,
            )
            for features, label in samples[:80]:
                engine.observe(model, features, label, 0.5)
            self.assertEqual(engine.tracker.total_seen, 80)
            self.assertGreater(engine.accepted_updates, 0)
            self.assertGreaterEqual(len(engine.replay), 80)
            report = engine.recalibrate(model, force=True)
            self.assertIsNotNone(report, "80 resolved outcomes are enough for a 60-point calibration")
            self.assertTrue(model.calibrator.fitted)
            self.assertEqual(report["samples"], 80)
            self.assertGreaterEqual(report["ece_after"], 0.0)
            self.assertEqual(engine.recalibrations, 1)
            status = engine.status()
            self.assertEqual(status["tracker"]["samples"], 80)
            self.assertIn(status["drift"]["state"], {"warming", "stable", "improving", "drift"})

    def test_split_calibration_validates_on_newer_outcomes(self) -> None:
        _, _, samples = demo_samples()
        model = OnlineLogisticModel().fit(samples)
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = SelfLearningEngine(
                pair="EUR/USD",
                dataset_id="dataset",
                registry=ModelRegistry(Path(temp_dir)),
                min_samples=10,
                recalibrate_every=10,
            )
            for features, label in samples[:200]:
                engine.observe(model, features, label, 0.5)
            report = engine.recalibrate(model, force=True)
            self.assertIsNotNone(report)
            self.assertEqual(report["samples"], 140)
            self.assertEqual(report["validated_on"], 60)
            self.assertIn("accepted", report)
            if report["accepted"]:
                self.assertTrue(model.calibrator.fitted)
                self.assertLessEqual(report["brier_after"], report["brier_before"] + 1e-9)
                self.assertLessEqual(report["ece_after"], report["ece_before"] + 1e-9)
            else:
                self.assertIn("previous mapping", report["reason"])

    def test_registry_versions_promote_and_roll_back(self) -> None:
        _, _, samples = demo_samples()
        model = OnlineLogisticModel().fit(samples)
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ModelRegistry(Path(temp_dir) / "versions", keep=4)
            first = registry.save(model, reason="bootstrap", metrics={"brier": 0.24})
            second = registry.save(model, reason="retrain", metrics={"brier": 0.23})
            self.assertEqual(first["version"], "v0001")
            self.assertEqual(second["version"], "v0002")
            self.assertEqual(registry.active_version(), "v0002")
            self.assertEqual(registry.previous_version(), "v0001")
            versions = registry.list_versions()
            self.assertTrue(versions[0]["active"])
            loaded = registry.load("v0001")
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.trained)
            registry.set_active("v0001")
            self.assertEqual(registry.active_version(), "v0001")
            with self.assertRaisesRegex(ValueError, "not found"):
                registry.set_active("v9999")


class RuntimeLearningTests(unittest.TestCase):
    def test_retrain_is_gated_and_rollback_requires_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / "history.csv"
            write_history(history, 320, datetime(2025, 5, 1, tzinfo=UTC))
            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")

            promoted = runtime.retrain_model("EUR/USD")
            self.assertTrue(promoted["promoted"])
            self.assertEqual(promoted["status"], "promoted")
            self.assertEqual(promoted["active_version"], "v0001")
            self.assertGreaterEqual(promoted["holdout_candles"], 40)
            self.assertIn("challenger", promoted)
            self.assertIn("future", promoted["note"])

            kept = runtime.retrain_model("EUR/USD")
            self.assertFalse(kept["promoted"])
            self.assertEqual(kept["status"], "kept_champion")
            self.assertGreaterEqual(kept["challenger"]["samples"], 40)

            with self.assertRaisesRegex(ValueError, "earlier validated model version"):
                runtime.rollback_model("EUR/USD")

            # Create a second snapshot, then verify the runtime restores the first.
            registry = runtime._registry("EUR/USD")
            registry.save(runtime.get_or_train_model("EUR/USD"), reason="test snapshot")
            self.assertEqual(registry.active_version(), "v0002")
            rolled = runtime.rollback_model("EUR/USD")
            self.assertEqual(rolled["status"], "rolled_back")
            self.assertEqual(rolled["active_version"], "v0001")
            self.assertEqual(runtime.get_or_train_model("EUR/USD").feature_version, FEATURE_VERSION)

            card = runtime.model_card("EUR/USD")
            self.assertTrue(card["model_ready"])
            self.assertEqual(card["feature_version"], FEATURE_VERSION)
            self.assertEqual(card["feature_count"], len(FEATURE_NAMES))
            self.assertIsNotNone(card["prediction"])
            self.assertIn("probability_up", card["prediction"])
            self.assertEqual(card["active_version"], "v0001")
            active_versions = [item["version"] for item in card["versions"] if item["active"]]
            self.assertEqual(active_versions, ["v0001"])
            self.assertEqual(card["by_strategy"], [])

            summary = runtime.learning_summary()
            self.assertEqual(summary["feature_version"], FEATURE_VERSION)
            self.assertIn("learning_engine", summary)
            self.assertIn("by_strategy", summary)
            self.assertEqual(summary["settings"]["ensemble_size"], runtime.settings["ensemble_size"])
            runtime.candle_store.close()

    def test_signal_outcome_flows_into_ledger_strategy_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / "history.csv"
            first = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=203)
            latest = write_history(history, 200, first)
            # Force the final bar above its recent high so the breakout rule fires.
            lines = history.read_text(encoding="utf-8").splitlines()
            fields = lines[-1].split(",")
            opening = float(fields[1])
            close = opening + 0.003
            fields[4] = format(close, ".12f")
            fields[2] = format(close + 0.00005, ".12f")
            lines[-1] = ",".join(fields)
            history.write_text("\n".join(lines) + "\n", encoding="utf-8")

            runtime = Runtime(data_dir=root / "data")
            runtime.import_csv_file(history, "EUR/USD")
            signal = runtime.generate_signal("EUR/USD", "breakout", 1)
            self.assertEqual(signal["side"], "BUY")
            self.assertIsNotNone(signal["probability_up"])
            self.assertTrue(signal["contributions"])

            append = root / "next.csv"
            exit_time = latest + timedelta(minutes=1)
            exit_price = signal["entry_price"] + 0.001
            with append.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                writer.writerow([
                    exit_time.isoformat().replace("+00:00", "Z"),
                    signal["entry_price"],
                    max(signal["entry_price"], exit_price) + 0.0001,
                    min(signal["entry_price"], exit_price) - 0.0001,
                    exit_price,
                    90,
                ])
            result = runtime.append_csv_file(append, "EUR/USD")
            self.assertEqual(result["automatic_model_updates"], 1)
            self.assertTrue(result["auto_resolved"][0]["learned"])

            summary = runtime.learning_summary()
            self.assertEqual(summary["online_updates"], 1)
            self.assertEqual(summary["learning_engine"]["tracker"]["samples"], 1)
            self.assertIsNotNone(summary["prequential_accuracy_ci95"]["lower"])
            strategy_rows = {row["strategy"]: row for row in summary["by_strategy"]}
            self.assertIn("breakout", strategy_rows)
            self.assertEqual(strategy_rows["breakout"]["evaluated"], 1)
            self.assertEqual(strategy_rows["breakout"]["wins"], 1)
            self.assertEqual(strategy_rows["breakout"]["resolved"], 1)

            card = runtime.model_card("EUR/USD")
            self.assertEqual(card["prequential"]["samples"], 1)
            self.assertEqual(card["prequential"]["correct"], int(summary["prequential_correct_predictions"]))
            runtime.candle_store.close()

    def test_demo_cannot_retrain_or_roll_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Runtime(data_dir=Path(temp_dir) / "data")
            with self.assertRaisesRegex(ValueError, "Import real candle history"):
                runtime.retrain_model("EUR/USD")
            with self.assertRaisesRegex(ValueError, "imported candle datasets"):
                runtime.rollback_model("EUR/USD")
            card = runtime.model_card("EUR/USD")
            self.assertTrue(card["model_ready"])
            self.assertEqual(card["versions"], [])
            self.assertIsNone(card["active_version"])
            runtime.candle_store.close()


if __name__ == "__main__":
    unittest.main()
