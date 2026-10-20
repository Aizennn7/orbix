from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orbix.domain import Signal
from orbix.ledger import PaperLedger


class PaperLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.ledger = PaperLedger(Path(self.directory.name) / "paper.sqlite3")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def make_signal(self, source: str, expired: bool = True) -> Signal:
        now = datetime.now(timezone.utc)
        generated = now - timedelta(minutes=3) if expired else now
        return Signal(
            signal_id=f"signal-{source}-{expired}",
            pair="EUR/USD",
            strategy="ml_consensus",
            side="BUY",
            entry_price=1.1,
            generated_at=generated,
            expires_at=generated + timedelta(minutes=1),
            probability_up=0.7,
            source=source,
            reason="test signal",
        )

    def test_imported_paper_outcome_records_dataset_and_label(self) -> None:
        signal = self.make_signal("IMPORTED CSV")
        self.ledger.record(signal, {"return_1": 0.01}, dataset_id="dataset-abc")
        saved = self.ledger.get(signal.signal_id)
        self.assertEqual(saved["dataset_id"], "dataset-abc")

        result = self.ledger.resolve(signal.signal_id, 1.11)
        self.assertEqual(result["outcome"], "WIN")
        self.assertEqual(result["direction_label"], 1)
        self.assertEqual(result["features"], {"return_1": 0.01})

    def test_demo_outcome_is_never_accepted_for_learning(self) -> None:
        signal = self.make_signal("SYNTHETIC DEMO")
        self.ledger.record(signal, {}, dataset_id="demo")
        with self.assertRaisesRegex(ValueError, "Synthetic demo"):
            self.ledger.resolve(signal.signal_id, 1.11)

    def test_signal_must_reach_expiry_before_manual_resolution(self) -> None:
        signal = self.make_signal("IMPORTED CSV", expired=False)
        self.ledger.record(signal, {}, dataset_id="dataset-live")
        with self.assertRaisesRegex(ValueError, "expiry"):
            self.ledger.resolve(signal.signal_id, 1.11)


if __name__ == "__main__":
    unittest.main()
