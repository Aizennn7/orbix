from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from orbix.domain import Candle
from orbix.study import (
    StudyConfig,
    TradeStats,
    build_verdict,
    load_candles_csv,
    run_study,
)


UTC = timezone.utc


def write_candles(path: Path, count: int, *, header: bool, separator: str = ",") -> None:
    price = 1.1000
    lines = []
    if header:
        lines.append(separator.join(["timestamp", "open", "high", "low", "close", "volume"]))
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for index in range(count):
        opening = price
        close = price * (1 + 0.00004 * math.sin(index / 9) + 0.00001)
        high = max(opening, close) + 0.00002
        low = min(opening, close) - 0.00002
        stamp = start + timedelta(minutes=index)
        if header:
            value = stamp.isoformat().replace("+00:00", "Z")
        else:
            value = stamp.strftime("%Y%m%d %H%M%S")
        lines.append(separator.join([
            value, f"{opening:.6f}", f"{high:.6f}", f"{low:.6f}", f"{close:.6f}", "0",
        ]))
        price = close
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def synthetic_candles(count: int) -> list[Candle]:
    candles = []
    price = 1.1000
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for index in range(count):
        opening = price
        move = 0.00006 * math.sin(index / 11.0) + 0.00002 * math.sin(index / 3.1)
        close = max(1.0, opening + move)
        high = max(opening, close) + 0.00003
        low = min(opening, close) - 0.00003
        candles.append(Candle(start + timedelta(minutes=index), opening, high, low, close, 0.0))
        price = close
    return candles


class CsvLoaderTests(unittest.TestCase):
    def test_reads_a_headed_iso_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            write_candles(path, 130, header=True)
            candles = load_candles_csv(path)
        self.assertEqual(len(candles), 130)
        self.assertEqual(candles[0].timestamp, datetime(2024, 1, 1, tzinfo=UTC))
        self.assertAlmostEqual(candles[0].open, 1.1, places=6)

    def test_reads_headerless_hisdata_rows_and_applies_the_archive_offset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histdata.csv"
            write_candles(path, 130, header=False, separator=";")
            candles = load_candles_csv(path, tz_offset_hours=5)
        self.assertEqual(candles[0].timestamp, datetime(2024, 1, 1, 5, 0, tzinfo=UTC))
        self.assertEqual(len(candles), 130)

    def test_sorts_and_drops_duplicate_timestamps(self) -> None:
        start = datetime(2024, 1, 1, 17, 0, tzinfo=UTC)
        rows = []
        for index in range(130):
            stamp = (start + timedelta(seconds=index)).strftime("%Y%m%d %H%M%S")
            rows.append(f"{stamp};1.1;1.2;1.0;1.15;0")
        rows.reverse()
        rows.append("20240101 170000;1.1;1.2;1.0;1.16;0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dupes.csv"
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            candles = load_candles_csv(path, tz_offset_hours=5)
        stamps = [candle.timestamp for candle in candles]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(stamps), len(set(stamps)))
        self.assertEqual(len(stamps), 130)

    def test_rejects_too_few_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.csv"
            write_candles(path, 12, header=True)
            with self.assertRaisesRegex(ValueError, "120 candles"):
                load_candles_csv(path)


class TradeStatsTests(unittest.TestCase):
    def test_counts_costs_and_ties_without_hiding_the_spread(self) -> None:
        stats = TradeStats(payout=0.80, spread_pips=1.0)
        moment = datetime(2024, 1, 1, tzinfo=UTC)
        stats.add("BUY", 1.1000, 1.1002, timestamp=moment, session="Asia", volatility="low")
        stats.add("SELL", 1.1000, 1.1002, timestamp=moment, session="Asia", volatility="low")
        stats.add("BUY", 1.1000, 1.1000, timestamp=moment, session="Asia", volatility="low")
        result = stats.as_dict()
        self.assertEqual(result["signals"], 3)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["decisive_trades"], 2)
        self.assertAlmostEqual(result["win_rate"], 0.5)
        self.assertAlmostEqual(result["net_units"], -0.2)
        self.assertAlmostEqual(result["gross_pips_mean"], 0.0)
        self.assertAlmostEqual(result["net_pips_mean"], -1.0)
        self.assertAlmostEqual(result["break_even_win_rate"], 1 / 1.8)
        self.assertFalse(result["has_edge_after_costs"])
        self.assertIsNotNone(result["win_rate_ci95"])
        self.assertEqual(set(stats.segments()), {"months", "sessions", "volatility"})
        self.assertEqual(stats.segments()["sessions"]["Asia"]["signals"], 3)

    def test_verdict_refuses_to_call_an_incomplete_sample_tradeable(self) -> None:
        empty = {
            "signals": 0, "wins": 0, "losses": 0, "ties": 0,
            "decisive_trades": 0, "win_rate": None, "win_rate_ci95": None,
            "break_even_win_rate": 5 / 9, "net_units": 0.0,
            "expected_value_per_trade": None, "gross_pips_mean": None,
            "net_pips_mean": None, "net_pips_t_stat": None,
            "spread_pips": 1.0, "has_edge_after_costs": False,
        }
        result = {
            "variants": {
                name: dict(empty) for name in (
                    "model_gated", "model_raw", "always_up", "momentum_1",
                    "momentum_5", "rsi_reversal", "rule_trend_pullback", "random_coin",
                )
            }
        }
        verdict = build_verdict(result, StudyConfig())
        self.assertFalse(verdict["tradeable"])
        self.assertIn("No tradeable edge", verdict["headline"])
        self.assertTrue(verdict["reasons"])


class StudyRunTests(unittest.TestCase):
    def test_a_short_series_produces_every_variant_and_a_guarded_verdict(self) -> None:
        candles = synthetic_candles(2_600)
        config = StudyConfig(
            horizon=3,
            warmup_bars=700,
            retrain_every=800,
            train_window=900,
            calibrate_every=60,
            payout=0.80,
            spread_pips=1.0,
            ensemble=False,
            min_trades_for_verdict=200,
        )
        result = run_study(candles, config)
        self.assertEqual(result["data"]["scored_bars"], len(candles) - 700 - 3)
        self.assertEqual(result["gates"]["bars"], result["data"]["scored_bars"])
        self.assertGreaterEqual(result["model"]["refits"], 1)
        for name in (
            "model_gated", "model_raw", "model_raw_pure", "always_up",
            "momentum_1", "momentum_5", "rsi_reversal", "rule_trend_pullback", "random_coin",
        ):
            self.assertIn(name, result["variants"])
        self.assertEqual(
            result["variants"]["always_up"]["signals"], result["gates"]["bars"]
        )
        self.assertIsInstance(result["verdict"]["headline"], str)
        self.assertIsInstance(result["verdict"]["tradeable"], bool)
        json.dumps(result)  # every value must be serialisable
        self.assertEqual(result["config"]["horizon"], 3)


if __name__ == "__main__":
    unittest.main()
