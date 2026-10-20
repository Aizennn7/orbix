from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from orbix.discipline import DisciplineSettings, TraderDiscipline, parse_sessions
from orbix.domain import Signal
from orbix.ledger import PaperLedger

UTC = timezone.utc
SETTINGS = DisciplineSettings(
    sessions=parse_sessions("07:00-11:00,12:30-16:30"),
    weekdays_only=True,
    open_quiet_minutes=10,
    max_signals_per_day=6,
    min_spacing_minutes=20,
    max_daily_losses=3,
    cooldown_after_losses=2,
    cooldown_minutes=30,
    context_min_samples=20,
    context_min_win_rate=0.45,
)


def add_signal(
    ledger: PaperLedger,
    when: datetime,
    *,
    side: str = "BUY",
    outcome: str = "WIN",
    pair: str = "EUR/USD",
    strategy: str = "orbix_confluence",
) -> str:
    entry = 1.1000
    move = 0.0003
    if outcome == "TIE":
        exit_price = entry
    elif side == "BUY":
        exit_price = entry + move if outcome == "WIN" else entry - move
    else:
        exit_price = entry - move if outcome == "WIN" else entry + move
    signal = Signal(
        signal_id=uuid4().hex,
        pair=pair,
        strategy=strategy,
        side=side,
        entry_price=entry,
        generated_at=when,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        probability_up=0.58,
        source="IMPORTED CSV",
        reason="test signal",
        entry_time=when,
    )
    ledger.record(signal, {})
    ledger.resolve(signal.signal_id, exit_price)
    return signal.signal_id


def next_weekday(moment: datetime) -> datetime:
    while moment.weekday() >= 5:
        moment += timedelta(days=1)
    return moment


class DisciplineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = PaperLedger(Path(self.temp.name) / "orbix.sqlite3")
        self.discipline = TraderDiscipline(self.ledger, SETTINGS)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_default_profile_is_a_three_minute_cadence(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = DisciplineSettings.from_env()
        self.assertEqual(settings.min_spacing_minutes, 3)
        self.assertEqual(settings.max_signals_per_day, 200)
        self.assertEqual(settings.max_daily_losses, 100)
        self.assertEqual(settings.cooldown_after_losses, 12)
        self.assertEqual(settings.context_min_samples, 200)
        self.assertAlmostEqual(settings.context_min_win_rate, 0.40)

    def test_asian_hour_is_outside_the_human_session(self) -> None:
        moment = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
        verdict = self.discipline.assess(
            pair="EUR/USD", strategy="orbix_confluence", entry_time=moment
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "outside_session")
        self.assertIn("outside my trading session", verdict.note)
        self.assertIn("07:00", verdict.note)

    def test_a_pending_paper_trade_in_another_dataset_does_not_block_the_live_one(self) -> None:
        moment = next_weekday(datetime(2026, 10, 21, 8, 0, tzinfo=UTC))
        signal = Signal(
            signal_id=uuid4().hex,
            pair="EUR/USD",
            strategy="orbix_confluence",
            side="BUY",
            entry_price=1.1,
            generated_at=moment,
            expires_at=moment + timedelta(minutes=1),
            probability_up=0.58,
            source="IMPORTED CSV",
            reason="pending paper trade",
            entry_time=moment,
        )
        self.ledger.record(signal, {}, dataset_id="historical-dataset")
        live = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=moment,
            dataset_id="live-dataset",
        )
        historical = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=moment,
            dataset_id="historical-dataset",
        )
        self.assertTrue(live.allowed, live.note)
        self.assertFalse(historical.allowed)
        self.assertEqual(historical.code, "position_open")

    def test_weekend_market_is_closed(self) -> None:
        moment = datetime(2026, 10, 24, 8, 30, tzinfo=UTC)  # Saturday
        verdict = self.discipline.assess(
            pair="EUR/USD", strategy="orbix_confluence", entry_time=moment
        )
        self.assertEqual(verdict.code, "market_closed")
        self.assertFalse(verdict.allowed)

    def test_the_first_minutes_of_a_session_are_left_to_settle(self) -> None:
        moment = datetime(2026, 10, 21, 7, 5, tzinfo=UTC)
        verdict = self.discipline.assess(
            pair="EUR/USD", strategy="orbix_confluence", entry_time=moment
        )
        self.assertEqual(verdict.code, "session_quiet")
        self.assertIn("session just opened", verdict.note)

    def test_daily_budget_stops_a_busy_day(self) -> None:
        start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        for index in range(6):
            add_signal(self.ledger, start + timedelta(minutes=25 * index))
        verdict = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=start + timedelta(minutes=150),
        )
        self.assertEqual(verdict.code, "daily_trade_limit")
        self.assertEqual(verdict.trades_today, 6)
        self.assertIn("daily budget", verdict.note)

    def test_two_entries_too_close_together_wait(self) -> None:
        start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        add_signal(self.ledger, start)
        verdict = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=start + timedelta(minutes=8),
        )
        self.assertEqual(verdict.code, "trade_spacing")
        self.assertIn("minutes since my last entry", verdict.note)

    def test_two_losses_start_a_cool_off(self) -> None:
        start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        add_signal(self.ledger, start, outcome="LOSS")
        add_signal(self.ledger, start + timedelta(minutes=30), outcome="LOSS")
        verdict = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=start + timedelta(minutes=55),
        )
        self.assertEqual(verdict.code, "loss_cooldown")
        self.assertEqual(verdict.streak, -2)
        self.assertIn("Cooling off until", verdict.note)

    def test_three_losses_end_the_day(self) -> None:
        start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        for index in range(3):
            add_signal(self.ledger, start + timedelta(minutes=40 * index), outcome="LOSS")
        verdict = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=start + timedelta(minutes=130),
        )
        self.assertEqual(verdict.code, "daily_loss_limit")
        self.assertEqual(verdict.losses_today, 3)
        self.assertIn("no revenge trades", verdict.note)

    def test_a_losing_context_is_remembered_across_days(self) -> None:
        moment = next_weekday(datetime(2026, 8, 31, 8, 30, tzinfo=UTC))
        for index in range(20):
            add_signal(
                self.ledger,
                moment,
                outcome="WIN" if index < 4 else "LOSS",
            )
            moment = next_weekday(moment + timedelta(days=1))
        verdict = self.discipline.assess(
            pair="EUR/USD",
            strategy="orbix_confluence",
            entry_time=moment,
        )
        self.assertEqual(verdict.code, "context_veto")
        self.assertTrue(verdict.journal)
        self.assertEqual(verdict.context["total"], 20)
        self.assertAlmostEqual(verdict.context["win_rate"], 0.2)
        self.assertIn("my journal has", verdict.note)

    def test_a_clean_setup_is_allowed_with_an_honest_note(self) -> None:
        moment = datetime(2026, 10, 21, 8, 30, tzinfo=UTC)
        verdict = self.discipline.assess(
            pair="EUR/USD", strategy="orbix_confluence", entry_time=moment
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.code, "allowed")
        self.assertIn("Trading the London window", verdict.note)
        trade = self.discipline.describe(
            {"side": "BUY", "probability_up": 0.60, "uncertainty": 0.02}, verdict
        )
        self.assertEqual(trade["grade"], "A")
        self.assertGreaterEqual(trade["conviction"], 0.7)
        self.assertIn("Took BUY", trade["note"])
        self.assertIn("grade A", trade["note"])
        self.assertIn("not a probability of profit", self.discipline.status(
            pair="EUR/USD", reference=moment
        )["grade_note"])

    def test_review_counts_trades_wins_losses_and_pips(self) -> None:
        day = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        add_signal(self.ledger, day, outcome="WIN")
        add_signal(self.ledger, day + timedelta(minutes=30), outcome="LOSS")
        add_signal(self.ledger, day + timedelta(minutes=60), outcome="TIE")
        review = self.discipline.review(pair="EUR/USD", day=day.date())
        self.assertEqual(review["trades"], 3)
        self.assertEqual(review["wins"], 1)
        self.assertEqual(review["losses"], 1)
        self.assertEqual(review["ties"], 1)
        self.assertEqual(review["accuracy"], 0.5)
        self.assertEqual(review["net_pips"], 0.0)
        self.assertIn("1 win(s)", review["summary"])
        self.assertEqual(review["by_session"][0]["session"], "London")

    def test_status_says_asleep_outside_the_window(self) -> None:
        moment = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
        status = self.discipline.status(pair="EUR/USD", reference=moment)
        self.assertFalse(status["awake"])
        self.assertIn("Asleep", status["note"])
        self.assertIsNotNone(status["next_open"])
        self.assertEqual(status["budget"]["remaining"], 6)

    def test_reviews_cover_the_requested_days(self) -> None:
        moment = datetime(2026, 10, 21, 8, 30, tzinfo=UTC)
        reviews = self.discipline.reviews(pair="EUR/USD", days=7, reference=moment)
        self.assertEqual(len(reviews), 7)
        self.assertEqual(reviews[-1]["date"], "2026-10-21")
        self.assertTrue(all(review["trades"] == 0 for review in reviews))


if __name__ == "__main__":
    unittest.main()
