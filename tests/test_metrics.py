from __future__ import annotations

import unittest

from orbix.metrics import wilson_interval


class WilsonIntervalTests(unittest.TestCase):
    def test_no_interval_without_decisions(self) -> None:
        self.assertIsNone(wilson_interval(0, 0))

    def test_nineteen_of_twenty_is_not_a_narrow_95_percent_claim(self) -> None:
        interval = wilson_interval(19, 20)
        self.assertIsNotNone(interval)
        self.assertLess(interval["lower"], 0.80)
        self.assertGreater(interval["upper"], 0.95)
        self.assertLessEqual(interval["lower"], 0.95)
        self.assertGreaterEqual(interval["upper"], 0.95)

    def test_invalid_counts_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            wilson_interval(21, 20)


if __name__ == "__main__":
    unittest.main()
