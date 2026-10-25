"""Test the mobile SQLite DB - offline database."""
import tempfile
from pathlib import Path
import unittest

from orbix.mobile_db import MobileCandleStore, MobileLedger, get_mobile_store
from orbix.domain import Candle
from datetime import datetime, timezone, timedelta


class TestMobileDB(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.candle_store, self.ledger = get_mobile_store(self.tmp)

    def test_candle_store_init(self):
        self.assertTrue((self.tmp / "mobile_candles.db").exists())
        datasets = self.candle_store.list_datasets()
        self.assertEqual(len(datasets), 0)

    def test_append_and_get_candles(self):
        # Create dataset via import
        csv_path = self.tmp / "test.csv"
        csv_path.write_text("timestamp,open,high,low,close,volume\n2024-01-01T00:00:00Z,1.08,1.09,1.07,1.085,100\n2024-01-01T00:01:00Z,1.085,1.09,1.08,1.088,120\n")
        ds_id = self.candle_store.import_csv(csv_path, pair="EUR/USD")
        self.assertTrue(ds_id.startswith("mobile-"))
        
        candles = self.candle_store.get_candles(ds_id, limit=10)
        self.assertEqual(len(candles), 2)
        self.assertAlmostEqual(candles[0].close, 1.085, places=3)

        # Append one more
        new_candle = Candle(
            timestamp=datetime.now(timezone.utc),
            open=1.088, high=1.09, low=1.087, close=1.089, volume=110
        )
        ok = self.candle_store.append_candle(ds_id, new_candle)
        self.assertTrue(ok)
        candles = self.candle_store.get_candles(ds_id, limit=10)
        self.assertEqual(len(candles), 3)

    def test_ledger(self):
        self.assertTrue((self.tmp / "mobile_ledger.db").exists())
        signal = {
            "signal_id": "test-123",
            "pair": "EUR/USD",
            "strategy": "orbix_confluence",
            "side": "BUY",
            "entry_price": 1.085,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            "probability_up": 0.56,
            "source": "mobile",
            "reason": "Test signal",
            "features": {"rsi": 55},
            "dataset_id": "test-ds"
        }
        self.ledger.save_signal(signal)
        signals = self.ledger.get_signals(limit=10)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["side"], "BUY")

    def test_settings(self):
        self.ledger.set_setting("test_key", {"foo": "bar"})
        val = self.ledger.get_setting("test_key")
        self.assertEqual(val["foo"], "bar")


if __name__ == "__main__":
    unittest.main()
