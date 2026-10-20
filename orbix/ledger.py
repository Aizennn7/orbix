from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

from .abstentions import REASON_LABELS, SESSION_ORDER, classify_reason, hour_bucket, session_for
from .metrics import wilson_interval


def _normalise_entry_time(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


class PaperLedger:
    """Small SQLite ledger for manually confirmed paper outcomes."""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    entry_time TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    probability_up REAL,
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    dataset_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    exit_price REAL,
                    outcome TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(signals)")}
            if "dataset_id" not in columns:
                connection.execute("ALTER TABLE signals ADD COLUMN dataset_id TEXT NOT NULL DEFAULT ''")
            if "entry_time" not in columns:
                connection.execute("ALTER TABLE signals ADD COLUMN entry_time TEXT NOT NULL DEFAULT ''")
            if "probability_raw" not in columns:
                connection.execute("ALTER TABLE signals ADD COLUMN probability_raw REAL")
            if "uncertainty" not in columns:
                connection.execute("ALTER TABLE signals ADD COLUMN uncertainty REAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS abstentions (
                    abstention_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    entry_time TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    dataset_id TEXT NOT NULL DEFAULT '',
                    session TEXT NOT NULL,
                    hour_utc INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS abstentions_created_at ON abstentions (created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS learning_events (
                    signal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    label INTEGER,
                    probability_up REAL,
                    correct INTEGER,
                    model_updated INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            learning_columns = {row["name"] for row in connection.execute("PRAGMA table_info(learning_events)")}
            if "kind" not in learning_columns:
                connection.execute("ALTER TABLE learning_events ADD COLUMN kind TEXT NOT NULL DEFAULT 'paper'")
            if "features_json" not in learning_columns:
                connection.execute("ALTER TABLE learning_events ADD COLUMN features_json TEXT NOT NULL DEFAULT ''")
            if "entry_time" not in learning_columns:
                connection.execute("ALTER TABLE learning_events ADD COLUMN entry_time TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS learning_events_kind ON learning_events (kind, dataset_id, entry_time)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS learning_state (
                    dataset_id TEXT PRIMARY KEY,
                    last_entry_time TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS shadow_signals (
                    shadow_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    probability_up REAL NOT NULL,
                    probability_calibrated REAL,
                    features_json TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    exit_price REAL,
                    outcome TEXT,
                    learned INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE (pair, strategy, dataset_id, entry_time)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS shadow_pending ON shadow_signals (status, pair, dataset_id)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS engine_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS engine_events_created ON engine_events (created_at)"
            )

    def record(self, signal, features: dict[str, float] | None, dataset_id: str = "") -> None:
        if signal.side == "NO SIGNAL":
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO signals
                (signal_id, pair, strategy, side, entry_price, generated_at, entry_time, expires_at,
                 probability_up, source, reason, features_json, dataset_id, created_at,
                 probability_raw, uncertainty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.signal_id,
                    signal.pair,
                    signal.strategy,
                    signal.side,
                    signal.entry_price,
                    signal.generated_at.isoformat(),
                    (signal.entry_time or signal.generated_at).isoformat(),
                    signal.expires_at.isoformat(),
                    signal.probability_up,
                    signal.source,
                    signal.reason,
                    json.dumps(features or {}),
                    dataset_id,
                    datetime.now(timezone.utc).isoformat(),
                    getattr(signal, "probability_raw", None),
                    getattr(signal, "uncertainty", None),
                ),
            )

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("features_json", None)
            item["entry_time"] = item.get("entry_time") or item["generated_at"]
            result.append(item)
        return result

    def has_signal_for_time(self, pair: str, strategy: str, dataset_id: str, entry_time: datetime) -> bool:
        timestamp = entry_time.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM signals WHERE pair = ? AND strategy = ? AND dataset_id = ?
                   AND COALESCE(NULLIF(entry_time, ''), generated_at) = ? LIMIT 1""",
                (pair, strategy, dataset_id, timestamp),
            ).fetchone()
        return row is not None

    def get(self, signal_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------ human-style review
    def day_signals(self, pair: str, start: datetime, end: datetime) -> list[dict]:
        """Signals with an entry inside one UTC trading day, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT entry_time, strategy, side, status, entry_price, exit_price, outcome
                   FROM signals
                   WHERE pair = ? AND entry_time >= ? AND entry_time < ?
                     AND entry_time != '' AND source != 'SYNTHETIC DEMO'
                   ORDER BY entry_time""",
                (pair.upper(), start.isoformat(), end.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_results(self, pair: str, limit: int = 12) -> list[dict]:
        """Most recent decided outcomes for a pair, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT entry_time, strategy, side, outcome
                   FROM signals
                   WHERE pair = ? AND status = 'RESOLVED'
                     AND outcome IN ('WIN', 'LOSS') AND entry_time != ''
                     AND source != 'SYNTHETIC DEMO'
                   ORDER BY entry_time DESC LIMIT ?""",
                (pair.upper(), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def pending_count(self, pair: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS pending FROM signals
                   WHERE pair = ? AND status = 'PENDING' AND source != 'SYNTHETIC DEMO'""",
                (pair.upper(),),
            ).fetchone()
        return int(row["pending"]) if row else 0

    def context_rows(self, pair: str) -> list[dict]:
        """Decided non-synthetic outcomes used for context memory."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT strategy, entry_time, outcome
                   FROM signals
                   WHERE pair = ? AND status = 'RESOLVED'
                     AND outcome IN ('WIN', 'LOSS')
                     AND source != 'SYNTHETIC DEMO' AND entry_time != ''
                   ORDER BY entry_time""",
                (pair.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pending(self, pair: str, dataset_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM signals
                   WHERE status = 'PENDING' AND source = 'IMPORTED CSV'
                     AND pair = ? AND dataset_id = ?
                   ORDER BY expires_at, created_at""",
                (pair, dataset_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("features_json"))
            result.append(item)
        return result

    def record_abstention(
        self,
        *,
        pair: str,
        strategy: str,
        reason: str,
        kind: str = "gate",
        entry_time: datetime | str | None = None,
        source: str = "",
        dataset_id: str = "",
        now: datetime | None = None,
    ) -> bool:
        """Store one NO SIGNAL / skipped-bar decision. Returns False if duplicated.

        Abstentions are evidence about restraint, not trades. They are deduplicated
        by dataset, strategy, entry boundary, kind, and reason code so a repeated
        poll of the same closed bar cannot inflate the counts.
        """
        created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        reason_text = str(reason or "").strip()[:400] or "No reason recorded."
        code = classify_reason(reason_text)
        if isinstance(entry_time, datetime):
            moment = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)
            entry_text = moment.astimezone(timezone.utc).isoformat()
        else:
            entry_text = str(entry_time or "")
            moment = created
            if entry_text:
                try:
                    parsed = datetime.fromisoformat(entry_text.replace("Z", "+00:00"))
                    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    moment = created
        session = session_for(moment)
        bucket = hour_bucket(moment)
        digest = hashlib.blake2s(
            f"{dataset_id}|{strategy}|{entry_text}|{kind}|{code}".encode("utf-8"), digest_size=10
        ).hexdigest()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO abstentions
                (abstention_id, pair, strategy, kind, reason_code, reason, entry_time,
                 source, dataset_id, session, hour_utc, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    digest,
                    str(pair or "UNKNOWN"),
                    str(strategy or "unknown"),
                    str(kind or "gate"),
                    code,
                    reason_text,
                    entry_text,
                    str(source or ""),
                    str(dataset_id or ""),
                    session,
                    int(bucket),
                    created.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def abstention_summary(
        self,
        days: int = 7,
        limit: int = 25,
        now: datetime | None = None,
    ) -> dict:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        days = max(1, min(int(days), 90))
        cutoff = (current - timedelta(days=days)).isoformat()
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._connect() as connection:
            all_time = int(connection.execute("SELECT COUNT(*) FROM abstentions").fetchone()[0])
            today = int(
                connection.execute(
                    "SELECT COUNT(*) FROM abstentions WHERE created_at >= ?", (day_start,)
                ).fetchone()[0]
            )
            rows = connection.execute(
                """SELECT kind, reason_code, session, hour_utc, created_at
                   FROM abstentions WHERE created_at >= ?""",
                (cutoff,),
            ).fetchall()
            recent_rows = connection.execute(
                """SELECT pair, strategy, kind, reason_code, reason, entry_time, session,
                          hour_utc, created_at
                   FROM abstentions ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            decided = int(
                connection.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ?", (cutoff,)
                ).fetchone()[0]
            )
            resolved = connection.execute(
                """SELECT entry_time, outcome FROM signals
                   WHERE outcome IN ('WIN', 'LOSS') AND created_at >= ?""",
                (cutoff,),
            ).fetchall()

        by_code: dict[str, int] = {}
        by_session: dict[str, dict[str, int]] = {}
        by_hour = [{"hour": hour, "gate": 0, "operational": 0} for hour in range(24)]
        gate_count = 0
        operational_count = 0
        for row in rows:
            kind = str(row["kind"])
            code = str(row["reason_code"])
            session = str(row["session"])
            by_code[code] = by_code.get(code, 0) + 1
            bucket = by_session.setdefault(session, {"gate": 0, "operational": 0})
            if kind == "gate":
                gate_count += 1
                bucket["gate"] += 1
            else:
                operational_count += 1
                bucket["operational"] += 1
            hour_index = int(row["hour_utc"])
            if 0 <= hour_index < 24:
                by_hour[hour_index]["gate" if kind == "gate" else "operational"] += 1

        window_total = len(rows)
        code_rows = [
            {
                "code": code,
                "label": REASON_LABELS.get(code, code),
                "count": count,
                "share": (count / window_total) if window_total else 0.0,
            }
            for code, count in sorted(by_code.items(), key=lambda item: (-item[1], item[0]))
        ]
        ordered_sessions = [name for name in SESSION_ORDER if name in by_session]
        ordered_sessions += [name for name in by_session if name not in SESSION_ORDER]
        session_rows = [
            {
                "session": name,
                "gate": by_session[name]["gate"],
                "operational": by_session[name]["operational"],
                "total": by_session[name]["gate"] + by_session[name]["operational"],
            }
            for name in ordered_sessions
        ]

        outcomes: dict[str, dict[str, int]] = {}
        for row in resolved:
            session = session_for(row["entry_time"] or "")
            bucket = outcomes.setdefault(session, {"wins": 0, "losses": 0})
            bucket["wins" if row["outcome"] == "WIN" else "losses"] += 1
        outcome_rows = []
        for name in list(SESSION_ORDER) + [key for key in outcomes if key not in SESSION_ORDER]:
            if name not in outcomes:
                continue
            wins = outcomes[name]["wins"]
            losses = outcomes[name]["losses"]
            total = wins + losses
            outcome_rows.append({
                "session": name,
                "wins": wins,
                "losses": losses,
                "decisive": total,
                "win_rate": (wins / total) if total else None,
                "win_rate_ci95": wilson_interval(wins, total),
            })

        denominator = gate_count + decided
        return {
            "window_days": days,
            "generated_at": current.isoformat(),
            "totals": {"all_time": all_time, "today": today, "window": window_total},
            "gate": {
                "window": gate_count,
                "today": sum(1 for row in rows if str(row["kind"]) == "gate" and str(row["created_at"]) >= day_start),
            },
            "operational": {"window": operational_count},
            "decided_signals_window": decided,
            "abstention_ratio": (gate_count / denominator) if denominator else None,
            "by_code": code_rows,
            "by_session": session_rows,
            "by_hour": by_hour,
            "outcomes_by_session": outcome_rows,
            "recent": [
                {
                    "pair": row["pair"],
                    "strategy": row["strategy"],
                    "kind": row["kind"],
                    "code": row["reason_code"],
                    "label": REASON_LABELS.get(row["reason_code"], row["reason_code"]),
                    "reason": row["reason"],
                    "entry_time": row["entry_time"],
                    "session": row["session"],
                    "hour_utc": row["hour_utc"],
                    "created_at": row["created_at"],
                }
                for row in recent_rows
            ],
        }

    def record_learning_event(
        self,
        signal_id: str,
        pair: str,
        dataset_id: str,
        label: int | None,
        probability_up: float | None,
        correct: bool | None,
        model_updated: bool,
        *,
        kind: str = "paper",
        features: dict[str, float] | None = None,
        entry_time: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO learning_events
                   (signal_id, pair, dataset_id, label, probability_up, correct, model_updated,
                    created_at, kind, features_json, entry_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal_id,
                    pair,
                    dataset_id,
                    label,
                    probability_up,
                    None if correct is None else int(correct),
                    int(model_updated),
                    datetime.now(timezone.utc).isoformat(),
                    kind,
                    json.dumps(features or {}),
                    entry_time or "",
                ),
            )

    def has_learning_event(self, signal_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM learning_events WHERE signal_id = ? LIMIT 1", (signal_id,)
            ).fetchone()
        return row is not None

    def has_learning_for_entry(
        self, dataset_id: str, entry_time: datetime | str, kinds: tuple[str, ...] = ("paper", "shadow")
    ) -> bool:
        """True when this bar was already learned through a signal stream."""
        value = _normalise_entry_time(entry_time)
        if not value or not kinds:
            return False
        placeholders = ", ".join("?" for _ in kinds)
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT 1 FROM learning_events
                   WHERE dataset_id = ? AND entry_time = ? AND kind IN ({placeholders})
                   LIMIT 1""",
                (dataset_id, value, *kinds),
            ).fetchone()
        return row is not None

    def learning_summary(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(model_updated), 0),
                          AVG(CASE WHEN model_updated = 1 THEN correct END), MAX(created_at),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct = 1 THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct IS NOT NULL THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN kind = 'shadow' AND label IS NOT NULL THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN kind = 'shadow' AND model_updated = 1 THEN 1 ELSE 0 END), 0)
                   FROM learning_events WHERE kind = 'paper'"""
            ).fetchone()
            shadow_row = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(model_updated), 0)
                   FROM learning_events WHERE kind = 'shadow'"""
            ).fetchone()
            dataset_row = connection.execute(
                """SELECT COUNT(*),
                          COALESCE(SUM(CASE WHEN label IS NOT NULL THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(model_updated), 0),
                          COALESCE(SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN correct IS NOT NULL THEN 1 ELSE 0 END), 0),
                          MAX(entry_time)
                   FROM learning_events WHERE kind = 'dataset'"""
            ).fetchone()
        correct = int(row[4])
        evaluated = int(row[5])
        return {
            "resolved_outcomes_seen": int(row[0]),
            "online_updates": int(row[1]),
            "prequential_accuracy": float(row[2]) if row[2] is not None else None,
            "prequential_correct_predictions": correct,
            "prequential_evaluated_predictions": evaluated,
            "prequential_accuracy_ci95": wilson_interval(correct, evaluated),
            "last_update": row[3],
            "shadow_resolved": int(shadow_row[0]),
            "shadow_learned": int(shadow_row[1]),
            "dataset_resolved": int(dataset_row[1]),
            "dataset_learned": int(dataset_row[2]),
            "dataset_accuracy": (
                int(dataset_row[3]) / int(dataset_row[4]) if int(dataset_row[4]) else None
            ),
            "dataset_last_entry": dataset_row[5] or None,
        }

    def _kind_history(self, kind: str, limit: int, dataset_id: str | None = None) -> list[dict]:
        clause = " AND dataset_id = ?" if dataset_id else ""
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT probability_up, label, correct, model_updated, entry_time, created_at
                   FROM learning_events
                   WHERE kind = ? AND probability_up IS NOT NULL AND label IN (0, 1){clause}
                   ORDER BY created_at DESC LIMIT ?""",
                (kind, *parameters, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def learning_history(self, limit: int = 600, dataset_id: str | None = None) -> list[dict]:
        """Resolved paper predictions in chronological order, for tracker hydration."""
        return self._kind_history("paper", limit, dataset_id)

    def shadow_history(self, limit: int = 600, dataset_id: str | None = None) -> list[dict]:
        """Shadow predictions in chronological order, for a separate tracker."""
        return self._kind_history("shadow", limit, dataset_id)

    def dataset_history(self, limit: int = 600, dataset_id: str | None = None) -> list[dict]:
        """Every completed-bar label in chronological order, for its own tracker."""
        return self._kind_history("dataset", limit, dataset_id)

    def learning_replay(self, limit: int = 200, dataset_id: str | None = None) -> list[dict]:
        """Stored feature rows and labels for guarded replay checks."""
        clause = " AND le.dataset_id = ?" if dataset_id else ""
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT le.label, le.probability_up,
                          COALESCE(NULLIF(le.features_json, ''), s.features_json) AS features_json
                   FROM learning_events le LEFT JOIN signals s ON s.signal_id = le.signal_id
                   WHERE le.label IN (0, 1){clause}
                     AND COALESCE(NULLIF(le.features_json, ''), s.features_json) NOT IN ('', '{{}}')
                   ORDER BY le.created_at DESC LIMIT ?""",
                (*parameters, max(1, int(limit))),
            ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["features"] = json.loads(item.pop("features_json"))
            except (TypeError, ValueError):
                continue
            if item["features"]:
                result.append(item)
        return result

    def learning_by_strategy(self, dataset_id: str | None = None) -> list[dict]:
        """Per-strategy prediction record, joined through the stored signal."""
        dataset_clause = " AND s.dataset_id = ?" if dataset_id else ""
        outcome_filter = " WHERE dataset_id = ? AND status = 'RESOLVED'" if dataset_id else " WHERE status = 'RESOLVED'"
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            learning_rows = connection.execute(
                f"""SELECT s.strategy AS strategy, COUNT(*) AS resolved,
                          COALESCE(SUM(CASE WHEN le.correct IS NOT NULL THEN 1 ELSE 0 END), 0) AS evaluated,
                          COALESCE(SUM(CASE WHEN le.correct = 1 THEN 1 ELSE 0 END), 0) AS correct,
                          COALESCE(SUM(le.model_updated), 0) AS updates,
                          AVG(CASE WHEN le.correct IS NOT NULL THEN le.probability_up END) AS mean_probability
                   FROM learning_events le JOIN signals s ON s.signal_id = le.signal_id
                   WHERE le.kind = 'paper'{dataset_clause}
                   GROUP BY s.strategy""",
                parameters,
            ).fetchall()
            outcome_rows = connection.execute(
                f"""SELECT strategy, COUNT(*) AS resolved,
                          COALESCE(SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END), 0) AS wins,
                          COALESCE(SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END), 0) AS losses,
                          COALESCE(SUM(CASE WHEN outcome = 'TIE' THEN 1 ELSE 0 END), 0) AS ties
                   FROM signals{outcome_filter} GROUP BY strategy""",
                parameters,
            ).fetchall()
        outcomes = {row["strategy"]: dict(row) for row in outcome_rows}
        result = []
        for row in learning_rows:
            item = dict(row)
            item["evaluated"] = int(item["evaluated"])
            item["correct"] = int(item["correct"])
            item["accuracy"] = (item["correct"] / item["evaluated"]) if item["evaluated"] else None
            item["accuracy_ci95"] = wilson_interval(item["correct"], item["evaluated"])
            for key, value in outcomes.get(item["strategy"], {}).items():
                if key == "strategy":
                    continue
                item["outcome_resolved" if key == "resolved" else key] = value
            result.append(item)
        result.sort(key=lambda item: (-item["evaluated"], item["strategy"]))
        return result

    def resolve(self, signal_id: str, exit_price: float) -> dict:
        if exit_price <= 0:
            raise ValueError("Exit price must be greater than zero")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("Signal was not found")
            if row["status"] != "PENDING":
                raise ValueError("Signal has already been resolved")
            if row["source"] == "SYNTHETIC DEMO":
                raise ValueError("Synthetic demo outcomes cannot train the model; import real historical/paper data first")
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > datetime.now(timezone.utc):
                raise ValueError("Signal has not reached its expiry time yet")
            entry = float(row["entry_price"])
            side = row["side"]
            if exit_price == entry:
                direction_label = None
                outcome = "TIE"
            else:
                direction_label = int(exit_price > entry)
                won = (side == "BUY" and direction_label == 1) or (side == "SELL" and direction_label == 0)
                outcome = "WIN" if won else "LOSS"
            connection.execute(
                "UPDATE signals SET status = 'RESOLVED', exit_price = ?, outcome = ? WHERE signal_id = ?",
                (exit_price, outcome, signal_id),
            )
            updated = dict(connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone())
        updated["features"] = json.loads(updated.pop("features_json"))
        updated["direction_label"] = direction_label
        return updated

    # ------------------------------------------------------------------ shadow
    def record_shadow(
        self,
        shadow: dict,
        features: dict[str, float] | None,
        dataset_id: str,
    ) -> bool:
        """Store a model read that was not traded, for learning only."""
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO shadow_signals
                   (shadow_id, pair, strategy, side, entry_price, entry_time, expires_at,
                    probability_up, probability_calibrated, features_json, dataset_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    shadow["shadow_id"],
                    shadow["pair"],
                    shadow["strategy"],
                    shadow["side"],
                    shadow["entry_price"],
                    shadow["entry_time"],
                    shadow["expires_at"],
                    shadow["probability_up"],
                    shadow.get("probability_calibrated"),
                    json.dumps(features or {}),
                    dataset_id,
                    shadow.get("reason", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def has_shadow_for_time(self, pair: str, strategy: str, dataset_id: str, entry_time: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM shadow_signals
                   WHERE pair = ? AND strategy = ? AND dataset_id = ? AND entry_time = ? LIMIT 1""",
                (pair, strategy, dataset_id, entry_time),
            ).fetchone()
        return row is not None

    def list_pending_shadows(self, pair: str, dataset_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM shadow_signals
                   WHERE status = 'PENDING' AND pair = ? AND dataset_id = ?
                   ORDER BY expires_at, created_at""",
                (pair, dataset_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("features_json"))
            result.append(item)
        return result

    def resolve_shadow(self, shadow_id: str, exit_price: float) -> dict:
        if exit_price <= 0:
            raise ValueError("Exit price must be greater than zero")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shadow_signals WHERE shadow_id = ?", (shadow_id,)).fetchone()
            if row is None:
                raise ValueError("Shadow read was not found")
            if row["status"] != "PENDING":
                raise ValueError("Shadow read has already been resolved")
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > datetime.now(timezone.utc):
                raise ValueError("Shadow read has not reached its expiry time yet")
            entry = float(row["entry_price"])
            side = row["side"]
            if exit_price == entry:
                direction_label = None
                outcome = "TIE"
            else:
                direction_label = int(exit_price > entry)
                won = (side == "BUY" and direction_label == 1) or (side == "SELL" and direction_label == 0)
                outcome = "WIN" if won else "LOSS"
            connection.execute(
                """UPDATE shadow_signals
                   SET status = 'RESOLVED', exit_price = ?, outcome = ?, resolved_at = ?
                   WHERE shadow_id = ?""",
                (exit_price, outcome, datetime.now(timezone.utc).isoformat(), shadow_id),
            )
            updated = dict(connection.execute(
                "SELECT * FROM shadow_signals WHERE shadow_id = ?", (shadow_id,)
            ).fetchone())
        updated["features"] = json.loads(updated.pop("features_json"))
        updated["direction_label"] = direction_label
        return updated

    def mark_shadow_learned(self, shadow_id: str, learned: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE shadow_signals SET learned = ? WHERE shadow_id = ?",
                (int(bool(learned)), shadow_id),
            )

    def shadow_summary(self, dataset_id: str | None = None) -> dict:
        clause = " WHERE dataset_id = ?" if dataset_id else ""
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(*),
                           COALESCE(SUM(CASE WHEN status = 'RESOLVED' THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(learned), 0),
                           COALESCE(SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(CASE WHEN outcome = 'TIE' THEN 1 ELSE 0 END), 0)
                   FROM shadow_signals{clause}""",
                parameters,
            ).fetchone()
        resolved, wins, losses = int(row[1]), int(row[4]), int(row[5])
        decisive = wins + losses
        return {
            "total": int(row[0]),
            "resolved": resolved,
            "pending": int(row[2]),
            "learned": int(row[3]),
            "wins": wins,
            "losses": losses,
            "ties": int(row[6]),
            "accuracy": (wins / decisive) if decisive else None,
            "accuracy_ci95": wilson_interval(wins, decisive),
        }

    def dataset_summary(self, dataset_id: str | None = None) -> dict:
        """Counts for the every-completed-bar learning stream."""
        clause = " AND dataset_id = ?" if dataset_id else ""
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(*),
                           COALESCE(SUM(CASE WHEN label IS NOT NULL THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(model_updated), 0),
                           COALESCE(SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0),
                           COALESCE(SUM(CASE WHEN correct IS NOT NULL THEN 1 ELSE 0 END), 0),
                           MAX(entry_time)
                   FROM learning_events WHERE kind = 'dataset'{clause}""",
                parameters,
            ).fetchone()
        scored = int(row[4])
        return {
            "total": int(row[0]),
            "resolved": int(row[1]),
            "learned": int(row[2]),
            "correct": int(row[3]),
            "scored": scored,
            "accuracy": (int(row[3]) / scored) if scored else None,
            "accuracy_ci95": wilson_interval(int(row[3]), scored),
            "last_entry_time": row[5] or None,
        }

    def seed_dataset_learning(self, dataset_id: str, entry_time: datetime | str) -> None:
        """Remember where incremental learning starts; never move an existing marker back."""
        value = _normalise_entry_time(entry_time)
        if value is None:
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO learning_state (dataset_id, last_entry_time, updated_at)
                   VALUES (?, ?, ?)""",
                (dataset_id, value, datetime.now(timezone.utc).isoformat()),
            )

    def last_dataset_entry_time(self, dataset_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_entry_time FROM learning_state WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return row[0] if row else None

    def set_dataset_entry_time(self, dataset_id: str, entry_time: datetime | str) -> None:
        value = _normalise_entry_time(entry_time)
        if value is None:
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO learning_state (dataset_id, last_entry_time, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(dataset_id) DO UPDATE SET
                     last_entry_time = excluded.last_entry_time,
                     updated_at = excluded.updated_at""",
                (dataset_id, value, datetime.now(timezone.utc).isoformat()),
            )

    # ------------------------------------------------------------- audit events
    def record_engine_event(
        self,
        pair: str,
        dataset_id: str,
        kind: str,
        detail: str,
        at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO engine_events (pair, dataset_id, kind, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (pair, dataset_id, kind, detail, at or datetime.now(timezone.utc).isoformat()),
            )

    def has_engine_event(
        self,
        kind: str,
        *,
        since: str,
        contains: str | None = None,
        dataset_id: str | None = None,
    ) -> bool:
        conditions = ["kind = ?", "created_at >= ?"]
        parameters: list = [kind, since]
        if contains:
            conditions.append("detail LIKE ?")
            parameters.append(f"%{contains}%")
        if dataset_id:
            conditions.append("dataset_id = ?")
            parameters.append(dataset_id)
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT 1 FROM engine_events WHERE {' AND '.join(conditions)} LIMIT 1""",
                parameters,
            ).fetchone()
        return row is not None

    def list_engine_events(
        self,
        limit: int = 25,
        dataset_id: str | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> list[dict]:
        conditions = []
        parameters: list = []
        if dataset_id:
            conditions.append("dataset_id = ?")
            parameters.append(dataset_id)
        if kinds:
            conditions.append(f"kind IN ({','.join('?' for _ in kinds)})")
            parameters.extend(kinds)
        clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT kind, detail, created_at AS at FROM engine_events{clause}
                   ORDER BY id DESC LIMIT ?""",
                (*parameters, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
