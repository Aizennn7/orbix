from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

from .abstentions import REASON_LABELS, SESSION_ORDER, classify_reason, hour_bucket, session_for
from .metrics import wilson_interval


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
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO learning_events
                   (signal_id, pair, dataset_id, label, probability_up, correct, model_updated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal_id,
                    pair,
                    dataset_id,
                    label,
                    probability_up,
                    None if correct is None else int(correct),
                    int(model_updated),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def learning_summary(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(model_updated), 0),
                          AVG(CASE WHEN model_updated = 1 THEN correct END), MAX(created_at),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct = 1 THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN model_updated = 1 AND correct IS NOT NULL THEN 1 ELSE 0 END), 0)
                   FROM learning_events"""
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
        }

    def learning_history(self, limit: int = 600) -> list[dict]:
        """Resolved predictions in chronological order, for tracker hydration."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT probability_up, label, correct, model_updated, created_at FROM learning_events
                   WHERE probability_up IS NOT NULL AND label IN (0, 1)
                   ORDER BY created_at DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def learning_replay(self, limit: int = 200) -> list[dict]:
        """Stored feature rows and labels for guarded replay checks."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT le.label, le.probability_up, s.features_json
                   FROM learning_events le JOIN signals s ON s.signal_id = le.signal_id
                   WHERE le.label IN (0, 1) AND s.features_json NOT IN ('', '{}')
                   ORDER BY le.created_at DESC LIMIT ?""",
                (max(1, int(limit)),),
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
        signal_filter = " WHERE s.dataset_id = ?" if dataset_id else ""
        outcome_filter = " WHERE dataset_id = ? AND status = 'RESOLVED'" if dataset_id else " WHERE status = 'RESOLVED'"
        parameters = (dataset_id,) if dataset_id else ()
        with self._connect() as connection:
            learning_rows = connection.execute(
                f"""SELECT s.strategy AS strategy, COUNT(*) AS resolved,
                          COALESCE(SUM(CASE WHEN le.correct IS NOT NULL THEN 1 ELSE 0 END), 0) AS evaluated,
                          COALESCE(SUM(CASE WHEN le.correct = 1 THEN 1 ELSE 0 END), 0) AS correct,
                          COALESCE(SUM(le.model_updated), 0) AS updates,
                          AVG(CASE WHEN le.correct IS NOT NULL THEN le.probability_up END) AS mean_probability
                   FROM learning_events le JOIN signals s ON s.signal_id = le.signal_id{signal_filter}
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
