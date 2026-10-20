from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from orbix.backtest import run_walk_forward_backtest
from orbix.engine import PredictionEngine
from orbix.features import FEATURE_NAMES, compute_features
from orbix.market_data import build_demo_candles, parse_candles_csv, validate_candles
from orbix.model import OnlineLogisticModel, build_training_samples, train_from_candles
from orbix.strategies import STRATEGY_CATALOG, strategy_catalog_payload


class OrbixEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = build_demo_candles("EUR/USD", count=360, now=datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc))

    def test_demo_candles_are_valid_and_ordered(self) -> None:
        candles = validate_candles(self.candles, minimum=100)
        self.assertEqual(len(candles), 360)
        self.assertLess(candles[0].timestamp, candles[-1].timestamp)
        self.assertLessEqual(candles[-1].low, candles[-1].close)
        self.assertGreaterEqual(candles[-1].high, candles[-1].close)

    def test_feature_engine_uses_warmup_and_finite_values(self) -> None:
        rows = compute_features(self.candles)
        self.assertIsNone(rows[10])
        latest = rows[-1]
        self.assertIsNotNone(latest)
        self.assertTrue(all(name in latest for name in FEATURE_NAMES))
        self.assertTrue(all(value == value for value in latest.values()))

    def test_csv_import(self) -> None:
        csv_text = "timestamp,open,high,low,close,volume\n" + "\n".join(
            f"2026-01-01T00:{minute:02d}:00Z,1.1,1.101,1.099,1.1005,10" for minute in range(30)
        )
        candles = parse_candles_csv(csv_text, minimum=30)
        self.assertEqual(len(candles), 30)
        self.assertAlmostEqual(candles[0].close, 1.1005)

    def test_model_training_prediction_and_online_update(self) -> None:
        rows = compute_features(self.candles)
        samples = build_training_samples(self.candles, rows, horizon=1)
        self.assertGreater(len(samples), 100)
        model = OnlineLogisticModel().fit(samples)
        before = model.training_samples
        probability = model.predict_up(rows[-1])
        self.assertGreaterEqual(probability, 0.0)
        self.assertLessEqual(probability, 1.0)
        model.learn_one(rows[-1], 1)
        self.assertEqual(model.training_samples, before + 1)

    def test_signal_has_entry_and_expiry_even_when_no_signal(self) -> None:
        signal, model, _ = PredictionEngine().generate_signal(
            self.candles, "EUR/USD", "ml_consensus", "SYNTHETIC DEMO", horizon=1
        )
        self.assertIn(signal.side, {"BUY", "SELL", "NO SIGNAL"})
        self.assertGreater(signal.entry_price, 0)
        self.assertEqual(signal.expires_at - signal.generated_at, timedelta(minutes=1))
        payload = signal.to_dict()
        self.assertEqual(payload["entry_time"], payload["generated_at"])
        self.assertEqual(payload["prediction_time"], payload["expires_at"])
        self.assertFalse(payload["is_live"])
        self.assertEqual(payload["horizon"], 1)
        self.assertIsNotNone(model)

    def test_walk_forward_backtest_returns_metrics(self) -> None:
        result = run_walk_forward_backtest(self.candles, strategy_id="trend_pullback", payout=0.8)
        self.assertEqual(result["strategy"], "trend_pullback")
        self.assertGreater(result["test_candles"], 0)
        self.assertEqual(result["signals"], result["wins"] + result["losses"] + result["ties"])
        self.assertEqual(result["assumed_payout"], 0.8)
        self.assertEqual(result["accuracy_target"], 0.95)
        self.assertEqual(result["target_window"], 20)
        self.assertIn("last_20_decisive", result)
        if result["decisive_trades"]:
            self.assertLessEqual(result["win_rate_ci95"]["lower"], result["win_rate"])
            self.assertLessEqual(result["win_rate"], result["win_rate_ci95"]["upper"])

    def test_strategy_catalog_has_30_candidates(self) -> None:
        self.assertEqual(len(STRATEGY_CATALOG), 30)
        payload = strategy_catalog_payload()
        self.assertEqual(len(payload["candidates"]), 30)
        self.assertEqual(len(payload["executable"]), 4)
        statuses = {item["name"]: item["status"] for item in payload["candidates"]}
        self.assertEqual(statuses["Trend-filtered RSI pullback"], "prototype approximation")
        self.assertEqual(statuses["Bollinger bounce"], "prototype approximation")
        self.assertEqual(statuses["Breakout and retest"], "research")


if __name__ == "__main__":
    unittest.main()
