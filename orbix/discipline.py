"""Human-style trading discipline for the automatic signal path.

The model learns from every completed bar. This module is deliberately
different: it governs what the system would *do*, the way a disciplined person
trades. It wakes for a couple of liquid windows instead of staring at every
minute, takes a small number of setups, keeps one trade at a time, cools off
after consecutive losses, stops for the day after the loss limit, and remembers
contexts that have burned it.

These rules can only reduce activity and cap risk. They cannot create an edge,
and the counts here are not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import os
from typing import Any, TYPE_CHECKING

from .abstentions import session_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ledger import PaperLedger

UTC = timezone.utc

DEFAULT_SESSIONS = "07:00-11:00,12:30-16:30"
ALL_DAY = "all"

GRADE_NOTE = (
    "Conviction is a label for distance from a coin flip plus ensemble agreement. "
    "It is not a probability of profit."
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(float(raw.strip()))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _clock_label(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_window_minutes(text: str) -> int:
    text = text.strip()
    if ":" not in text:
        raise ValueError(f"Use HH:MM, not '{text}'")
    hour_text, minute_text = text.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 24 and 0 <= minute < 60) or (hour == 24 and minute):
        raise ValueError(f"Invalid clock time '{text}'")
    return hour * 60 + minute


def parse_sessions(text: str) -> tuple[tuple[int, int], ...]:
    """Parse ``07:00-11:00,12:30-16:30`` into minute windows.

    ``all`` (or an empty string) means every hour is allowed. Raises ValueError
    for malformed input so the caller can fall back and report the problem.
    """
    cleaned = (text or "").strip()
    if not cleaned or cleaned.lower() in {ALL_DAY, "24h", "24/7", "0-24"}:
        return ()
    windows: list[tuple[int, int]] = []
    for part in cleaned.split(","):
        if "-" not in part:
            raise ValueError(f"Session window '{part.strip()}' needs a dash")
        start_text, end_text = part.split("-", 1)
        start = _parse_window_minutes(start_text)
        end = _parse_window_minutes(end_text)
        if start == end or start >= 24 * 60 or end > 24 * 60:
            raise ValueError(f"Session window '{part.strip()}' is empty or out of range")
        windows.append((start, end))
    if not windows:
        raise ValueError("No trading windows were configured")
    return tuple(windows)


@dataclass(frozen=True)
class DisciplineSettings:
    enabled: bool = True
    sessions: tuple[tuple[int, int], ...] = ((7 * 60, 11 * 60), (12 * 60 + 30, 16 * 60 + 30))
    sessions_text: str = DEFAULT_SESSIONS
    sessions_error: str | None = None
    weekdays_only: bool = True
    open_quiet_minutes: int = 10
    max_signals_per_day: int = 6
    min_spacing_minutes: int = 20
    max_daily_losses: int = 3
    cooldown_after_losses: int = 2
    cooldown_minutes: int = 30
    context_min_samples: int = 20
    context_min_win_rate: float = 0.45
    max_open_trades: int = 1

    @classmethod
    def from_env(cls) -> "DisciplineSettings":
        text = os.getenv("ORBIX_TRADING_SESSIONS", DEFAULT_SESSIONS).strip()
        error: str | None = None
        try:
            windows = parse_sessions(text)
        except ValueError as exc:
            error = f"{exc}; using {DEFAULT_SESSIONS} UTC."
            text = DEFAULT_SESSIONS
            windows = parse_sessions(text)
        return cls(
            enabled=_env_bool("ORBIX_DISCIPLINE", True),
            sessions=windows,
            sessions_text=text if windows else ALL_DAY,
            sessions_error=error,
            weekdays_only=_env_bool("ORBIX_WEEKDAYS_ONLY", True),
            open_quiet_minutes=_env_int("ORBIX_SESSION_QUIET_MINUTES", 10, 0, 180),
            max_signals_per_day=_env_int("ORBIX_MAX_SIGNALS_PER_DAY", 6, 1, 100),
            min_spacing_minutes=_env_int("ORBIX_MIN_SIGNAL_SPACING_MINUTES", 20, 1, 1440),
            max_daily_losses=_env_int("ORBIX_MAX_DAILY_LOSSES", 3, 1, 100),
            cooldown_after_losses=_env_int("ORBIX_COOLDOWN_AFTER_LOSSES", 2, 1, 20),
            cooldown_minutes=_env_int("ORBIX_COOLDOWN_MINUTES", 30, 1, 1440),
            context_min_samples=_env_int("ORBIX_CONTEXT_MIN_SAMPLES", 20, 5, 500),
            context_min_win_rate=_env_float("ORBIX_CONTEXT_MIN_WIN_RATE", 0.45, 0.0, 0.9),
            max_open_trades=_env_int("ORBIX_MAX_OPEN_TRADES", 1, 1, 20),
        )

    def windows_label(self) -> str:
        if not self.sessions:
            return "Every hour"
        return ", ".join(
            f"{_clock_label(start)}-{_clock_label(end)}" for start, end in self.sessions
        )


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    code: str
    note: str
    session: str
    trades_today: int = 0
    losses_today: int = 0
    streak: int = 0
    cooldown_until: str | None = None
    context: dict[str, Any] | None = None
    journal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "note": self.note,
            "session": self.session,
            "trades_today": self.trades_today,
            "losses_today": self.losses_today,
            "streak": self.streak,
            "cooldown_until": self.cooldown_until,
            "context": self.context,
            "journal": self.journal,
        }


class TraderDiscipline:
    """Session clock, risk budget, cool-off, and context memory."""

    def __init__(self, ledger: "PaperLedger", settings: DisciplineSettings | None = None) -> None:
        self.ledger = ledger
        self.settings = settings or DisciplineSettings.from_env()

    # ------------------------------------------------------------------ clock
    def window_at(self, moment: datetime) -> tuple[int, int] | None:
        if not self.settings.sessions:
            return (0, 24 * 60)
        minute = moment.hour * 60 + moment.minute
        for start, end in self.settings.sessions:
            if start < end:
                if start <= minute < end:
                    return (start, end)
            elif minute >= start or minute < end:
                return (start, end)
        return None

    def market_open(self, moment: datetime) -> bool:
        if self.settings.weekdays_only and moment.weekday() >= 5:
            return False
        return self.window_at(moment) is not None

    def next_open(self, moment: datetime) -> datetime | None:
        if not self.settings.enabled:
            return None
        for offset in range(0, 8):
            day = (moment + timedelta(days=offset)).date()
            if self.settings.weekdays_only and day.weekday() >= 5:
                continue
            starts = [start for start, _ in self.settings.sessions] or [0]
            for start in starts:
                candidate = datetime.combine(day, time(0, 0), UTC) + timedelta(minutes=start)
                if candidate > moment:
                    return candidate
        return None

    # ---------------------------------------------------------------- verdicts
    def assess(
        self,
        *,
        pair: str,
        strategy: str,
        entry_time: datetime | str,
        side: str | None = None,
    ) -> Verdict:
        """Decide whether a disciplined trader would take this minute."""
        settings = self.settings
        moment = _as_utc(entry_time)
        session = session_for(moment)
        if not settings.enabled:
            return Verdict(True, "off", "Discipline rules are off.", session)

        if settings.weekdays_only and moment.weekday() >= 5:
            return Verdict(
                False,
                "market_closed",
                "Passing - the market is closed for the weekend.",
                session,
            )
        window = self.window_at(moment)
        if window is None:
            upcoming = self.next_open(moment)
            when = _when_label(upcoming) if upcoming else "the next weekday"
            return Verdict(
                False,
                "outside_session",
                f"Passing - outside my trading session. The next window opens {when} UTC.",
                session,
            )
        start, _end = window
        minutes_into_window = (moment.hour * 60 + moment.minute - start) % (24 * 60)
        if settings.sessions and settings.open_quiet_minutes and minutes_into_window < settings.open_quiet_minutes:
            return Verdict(
                False,
                "session_quiet",
                (
                    "Passing - the session just opened. Letting the first "
                    f"{settings.open_quiet_minutes} minutes settle before acting."
                ),
                session,
            )

        day_start = datetime.combine(moment.date(), time(0, 0), UTC)
        day_end = day_start + timedelta(days=1)
        day = self.ledger.day_signals(pair, day_start, day_end)
        trades_today = len(day)
        losses_today = sum(1 for row in day if row.get("outcome") == "LOSS")
        last_entry = _last_entry(day)
        pending = self.ledger.pending_count(pair)

        open_trades = sum(
            1 for row in day if str(row.get("status") or "").upper() == "PENDING"
        )
        if pending and open_trades >= settings.max_open_trades:
            return Verdict(
                False,
                "position_open",
                "Passing - one trade at a time. Waiting for the open paper trade to settle.",
                session,
                trades_today=trades_today,
                losses_today=losses_today,
            )

        if trades_today >= settings.max_signals_per_day:
            return Verdict(
                False,
                "daily_trade_limit",
                (
                    f"Passing - my {settings.max_signals_per_day}-trade daily budget is used up. "
                    "Tomorrow is another session."
                ),
                session,
                trades_today=trades_today,
                losses_today=losses_today,
                journal=True,
            )

        if last_entry is not None:
            gap = (moment - last_entry).total_seconds() / 60.0
            if gap < settings.min_spacing_minutes:
                return Verdict(
                    False,
                    "trade_spacing",
                    (
                        "Passing - only "
                        f"{max(0, int(gap))} minutes since my last entry "
                        f"(minimum {settings.min_spacing_minutes})."
                    ),
                    session,
                    trades_today=trades_today,
                    losses_today=losses_today,
                )

        if losses_today >= settings.max_daily_losses:
            return Verdict(
                False,
                "daily_loss_limit",
                (
                    f"Passing - daily loss limit of {settings.max_daily_losses} reached. "
                    "I am done for the day; no revenge trades."
                ),
                session,
                trades_today=trades_today,
                losses_today=losses_today,
                journal=True,
            )

        streak, last_loss_at = self._streak(pair)
        cooldown_until = None
        if (
            streak <= -settings.cooldown_after_losses
            and last_loss_at is not None
            and moment - last_loss_at < timedelta(minutes=settings.cooldown_minutes)
        ):
            cooldown_until = last_loss_at + timedelta(minutes=settings.cooldown_minutes)
            return Verdict(
                False,
                "loss_cooldown",
                (
                    f"Passing - {-streak} losses in a row. Cooling off until "
                    f"{cooldown_until.strftime('%H:%M')} UTC."
                ),
                session,
                trades_today=trades_today,
                losses_today=losses_today,
                streak=streak,
                cooldown_until=cooldown_until.isoformat(),
                journal=True,
            )

        context = self.context_for(pair, strategy, session)
        if (
            context
            and context["total"] >= settings.context_min_samples
            and context["win_rate"] < settings.context_min_win_rate
        ):
            return Verdict(
                False,
                "context_veto",
                (
                    "Passing - my journal has "
                    f"{context['strategy']} at {context['win_rate'] * 100:.0f}% over "
                    f"{context['total']} {session} trades. I stopped repeating that mistake."
                ),
                session,
                trades_today=trades_today,
                losses_today=losses_today,
                streak=streak,
                context=context,
                journal=True,
            )

        budget_left = settings.max_signals_per_day - trades_today
        note = (
            f"Trading the {session} window. Setup {trades_today + 1} of "
            f"{settings.max_signals_per_day} today, {budget_left} left after this one."
        )
        if context and context["total"] >= settings.context_min_samples:
            note += (
                f" Same context: {context['win_rate'] * 100:.0f}% over "
                f"{context['total']} trades."
            )
        elif not context or context["total"] < settings.context_min_samples:
            note += " This context has little journal history yet."
        if streak < 0:
            note += f" Coming back after {-streak} loss(es)."
        return Verdict(
            True,
            "allowed",
            note,
            session,
            trades_today=trades_today,
            losses_today=losses_today,
            streak=streak,
            context=context,
        )

    # ------------------------------------------------------------- memory/grade
    def _streak(self, pair: str) -> tuple[int, datetime | None]:
        rows = self.ledger.recent_results(pair, limit=12)
        streak = 0
        last_loss_at: datetime | None = None
        for row in reversed(rows):
            outcome = row.get("outcome")
            if outcome == "WIN":
                if streak < 0:
                    break
                streak += 1
            elif outcome == "LOSS":
                if streak > 0:
                    break
                if last_loss_at is None:
                    last_loss_at = _as_utc(row["entry_time"])
                streak -= 1
        return streak, last_loss_at

    def context_for(self, pair: str, strategy: str, session: str) -> dict[str, Any] | None:
        rows = self.ledger.context_rows(pair)
        wins = total = 0
        for row in rows:
            if row.get("strategy") != strategy:
                continue
            if session_for(row.get("entry_time")) != session:
                continue
            total += 1
            if row.get("outcome") == "WIN":
                wins += 1
        if not total:
            return None
        return {
            "strategy": strategy,
            "session": session,
            "total": total,
            "wins": wins,
            "win_rate": wins / total,
            "cautious": total >= self.settings.context_min_samples
            and wins / total < self.settings.context_min_win_rate,
        }

    def contexts(self, pair: str) -> list[dict[str, Any]]:
        rows = self.ledger.context_rows(pair)
        buckets: dict[tuple[str, str], dict[str, int]] = {}
        for row in rows:
            session = session_for(row.get("entry_time"))
            bucket = buckets.setdefault((row.get("strategy") or "", session), {"wins": 0, "total": 0})
            bucket["total"] += 1
            if row.get("outcome") == "WIN":
                bucket["wins"] += 1
        result = []
        for (strategy, session), bucket in buckets.items():
            total = bucket["total"]
            rate = bucket["wins"] / total if total else 0.0
            result.append(
                {
                    "strategy": strategy,
                    "session": session,
                    "total": total,
                    "wins": bucket["wins"],
                    "win_rate": rate,
                    "cautious": total >= self.settings.context_min_samples
                    and rate < self.settings.context_min_win_rate,
                    "note": (
                        "Too few trades to judge yet."
                        if total < self.settings.context_min_samples
                        else (
                            "My journal says to avoid this context."
                            if rate < self.settings.context_min_win_rate
                            else "No reason to avoid it; no reason to expect an edge either."
                        )
                    ),
                }
            )
        result.sort(key=lambda item: (-item["total"], item["strategy"], item["session"]))
        return result

    def grade(self, probability: float | None, uncertainty: float | None) -> dict[str, Any]:
        if probability is None:
            return {"grade": "-", "score": None, "label": "no model estimate"}
        distance = abs(float(probability) - 0.5)
        score = min(1.0, distance / 0.12)
        if uncertainty is not None:
            agreement = max(0.0, 1.0 - min(1.0, float(uncertainty) / 0.10))
            score = 0.65 * score + 0.35 * agreement
        grade = "A" if score >= 0.70 else "B" if score >= 0.45 else "C"
        label = {"A": "clear setup", "B": "ordinary setup", "C": "marginal setup"}[grade]
        return {"grade": grade, "score": round(score, 3), "label": label}

    def describe(self, signal: dict[str, Any], verdict: Verdict) -> dict[str, Any]:
        grade = self.grade(signal.get("probability_up"), signal.get("uncertainty"))
        probability = signal.get("probability_up")
        parts = [
            f"Took {signal.get('side')}",
            verdict.session,
            (
                f"setup {verdict.trades_today + 1}/{self.settings.max_signals_per_day} today"
            ),
        ]
        if probability is not None:
            parts.append(f"{float(probability) * 100:.0f}% up")
        parts.append(f"grade {grade['grade']}")
        if verdict.context and verdict.context["total"] >= self.settings.context_min_samples:
            parts.append(
                f"journal {verdict.context['win_rate'] * 100:.0f}% over "
                f"{verdict.context['total']}"
            )
        note = " - ".join(parts) + "."
        return {
            "note": note,
            "grade": grade["grade"],
            "conviction": grade["score"],
            "conviction_label": grade["label"],
            "session": verdict.session,
            "context": verdict.context,
            "budget_used": verdict.trades_today + 1,
            "budget_max": self.settings.max_signals_per_day,
            "streak": verdict.streak,
        }

    # ------------------------------------------------------------------ status
    def status(
        self,
        *,
        pair: str,
        reference: datetime | None = None,
        apply_rules: bool = True,
    ) -> dict[str, Any]:
        settings = self.settings
        moment = _as_utc(reference or datetime.now(UTC))
        window = self.window_at(moment)
        awake = bool(
            settings.enabled and apply_rules and self.market_open(moment) and window is not None
        )
        day_start = datetime.combine(moment.date(), time(0, 0), UTC)
        day = self.ledger.day_signals(pair, day_start, day_start + timedelta(days=1))
        losses_today = sum(1 for row in day if row.get("outcome") == "LOSS")
        streak, last_loss_at = self._streak(pair)
        upcoming = None if awake else self.next_open(moment)
        cooldown_until = None
        if (
            streak <= -settings.cooldown_after_losses
            and last_loss_at is not None
            and moment - last_loss_at < timedelta(minutes=settings.cooldown_minutes)
        ):
            cooldown_until = (
                last_loss_at + timedelta(minutes=settings.cooldown_minutes)
            ).isoformat()
        last_entry = _last_entry(day)
        spacing_until = (
            last_entry + timedelta(minutes=settings.min_spacing_minutes)
            if last_entry is not None
            else None
        )

        if not apply_rules:
            note = "Synthetic demo or no real dataset. Rules apply once real candles are loaded."
        elif not settings.enabled:
            note = "Discipline rules are switched off for this deployment."
        elif not awake and settings.weekdays_only and moment.weekday() >= 5:
            note = f"Market closed for the weekend. Next window {_when_label(upcoming)} UTC."
        elif not awake:
            note = f"Asleep. Next window opens {_when_label(upcoming)} UTC."
        elif losses_today >= settings.max_daily_losses:
            note = "Stopped for the day: the daily loss limit was reached."
        elif cooldown_until:
            note = f"Cooling off after {abs(streak)} losses until {cooldown_until[11:16]} UTC."
        elif len(day) >= settings.max_signals_per_day:
            note = "Daily trade budget used up; done for the day."
        elif spacing_until is not None and spacing_until > moment:
            note = f"Awake and patient. Next entry allowed after {spacing_until.strftime('%H:%M')} UTC."
        else:
            note = "Awake and watching for the next completed bar."

        return {
            "enabled": settings.enabled,
            "apply_rules": apply_rules,
            "awake": awake,
            "session": session_for(moment) if window is not None else "Outside session",
            "window": (
                f"{_clock_label(window[0])}-{_clock_label(window[1])}"
                if window is not None
                else None
            ),
            "sessions": settings.windows_label(),
            "timezone": "UTC",
            "weekdays_only": settings.weekdays_only,
            "reference": moment.isoformat(),
            "next_open": upcoming.isoformat() if upcoming else None,
            "note": note,
            "budget": {
                "max": settings.max_signals_per_day,
                "used": len(day),
                "remaining": max(0, settings.max_signals_per_day - len(day)),
            },
            "losses_today": losses_today,
            "max_daily_losses": settings.max_daily_losses,
            "streak": streak,
            "cooldown_until": cooldown_until,
            "spacing_until": spacing_until.isoformat() if spacing_until else None,
            "rules": {
                "min_spacing_minutes": settings.min_spacing_minutes,
                "open_quiet_minutes": settings.open_quiet_minutes,
                "cooldown_after_losses": settings.cooldown_after_losses,
                "cooldown_minutes": settings.cooldown_minutes,
                "context_min_samples": settings.context_min_samples,
                "context_min_win_rate": settings.context_min_win_rate,
                "max_open_trades": settings.max_open_trades,
            },
            "config_error": settings.sessions_error,
            "grade_note": GRADE_NOTE,
        }

    # ------------------------------------------------------------------ reviews
    def review(
        self,
        *,
        pair: str,
        day: date,
    ) -> dict[str, Any]:
        start = datetime.combine(day, time(0, 0), UTC)
        rows = self.ledger.day_signals(pair, start, start + timedelta(days=1))
        resolved = [row for row in rows if row.get("outcome") in {"WIN", "LOSS", "TIE"}]
        wins = sum(1 for row in resolved if row["outcome"] == "WIN")
        losses = sum(1 for row in resolved if row["outcome"] == "LOSS")
        ties = sum(1 for row in resolved if row["outcome"] == "TIE")
        decided = wins + losses
        net_pips = 0.0
        for row in resolved:
            if row.get("exit_price") in (None, "") or row.get("entry_price") in (None, ""):
                continue
            direction = 1.0 if row.get("side") == "BUY" else -1.0
            pip = 0.01 if str(pair).upper().endswith("JPY") else 0.0001
            net_pips += (float(row["exit_price"]) - float(row["entry_price"])) / pip * direction
        by_session: dict[str, dict[str, int]] = {}
        for row in resolved:
            session = session_for(row.get("entry_time"))
            bucket = by_session.setdefault(session, {"trades": 0, "wins": 0, "losses": 0, "ties": 0})
            bucket["trades"] += 1
            bucket["wins" if row["outcome"] == "WIN" else "losses" if row["outcome"] == "LOSS" else "ties"] += 1
        sessions = [
            {"session": session, **bucket, "win_rate": (bucket["wins"] / bucket["trades"] if bucket["trades"] else None)}
            for session, bucket in by_session.items()
        ]
        sessions.sort(key=lambda item: -item["trades"])

        if not rows:
            summary = "No trades. Waiting is a decision too."
            lesson = "No lessons from an empty day; the budget and session rules were not tested."
        else:
            summary = (
                f"{len(rows)} trade(s): {wins} win(s), {losses} loss(es)"
                + (f", {ties} tie(s)" if ties else "")
                + f". Net {net_pips:+.1f} pips."
            )
            if sessions:
                summary += f" Most active window: {sessions[0]['session']}."
            if losses >= self.settings.max_daily_losses:
                summary += " The daily loss limit stopped the day."
            lesson = self._lesson(resolved)
        return {
            "date": day.isoformat(),
            "trades": len(rows),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "accuracy": (wins / decided) if decided else None,
            "net_pips": round(net_pips, 2),
            "by_session": sessions,
            "summary": summary,
            "lesson": lesson,
        }

    def _lesson(self, resolved: list[dict[str, Any]]) -> str:
        if not resolved:
            return "Trades are still open; the review will fill in after they resolve."
        losses = [row for row in resolved if row.get("outcome") == "LOSS"]
        wins = [row for row in resolved if row.get("outcome") == "WIN"]
        if len(losses) >= 2 and len(wins) == 0:
            return "Every trade lost. The cool-off rule should have limited the damage; check the entries."
        if len(losses) > len(wins):
            return "More losses than wins. This is a small sample, not a verdict; keep the budget small."
        if len(wins) > len(losses):
            return "More wins than losses. That is still not an edge; the payout hurdle is above 50%."
        return "Even split. No conclusion from a handful of trades."

    def reviews(self, *, pair: str, days: int = 7, reference: datetime | None = None) -> list[dict[str, Any]]:
        moment = _as_utc(reference or datetime.now(UTC))
        count = max(1, min(30, int(days)))
        return [
            self.review(pair=pair, day=moment.date() - timedelta(days=offset))
            for offset in range(count - 1, -1, -1)
        ]


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            moment = datetime.now(UTC)
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _last_entry(rows: list[dict[str, Any]]) -> datetime | None:
    times = []
    for row in rows:
        value = row.get("entry_time")
        if not value:
            continue
        try:
            times.append(_as_utc(value))
        except ValueError:
            continue
    return max(times) if times else None


def _when_label(moment: datetime | None) -> str:
    if moment is None:
        return "the next weekday"
    now = datetime.now(UTC)
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if (moment.date() - now.date()).days == 1:
        return f"tomorrow {moment.strftime('%H:%M')}"
    return f"{moment.strftime('%a')} {moment.strftime('%H:%M')}"
