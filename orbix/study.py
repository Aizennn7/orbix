"""Rolling walk-forward study with baselines, spread costs, and no look-ahead.

The study answers a narrow question: does the model read EUR/USD better than
trivial rules, after a realistic spread, on data it has never seen? It is a
research tool, not a performance claim. Every prediction uses only candles
whose close is already known, every label is used only after its expiry, and
the calibration map is refitted from resolved outcomes exactly the way the
runtime does it.

Two model views are reported:

``model_gated``
    What the product would do: calibrated probability, trend agreement,
    ensemble disagreement, and the flat-calibration abstention.
``model_raw``
    The counterfactual shadow read: the uncalibrated score with the same
    direction rules. It is never traded; it exists so a gate that blocks every
    signal can still be measured instead of hiding the evidence.

A separate ``model_raw_pure`` view drops the trend rules and trades the raw
score alone. The baselines (always-up, momentum, mean reversion, RSI, the
rule strategy, and a seeded coin flip) are evaluated on exactly the same bars
and horizon.

Costs are explicit. ``net_pips`` subtracts a spread from every direction bet.
The binary-option view also reports win rate against the break-even implied
by the assumed payout. If the evidence is weak, the verdict says so.

Run it with ``python -m orbix.study --csv data.csv --tz-offset 5``.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import sys
from typing import Iterable, Sequence

from .calibration import IsotonicCalibrator, fit_validated
from .domain import Candle, format_datetime
from .features import compute_features
from .metrics import wilson_interval
from .model import BootstrapEnsemble, OnlineLogisticModel
from .strategies import decide

UTC = timezone.utc

SESSIONS = (
    (0, 7, "Asia"),
    (7, 12, "London"),
    (12, 16, "London/New York overlap"),
    (16, 21, "New York"),
    (21, 24, "Late"),
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _parse_timestamp(text: str) -> datetime:
    text = text.strip()
    if not text:
        raise ValueError("Empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y%m%d %H%M%S", "%Y%m%d %H%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    raise ValueError(f"Unrecognised timestamp: {text!r}")


def load_candles_csv(
    path: str | Path,
    *,
    tz_offset_hours: float = 0.0,
    limit: int | None = None,
) -> list[Candle]:
    """Load M1 candles from a headed CSV or a header-less HistData export.

    HistData rows are ``YYYYMMDD HHMMSS;open;high;low;close;volume`` in a fixed
    exchange time (UTC-5, no daylight saving for these archives), so pass
    ``tz_offset_hours=5`` to convert them to UTC. Timestamps that already carry
    an offset are respected as-is.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    lines = [line for line in lines if line.strip()]
    if not lines:
        raise ValueError("CSV file is empty")
    delimiter = ";" if ";" in lines[0] else ","
    header = lines[0].split(delimiter)
    has_header = header[0].strip().lower() in {"timestamp", "datetime", "date", "time", "ts"}
    body = lines[1:] if has_header else lines
    if limit is not None:
        body = body[:limit]

    offset = timedelta(hours=tz_offset_hours)
    candles: list[Candle] = []
    for number, line in enumerate(body, start=2 if has_header else 1):
        parts = [part.strip() for part in line.split(delimiter)]
        if len(parts) < 5:
            raise ValueError(f"Line {number} has fewer than five columns")
        try:
            timestamp = _parse_timestamp(parts[0])
            if timestamp.tzinfo is None:
                # Archive timestamps are naive exchange time; convert once.
                timestamp = timestamp.replace(tzinfo=UTC) + offset
            volume = float(parts[5]) if len(parts) > 5 and parts[5] else 0.0
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(parts[1]),
                    high=float(parts[2]),
                    low=float(parts[3]),
                    close=float(parts[4]),
                    volume=volume,
                )
            )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Invalid candle on line {number}: {exc}") from exc

    candles.sort(key=lambda candle: candle.timestamp)
    deduped: list[Candle] = []
    for candle in candles:
        if deduped and candle.timestamp == deduped[-1].timestamp:
            continue
        deduped.append(candle)
    if len(deduped) < 120:
        raise ValueError("At least 120 candles are required for a study")
    return deduped



# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
class RunningStats:
    """Welford accumulator so long studies stay memory-flat."""

    __slots__ = ("count", "mean", "_m2")

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)

    @property
    def stdev(self) -> float | None:
        if self.count <= 1:
            return None
        return math.sqrt(max(0.0, self._m2 / (self.count - 1)))

    @property
    def t_stat(self) -> float | None:
        error = self.stdev
        if error is None or error <= 0 or self.count < 2:
            return None
        return self.mean / (error / math.sqrt(self.count))


@dataclass
class TradeStats:
    """Outcome counts and cost-adjusted edge for one variant and segment."""

    payout: float = 0.80
    spread_pips: float = 1.0
    pip: float = 0.0001
    signals: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    net_units: float = 0.0
    gross_pips_sum: float = 0.0
    net: RunningStats = field(default_factory=RunningStats)
    months: dict[str, "TradeStats"] = field(default_factory=dict)
    sessions: dict[str, "TradeStats"] = field(default_factory=dict)
    volatility: dict[str, "TradeStats"] = field(default_factory=dict)

    def add(
        self,
        side: str,
        entry: float,
        exit_price: float,
        *,
        timestamp: datetime,
        session: str,
        volatility: str,
        track_segments: bool = True,
    ) -> None:
        if side not in {"BUY", "SELL"}:
            return
        move = exit_price - entry
        signed = (move if side == "BUY" else -move) / self.pip
        self.signals += 1
        self.gross_pips_sum += signed
        self.net.add(signed - self.spread_pips)
        if move == 0:
            self.ties += 1
        elif signed > 0:
            self.wins += 1
            self.net_units += self.payout
        else:
            self.losses += 1
            self.net_units -= 1.0
        if not track_segments:
            return
        month = f"{timestamp.year:04d}-{timestamp.month:02d}"
        self.months.setdefault(month, TradeStats(self.payout, self.spread_pips, self.pip)).add(
            side, entry, exit_price, timestamp=timestamp, session=session,
            volatility=volatility, track_segments=False,
        )
        self.sessions.setdefault(session, TradeStats(self.payout, self.spread_pips, self.pip)).add(
            side, entry, exit_price, timestamp=timestamp, session=session,
            volatility=volatility, track_segments=False,
        )
        self.volatility.setdefault(volatility, TradeStats(self.payout, self.spread_pips, self.pip)).add(
            side, entry, exit_price, timestamp=timestamp, session=session,
            volatility=volatility, track_segments=False,
        )

    def as_dict(self) -> dict:
        decisive = self.wins + self.losses
        break_even = 1.0 / (1.0 + self.payout)
        interval = wilson_interval(self.wins, decisive) if decisive else None
        net_mean = self.net.mean if self.net.count else None
        return {
            "signals": self.signals,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "decisive_trades": decisive,
            "win_rate": (self.wins / decisive) if decisive else None,
            "win_rate_ci95": interval,
            "break_even_win_rate": break_even,
            "net_units": round(self.net_units, 4),
            "expected_value_per_trade": round(self.net_units / decisive, 6) if decisive else None,
            "gross_pips_mean": round(self.gross_pips_sum / self.signals, 4) if self.signals else None,
            "net_pips_mean": round(net_mean, 4) if net_mean is not None else None,
            "net_pips_t_stat": self.net.t_stat,
            "spread_pips": self.spread_pips,
            "has_edge_after_costs": bool(
                decisive >= 200
                and interval is not None
                and interval["lower"] > break_even
                and net_mean is not None
                and net_mean > 0
                and self.net.t_stat is not None
                and self.net.t_stat >= 2.0
            ),
        }

    def segments(self) -> dict:
        return {
            "months": {key: value.as_dict() for key, value in sorted(self.months.items())},
            "sessions": {key: value.as_dict() for key, value in sorted(self.sessions.items())},
            "volatility": {key: value.as_dict() for key, value in sorted(self.volatility.items())},
        }


def _empty_variants(config: "StudyConfig") -> dict[str, TradeStats]:
    return {
        name: TradeStats(payout=config.payout, spread_pips=config.spread_pips, pip=config.pip)
        for name in (
            "model_gated",
            "model_raw",
            "model_raw_pure",
            "always_up",
            "momentum_1",
            "momentum_5",
            "rsi_reversal",
            "rule_trend_pullback",
            "random_coin",
        )
    }


@dataclass(frozen=True)
class StudyConfig:
    horizon: int = 5
    warmup_bars: int = 40_000
    retrain_every: int = 2_880
    train_window: int = 40_000
    calibrate_every: int = 50
    replay_capacity: int = 200
    payout: float = 0.80
    buy_threshold: float = 0.57
    max_uncertainty: float = 0.10
    spread_pips: float = 1.0
    pip: float = 0.0001
    strategy: str = "ml_consensus"
    ensemble: bool = True
    ensemble_size: int = 3
    seed: int = 20261020
    min_trades_for_verdict: int = 200

    def __post_init__(self) -> None:
        if self.horizon < 1 or self.horizon > 60:
            raise ValueError("Horizon must be between 1 and 60 candles")
        if self.warmup_bars < 200:
            raise ValueError("Warm-up must cover at least 200 candles")
        if self.train_window < 240:
            raise ValueError("Training window must cover at least 240 candles")
        if not 0.0 < self.payout <= 1.0:
            raise ValueError("Payout must be in (0, 1]")
        if not 0.5 < self.buy_threshold < 1.0:
            raise ValueError("Buy threshold must be between 0.5 and 1.0")
        if self.spread_pips < 0:
            raise ValueError("Spread cannot be negative")
        if self.replay_capacity < 60:
            raise ValueError("Replay capacity must hold at least 60 outcomes")


# ---------------------------------------------------------------------------
# Study
# ---------------------------------------------------------------------------
def _session_for(hour: int) -> str:
    for start, end, name in SESSIONS:
        if start <= hour < end:
            return name
    return "Late"


def _volatility_bucket(value: float, cuts: tuple[float, float]) -> str:
    if value <= cuts[0]:
        return "low"
    if value <= cuts[1]:
        return "medium"
    return "high"


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round(fraction * (len(sorted_values) - 1)))))
    return float(sorted_values[index])


def _label(entry: float, exit_price: float) -> int | None:
    if exit_price == entry:
        return None
    return int(exit_price > entry)


def run_study(
    candles: Sequence[Candle],
    config: StudyConfig = StudyConfig(),
    *,
    progress: bool = False,
) -> dict:
    """Walk the whole series once, predicting before any label is used.

    The model is refitted on a rolling window of *matured* labels every
    ``retrain_every`` bars, updated online as each expiry passes, and
    recalibrated from the same 200-outcome replay the runtime uses. Every
    variant sees the same entry price, exit price, and bar.
    """
    if len(candles) < config.warmup_bars + config.horizon + 120:
        raise ValueError("Not enough candles for the requested warm-up and horizon")
    if config.strategy not in {"ml_consensus", "orbix_confluence"}:
        raise ValueError("The study is limited to model-driven executable strategies")

    rows = compute_features(candles)
    start = max(config.warmup_bars, 20)
    variants = _empty_variants(config)

    warmup_volatility = sorted(
        rows[index]["volatility_10"]
        for index in range(20, start)
        if rows[index] is not None
    )
    volume_cuts = (
        _percentile(warmup_volatility, 1 / 3),
        _percentile(warmup_volatility, 2 / 3),
    )

    # Labels are fixed by price history; the pointer only controls when they
    # become visible to the model.
    matured: list[tuple[dict[str, float], int]] = []
    next_label_index = 20
    replay: deque[tuple[dict[str, float], int]] = deque(maxlen=config.replay_capacity)
    learnable: deque[tuple[int, bool]] = deque(maxlen=config.horizon + 2)

    model: OnlineLogisticModel | None = None
    ensemble: BootstrapEnsemble | None = None
    calibrator = IsotonicCalibrator()
    calibration_report: dict | None = None
    updates_since_calibration = 0
    last_refit: int | None = None
    refits = 0
    refit_failures = 0
    calibration_fits = 0
    calibration_accepted = 0
    flat_fits = 0

    gates = {
        "bars": 0,
        "flat_bars": 0,
        "flat_blocked_signals": 0,
        "uncertainty_blocks": 0,
        "model_signals": 0,
        "shadow_reads": 0,
    }
    rng = random.Random(config.seed)
    progress_step = max(1, (len(candles) - start) // 12)

    def recalibrate() -> None:
        nonlocal calibrator, calibration_report, calibration_fits, calibration_accepted, flat_fits, updates_since_calibration
        if model is None or len(replay) < 60:
            return
        points = [(model.predict_up(features), label) for features, label in replay]
        candidate, report = fit_validated(points)
        calibration_report = report
        calibration_fits += 1
        if report.get("accepted") and candidate.fitted:
            calibrator = candidate
            calibration_accepted += 1
            if candidate.flat:
                flat_fits += 1
        updates_since_calibration = 0

    for index in range(start, len(candles) - config.horizon):
        # 1. Reveal labels whose expiry is the current bar and learn from them.
        while next_label_index <= index - config.horizon:
            feature = rows[next_label_index]
            outcome = _label(candles[next_label_index].close, candles[next_label_index + config.horizon].close)
            while learnable and learnable[0][0] < next_label_index:
                learnable.popleft()
            was_learnable = bool(learnable and learnable[0][0] == next_label_index and learnable[0][1])
            if was_learnable and feature is not None and outcome is not None and model is not None and model.trained:
                model.learn_one(feature, outcome)
                replay.append((feature, outcome))
                updates_since_calibration += 1
            if outcome is not None and feature is not None:
                matured.append((feature, outcome))
            next_label_index += 1

        # 2. Roll the model and the map forward at fixed boundaries.
        if model is None or (last_refit is not None and index - last_refit >= config.retrain_every):
            training = [sample for sample in matured[-config.train_window:] if sample[0] is not None]
            if len(training) >= 240:
                try:
                    model = OnlineLogisticModel().fit(training)
                    refits += 1
                    last_refit = index
                    if config.ensemble and len(training) >= 60:
                        try:
                            ensemble = BootstrapEnsemble.fit(training, size=config.ensemble_size, seed=config.seed)
                        except ValueError:
                            ensemble = None
                    recalibrate()
                except ValueError:
                    refit_failures += 1
        if model is not None and updates_since_calibration >= config.calibrate_every:
            recalibrate()

        feature = rows[index]
        if feature is None:
            continue
        gates["bars"] += 1
        if progress and gates["bars"] % progress_step == 0:
            print(
                f"  {format_datetime(candles[index].timestamp)}  {gates['bars']:,} bars",
                file=sys.stderr,
                flush=True,
            )

        raw = model.predict_up(feature) if model is not None and model.trained else None
        probability = calibrator.transform(raw) if (raw is not None and calibrator.fitted) else raw
        flat = bool(calibrator.fitted and calibrator.flat)
        uncertainty = None
        if ensemble is not None and ensemble.ready:
            try:
                _, uncertainty = ensemble.predict(feature)
            except ValueError:
                uncertainty = None

        candle = candles[index]
        previous_close = candles[index - 1].close
        hour = candle.timestamp.hour
        session = _session_for(hour)
        volatility = _volatility_bucket(feature["volatility_10"], volume_cuts)
        exit_price = candles[index + config.horizon].close
        entry_price = candle.close

        common = {
            "buy_threshold": config.buy_threshold,
            "sell_threshold": 1.0 - config.buy_threshold,
            "max_uncertainty": config.max_uncertainty,
        }
        gated = decide(
            config.strategy,
            candle,
            previous_close,
            feature,
            probability,
            probability_calibrated=calibrator.fitted,
            uncertainty=uncertainty,
            calibration_flat=flat,
            **common,
        )
        raw_decision = decide(
            config.strategy,
            candle,
            previous_close,
            feature,
            raw,
            probability_calibrated=False,
            uncertainty=None,
            calibration_flat=False,
            **common,
        )
        if raw is None:
            pure_side = None
        elif raw >= config.buy_threshold:
            pure_side = "BUY"
        elif raw <= 1.0 - config.buy_threshold:
            pure_side = "SELL"
        else:
            pure_side = None

        # 3. Record every variant on the same bar.
        if gated.side in {"BUY", "SELL"}:
            variants["model_gated"].add(gated.side, entry_price, exit_price,
                                        timestamp=candle.timestamp, session=session, volatility=volatility)
            gates["model_signals"] += 1
        if raw_decision.side in {"BUY", "SELL"}:
            variants["model_raw"].add(raw_decision.side, entry_price, exit_price,
                                      timestamp=candle.timestamp, session=session, volatility=volatility)
        if pure_side in {"BUY", "SELL"}:
            variants["model_raw_pure"].add(pure_side, entry_price, exit_price,
                                           timestamp=candle.timestamp, session=session, volatility=volatility)
        # The shadow reader is exactly the production fallback: a raw read is
        # kept only when the flat gate blocked an otherwise valid signal.
        shadow_read = flat and raw_decision.side in {"BUY", "SELL"}
        learnable.append((index, bool(gated.side in {"BUY", "SELL"} or shadow_read)))
        if shadow_read:
            gates["shadow_reads"] += 1
        if flat:
            gates["flat_bars"] += 1
            if raw_decision.side in {"BUY", "SELL"}:
                gates["flat_blocked_signals"] += 1
        if uncertainty is not None and uncertainty > config.max_uncertainty:
            if raw_decision.side in {"BUY", "SELL"}:
                gates["uncertainty_blocks"] += 1

        variants["always_up"].add("BUY", entry_price, exit_price,
                                  timestamp=candle.timestamp, session=session, volatility=volatility)
        if feature["return_1"] != 0:
            variants["momentum_1"].add("BUY" if feature["return_1"] > 0 else "SELL", entry_price, exit_price,
                                       timestamp=candle.timestamp, session=session, volatility=volatility)
        if feature["return_5"] != 0:
            variants["momentum_5"].add("BUY" if feature["return_5"] > 0 else "SELL", entry_price, exit_price,
                                       timestamp=candle.timestamp, session=session, volatility=volatility)
        if feature["rsi_scaled"] <= -0.4:
            variants["rsi_reversal"].add("BUY", entry_price, exit_price,
                                         timestamp=candle.timestamp, session=session, volatility=volatility)
        elif feature["rsi_scaled"] >= 0.4:
            variants["rsi_reversal"].add("SELL", entry_price, exit_price,
                                         timestamp=candle.timestamp, session=session, volatility=volatility)
        rule = decide("trend_pullback", candle, previous_close, feature, None)
        if rule.side in {"BUY", "SELL"}:
            variants["rule_trend_pullback"].add(rule.side, entry_price, exit_price,
                                                timestamp=candle.timestamp, session=session, volatility=volatility)
        variants["random_coin"].add("BUY" if rng.random() < 0.5 else "SELL", entry_price, exit_price,
                                    timestamp=candle.timestamp, session=session, volatility=volatility)

    if model is None or gates["bars"] == 0:
        raise ValueError("The study produced no scored bars")

    tested = gates["bars"]
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "horizon": config.horizon,
            "warmup_bars": config.warmup_bars,
            "retrain_every": config.retrain_every,
            "train_window": config.train_window,
            "payout": config.payout,
            "buy_threshold": config.buy_threshold,
            "max_uncertainty": config.max_uncertainty,
            "spread_pips": config.spread_pips,
            "strategy": config.strategy,
            "ensemble": config.ensemble,
        },
        "data": {
            "candles": len(candles),
            "first": format_datetime(candles[0].timestamp),
            "last": format_datetime(candles[-1].timestamp),
            "scored_bars": tested,
        },
        "model": {
            "refits": refits,
            "refit_failures": refit_failures,
            "training_samples": model.training_samples,
            "calibration_fits": calibration_fits,
            "calibration_accepted": calibration_accepted,
            "flat_fits": flat_fits,
            "levels": calibrator.levels,
            "flat": calibrator.flat,
            "calibration_samples": calibrator.samples,
            "last_calibration_report": calibration_report,
        },
        "gates": {
            **gates,
            "coverage": round(gates["model_signals"] / tested, 6),
            "flat_bar_share": round(gates["flat_bars"] / tested, 6),
            "shadow_share": round(gates["shadow_reads"] / tested, 6),
        },
        "variants": {name: stats.as_dict() for name, stats in variants.items()},
        "segments": {
            "model_gated": variants["model_gated"].segments(),
            "model_raw": variants["model_raw"].segments(),
            "model_raw_pure": variants["model_raw_pure"].segments(),
            "always_up": variants["always_up"].segments(),
            "momentum_5": variants["momentum_5"].segments(),
            "rule_trend_pullback": variants["rule_trend_pullback"].segments(),
        },
        "verdict": {},
        "note": (
            "Historical walk-forward estimate on one instrument and one year. "
            "Spread is an assumption, the payout is an assumption, and neither "
            "is a live quote. Small samples have wide intervals; a positive "
            "year is not a promise about the next one."
        ),
    }
    result["verdict"] = build_verdict(result, config)
    return result


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def build_verdict(result: dict, config: StudyConfig) -> dict:
    """Judge the *gated* product path, and report the raw score separately.

    The raw view is a diagnostic, never a tradeable result: it is the
    counterfactual that only the learning loop consumes. Calling it the
    headline would overstate what the dashboard can actually deliver.
    """
    variants = result["variants"]
    baselines = [
        "always_up", "momentum_1", "momentum_5",
        "rsi_reversal", "rule_trend_pullback", "random_coin",
    ]
    eligible = [
        name for name in baselines
        if variants[name]["decisive_trades"] >= config.min_trades_for_verdict
        and variants[name]["net_pips_mean"] is not None
    ]
    best_baseline = max(eligible, key=lambda name: variants[name]["net_pips_mean"]) if eligible else None
    primary = variants["model_gated"]
    raw = variants["model_raw"]
    interval = primary["win_rate_ci95"]
    reasons: list[str] = []
    tradeable = True

    if primary["decisive_trades"] < config.min_trades_for_verdict:
        tradeable = False
        reasons.append(
            f"The gate allowed only {primary['decisive_trades']} decisive trades; "
            f"{config.min_trades_for_verdict} are required before judging an edge."
        )
    else:
        if interval is None or interval["lower"] <= primary["break_even_win_rate"]:
            tradeable = False
            reasons.append(
                "The 95% win-rate interval includes the break-even rate "
                f"({primary['break_even_win_rate']:.4f})."
            )
        if primary["net_pips_mean"] is None or primary["net_pips_mean"] <= 0:
            tradeable = False
            reasons.append("Mean net pips after the assumed spread are not positive.")
        if primary["net_pips_t_stat"] is None or primary["net_pips_t_stat"] < 2.0:
            tradeable = False
            reasons.append("The cost-adjusted edge is not distinguishable from zero (t < 2).")
        if best_baseline is not None:
            baseline = variants[best_baseline]
            if (primary["net_pips_mean"] is None or baseline["net_pips_mean"] is None
                    or primary["net_pips_mean"] <= baseline["net_pips_mean"]):
                tradeable = False
                reasons.append(
                    f"The gated model does not beat the best baseline ({best_baseline}, "
                    f"{baseline['net_pips_mean']:.3f} net pips per signal)."
                )

    if tradeable:
        headline = (
            "Positive after the assumed costs, and better than the tested baselines. "
            "This is one year on one pair; it is evidence, not a guarantee."
        )
    elif primary["decisive_trades"] < config.min_trades_for_verdict:
        headline = "No tradeable edge demonstrated: too few gated trades to measure one."
    else:
        headline = "No tradeable edge after costs on this data."

    raw_edge = bool(
        raw["decisive_trades"] >= config.min_trades_for_verdict
        and raw["has_edge_after_costs"]
    )
    if raw_edge:
        reasons.append(
            "The raw score alone clears the cost bar, but it is a counterfactual "
            "learning signal and is not what the product trades."
        )
    return {
        "headline": headline,
        "tradeable": tradeable,
        "reference_variant": "model_gated",
        "best_baseline": best_baseline,
        "reasons": reasons,
        "comparison": {
            "model_net_pips_mean": primary["net_pips_mean"],
            "best_baseline_net_pips_mean": (
                variants[best_baseline]["net_pips_mean"] if best_baseline else None
            ),
            "model_win_rate": primary["win_rate"],
            "best_baseline_win_rate": (
                variants[best_baseline]["win_rate"] if best_baseline else None
            ),
        },
        "raw_diagnostic": {
            "decisive_trades": raw["decisive_trades"],
            "win_rate": raw["win_rate"],
            "net_pips_mean": raw["net_pips_mean"],
            "has_edge_after_costs": raw["has_edge_after_costs"],
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _table(result: dict) -> str:
    bars = result["data"]["scored_bars"]
    header = (
        f"{'variant':<22}{'signals':>9}{'cover':>8}{'win%':>8}{'95% low':>9}"
        f"{'net pips':>12}{'t':>10}{'units':>10}"
    )
    lines = [header, "-" * len(header)]
    for name, stats in result["variants"].items():
        win_rate = stats["win_rate"]
        low = stats["win_rate_ci95"]["lower"] if stats["win_rate_ci95"] else None
        t_stat = stats["net_pips_t_stat"]
        lines.append(
            f"{name:<22}{stats['signals']:>9,}"
            f"{(stats['signals'] / bars if bars else 0.0) * 100:>7.2f}%"
            f"{(win_rate * 100 if win_rate is not None else float('nan')):>7.1f}%"
            f"{(low * 100 if low is not None else float('nan')):>8.1f}%"
            f"{(stats['net_pips_mean'] if stats['net_pips_mean'] is not None else float('nan')):>12.3f}"
            f"{(t_stat if t_stat is not None else float('nan')):>10.2f}"
            f"{stats['net_units']:>10.1f}"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", required=True, help="Header-less HistData M1 CSV or a headed OHLCV CSV")
    parser.add_argument("--tz-offset", type=float, default=0.0, help="Hours to add to naive timestamps (HistData 2024 = 5)")
    parser.add_argument("--limit", type=int, default=None, help="Read only the first N rows (smoke tests)")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--warmup-bars", type=int, default=40_000)
    parser.add_argument("--retrain-every", type=int, default=2_880)
    parser.add_argument("--train-window", type=int, default=40_000)
    parser.add_argument("--payout", type=float, default=0.80)
    parser.add_argument("--spread-pips", type=float, default=1.0)
    parser.add_argument("--strategy", default="ml_consensus")
    parser.add_argument("--no-ensemble", action="store_true")
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--out", default=None, help="Write the full JSON result here")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    candles = load_candles_csv(arguments.csv, tz_offset_hours=arguments.tz_offset, limit=arguments.limit)
    config = StudyConfig(
        horizon=arguments.horizon,
        warmup_bars=arguments.warmup_bars,
        retrain_every=arguments.retrain_every,
        train_window=arguments.train_window,
        payout=arguments.payout,
        spread_pips=arguments.spread_pips,
        strategy=arguments.strategy,
        ensemble=not arguments.no_ensemble,
        seed=arguments.seed,
    )
    print(
        f"Loaded {len(candles):,} candles {format_datetime(candles[0].timestamp)} -> "
        f"{format_datetime(candles[-1].timestamp)}",
        file=sys.stderr,
    )
    result = run_study(candles, config, progress=True)
    print(_table(result))
    print()
    print(result["verdict"]["headline"])
    for reason in result["verdict"]["reasons"]:
        print(f"- {reason}")
    print(
        f"\nModel: {result['model']['refits']} refits, {result['model']['calibration_fits']} calibration fits, "
        f"final map levels={result['model']['levels']} flat={result['model']['flat']}."
    )
    print(
        f"Gate: {result['gates']['model_signals']:,} signals on {result['data']['scored_bars']:,} bars "
        f"({result['gates']['coverage'] * 100:.2f}% coverage), flat on "
        f"{result['gates']['flat_bar_share'] * 100:.1f}% of bars, "
        f"{result['gates']['shadow_reads']:,} shadow reads."
    )
    raw = result["verdict"]["raw_diagnostic"]
    print(
        f"Raw-score diagnostic: {raw['decisive_trades']:,} decisive reads, "
        f"win rate {raw['win_rate'] * 100 if raw['win_rate'] is not None else float('nan'):.2f}%, "
        f"net {raw['net_pips_mean'] if raw['net_pips_mean'] is not None else float('nan'):.3f} pips per read."
    )
    if arguments.out:
        destination = Path(arguments.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
