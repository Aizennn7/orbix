from __future__ import annotations

from collections import deque
import csv
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import threading
import uuid

import duckdb

from .domain import Candle, parse_datetime


IMPORT_CHUNK_ROWS = 50_000
TAIL_ROWS = 10_000
TIMEFRAME = "M1"


class CandleStore:
    """Persistent, chunked candle archive backed by a local DuckDB file.

    The full history stays on disk. A small active tail is kept separately for
    low-latency signal generation and bounded-memory research windows.
    """

    def __init__(self, database: str | Path, tail_rows: int = TAIL_ROWS):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.tail_rows = max(720, int(tail_rows))
        self._lock = threading.RLock()
        self._connection = duckdb.connect(str(self.database))
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id VARCHAR PRIMARY KEY,
                    pair VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    candle_count BIGINT NOT NULL DEFAULT 0,
                    first_timestamp TIMESTAMP,
                    latest_timestamp TIMESTAMP,
                    content_sha256 VARCHAR NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS candles (
                    dataset_id VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS candle_tail (
                    dataset_id VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS active_dataset (
                    singleton INTEGER PRIMARY KEY,
                    dataset_id VARCHAR,
                    pair VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TEMP TABLE IF NOT EXISTS import_batch (
                    timestamp TIMESTAMP NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL
                )"""
            )

    @staticmethod
    def _canonical(candle: Candle) -> str:
        return (
            f"{candle.timestamp.isoformat()}|{candle.open:.15g}|{candle.high:.15g}|"
            f"{candle.low:.15g}|{candle.close:.15g}|{candle.volume:.15g}\n"
        )

    @staticmethod
    def _normalise_row(raw: dict) -> dict[str, str | None]:
        return {(key or "").strip().lower(): value for key, value in raw.items()}

    @staticmethod
    def _validate_header(reader: csv.DictReader) -> None:
        if reader.fieldnames is None:
            raise ValueError("CSV is empty or has no header row")
        columns = {name.strip().lower() for name in reader.fieldnames if name}
        if not {"open", "high", "low", "close"}.issubset(columns):
            raise ValueError("CSV needs open, high, low, and close columns")
        if not ({"timestamp", "datetime", "time", "date"} & columns):
            raise ValueError("CSV needs a timestamp, time, or date column")

    def _copy_batch(self, csv_path: Path, dataset_id: str, *, append_to_tail: bool) -> None:
        connection = self._connection
        connection.execute("COPY import_batch FROM ? (HEADER, DELIMITER ',')", [str(csv_path)])
        connection.execute(
            """INSERT INTO candles
               SELECT ?, timestamp, open, high, low, close, volume FROM import_batch""",
            [dataset_id],
        )
        if append_to_tail:
            connection.execute(
                """INSERT INTO candle_tail
                   SELECT ?, timestamp, open, high, low, close, volume FROM import_batch""",
                [dataset_id],
            )
            connection.execute(
                """DELETE FROM candle_tail
                   WHERE dataset_id = ? AND timestamp NOT IN (
                       SELECT timestamp FROM candle_tail
                       WHERE dataset_id = ? ORDER BY timestamp DESC LIMIT ?
                   )""",
                [dataset_id, dataset_id, self.tail_rows],
            )
        connection.execute("DELETE FROM import_batch")

    def _stream_csv(
        self,
        csv_path: str | Path,
        dataset_id: str,
        *,
        minimum: int,
        previous_timestamp: datetime | None = None,
        append_to_tail: bool,
        seed_digest: str = "",
    ) -> dict:
        path = Path(csv_path)
        digest = hashlib.sha256()
        if seed_digest:
            digest.update(seed_digest.encode("ascii", errors="ignore"))
        recent: deque[Candle] = deque(maxlen=self.tail_rows)
        first: Candle | None = None
        latest: Candle | None = None
        previous = parse_datetime(previous_timestamp) if previous_timestamp is not None else None
        count = 0
        chunk_count = 0
        stage_path: Path | None = None

        try:
            self._connection.execute("DELETE FROM import_batch")
            with path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                self._validate_header(reader)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="", suffix=".csv",
                    prefix=".orbix-candle-batch-", dir=self.database.parent, delete=False,
                ) as stage:
                    stage_path = Path(stage.name)
                    writer = csv.writer(stage)
                    header = ["timestamp", "open", "high", "low", "close", "volume"]
                    writer.writerow(header)

                    def flush_batch() -> None:
                        nonlocal chunk_count
                        if not chunk_count:
                            return
                        stage.flush()
                        self._copy_batch(stage_path, dataset_id, append_to_tail=append_to_tail)
                        stage.seek(0)
                        stage.truncate(0)
                        writer.writerow(header)
                        chunk_count = 0

                    for line_number, raw in enumerate(reader, start=2):
                        row = self._normalise_row(raw)
                        if not any(value not in (None, "") for value in row.values()):
                            continue
                        try:
                            candle = Candle.from_mapping(row)
                        except ValueError as exc:
                            raise ValueError(f"CSV line {line_number}: {exc}") from exc
                        if previous is not None and candle.timestamp <= previous:
                            raise ValueError(
                                f"CSV line {line_number}: candle timestamps must be unique and in ascending order"
                            )
                        previous = candle.timestamp
                        first = first or candle
                        latest = candle
                        count += 1
                        chunk_count += 1
                        recent.append(candle)
                        digest.update(self._canonical(candle).encode("utf-8"))
                        writer.writerow(
                            [
                                candle.timestamp.replace(tzinfo=None).isoformat(sep="T"),
                                format(candle.open, ".17g"),
                                format(candle.high, ".17g"),
                                format(candle.low, ".17g"),
                                format(candle.close, ".17g"),
                                format(candle.volume, ".17g"),
                            ]
                        )
                        if chunk_count >= IMPORT_CHUNK_ROWS:
                            flush_batch()

                    flush_batch()

            if count < minimum:
                raise ValueError(f"At least {minimum} candles are required")
            if first is None or latest is None:
                raise ValueError("CSV contains no candle rows")
            return {
                "count": count,
                "first": first,
                "latest": latest,
                "recent": list(recent),
                "content_sha256": digest.hexdigest(),
            }
        finally:
            if stage_path is not None:
                stage_path.unlink(missing_ok=True)

    def import_csv_file(self, csv_path: str | Path, pair: str, minimum: int = 120) -> dict:
        cleaned_pair = pair.upper().strip() or "CSV/UNKNOWN"
        dataset_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """INSERT INTO datasets
                       (dataset_id, pair, timeframe, candle_count, created_at, updated_at)
                       VALUES (?, ?, ?, 0, ?, ?)""",
                    [dataset_id, cleaned_pair, TIMEFRAME, now, now],
                )
                result = self._stream_csv(
                    csv_path, dataset_id, minimum=minimum, append_to_tail=False
                )
                connection.execute(
                    """UPDATE datasets SET candle_count = ?, first_timestamp = ?, latest_timestamp = ?,
                           content_sha256 = ?, updated_at = ? WHERE dataset_id = ?""",
                    [
                        result["count"],
                        result["first"].timestamp.replace(tzinfo=None),
                        result["latest"].timestamp.replace(tzinfo=None),
                        result["content_sha256"],
                        datetime.now(timezone.utc).replace(tzinfo=None),
                        dataset_id,
                    ],
                )
                connection.execute("DELETE FROM candle_tail")
                connection.execute(
                    """INSERT INTO candle_tail
                       SELECT dataset_id, timestamp, open, high, low, close, volume
                       FROM candles WHERE dataset_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    [dataset_id, self.tail_rows],
                )
                connection.execute("DELETE FROM active_dataset WHERE singleton = 1")
                connection.execute(
                    """INSERT INTO active_dataset (singleton, dataset_id, pair, source, updated_at)
                       VALUES (1, ?, ?, 'IMPORTED CSV', ?)""",
                    [dataset_id, cleaned_pair, datetime.now(timezone.utc).replace(tzinfo=None)],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        result.update({"dataset_id": dataset_id, "pair": cleaned_pair, "source": "IMPORTED CSV"})
        return result

    def append_csv_file(self, csv_path: str | Path, pair: str, minimum: int = 1) -> dict:
        cleaned_pair = pair.upper().strip()
        with self._lock:
            active = self._active_row()
            if not active or active["source"] != "IMPORTED CSV" or not active["dataset_id"]:
                raise ValueError("Import a CSV dataset before appending candles")
            if cleaned_pair and cleaned_pair != active["pair"]:
                raise ValueError(f"The active candle dataset is for {active['pair']}")
            dataset_id = active["dataset_id"]
            dataset = self._connection.execute(
                "SELECT latest_timestamp, candle_count, content_sha256 FROM datasets WHERE dataset_id = ?",
                [dataset_id],
            ).fetchone()
            if dataset is None:
                raise ValueError("The active candle dataset could not be found")

            connection = self._connection
            connection.execute("BEGIN TRANSACTION")
            try:
                result = self._stream_csv(
                    csv_path,
                    dataset_id,
                    minimum=minimum,
                    previous_timestamp=dataset[0],
                    append_to_tail=True,
                    seed_digest=dataset[2],
                )
                connection.execute(
                    """UPDATE datasets SET candle_count = candle_count + ?, latest_timestamp = ?,
                           content_sha256 = ?, updated_at = ? WHERE dataset_id = ?""",
                    [
                        result["count"],
                        result["latest"].timestamp.replace(tzinfo=None),
                        result["content_sha256"],
                        datetime.now(timezone.utc).replace(tzinfo=None),
                        dataset_id,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

            result.update({
                "dataset_id": dataset_id,
                "pair": active["pair"],
                "source": "IMPORTED CSV",
                "candle_count": int(dataset[1]) + result["count"],
            })
            return result

    def _active_row(self) -> dict | None:
        row = self._connection.execute(
            "SELECT dataset_id, pair, source, updated_at FROM active_dataset WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "dataset_id": row[0],
            "pair": row[1],
            "source": row[2],
            "updated_at": parse_datetime(row[3]),
        }

    def active_state(self) -> dict | None:
        with self._lock:
            active = self._active_row()
            if active and active["dataset_id"]:
                row = self._connection.execute(
                    "SELECT candle_count, first_timestamp, latest_timestamp, content_sha256 "
                    "FROM datasets WHERE dataset_id = ?",
                    [active["dataset_id"]],
                ).fetchone()
                if row:
                    active.update({
                        "candle_count": int(row[0]),
                        "first_timestamp": parse_datetime(row[1]) if row[1] else None,
                        "latest_timestamp": parse_datetime(row[2]) if row[2] else None,
                        "content_sha256": row[3],
                    })
            return active

    def activate_dataset(self, dataset_id: str) -> dict:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            dataset = self._connection.execute(
                "SELECT pair, candle_count FROM datasets WHERE dataset_id = ?",
                [dataset_id],
            ).fetchone()
            if dataset is None:
                raise ValueError("Archived candle dataset was not found")
            self._connection.execute("BEGIN TRANSACTION")
            try:
                self._connection.execute("DELETE FROM candle_tail")
                self._connection.execute(
                    """INSERT INTO candle_tail
                       SELECT dataset_id, timestamp, open, high, low, close, volume
                       FROM candles WHERE dataset_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    [dataset_id, self.tail_rows],
                )
                self._connection.execute("DELETE FROM active_dataset WHERE singleton = 1")
                self._connection.execute(
                    """INSERT INTO active_dataset (singleton, dataset_id, pair, source, updated_at)
                       VALUES (1, ?, ?, 'IMPORTED CSV', ?)""",
                    [dataset_id, dataset[0], now],
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            return {"dataset_id": dataset_id, "pair": dataset[0], "candle_count": int(dataset[1]), "source": "IMPORTED CSV"}

    def set_active_demo(self, pair: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._connection.execute("BEGIN TRANSACTION")
            try:
                self._connection.execute("DELETE FROM active_dataset WHERE singleton = 1")
                self._connection.execute("DELETE FROM candle_tail")
                self._connection.execute(
                    """INSERT INTO active_dataset (singleton, dataset_id, pair, source, updated_at)
                       VALUES (1, NULL, ?, 'SYNTHETIC DEMO', ?)""",
                    [pair, now],
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def load_latest(self, dataset_id: str, limit: int = TAIL_ROWS) -> list[Candle]:
        limit = max(1, min(int(limit), self.tail_rows))
        with self._lock:
            rows = self._connection.execute(
                """SELECT timestamp, open, high, low, close, volume FROM candle_tail
                   WHERE dataset_id = ? ORDER BY timestamp DESC LIMIT ?""",
                [dataset_id, limit],
            ).fetchall()
        return [
            Candle(timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
            for row in reversed(rows)
        ]

    def get_candle(self, dataset_id: str, timestamp: datetime) -> Candle | None:
        target = parse_datetime(timestamp).replace(tzinfo=None)
        with self._lock:
            row = self._connection.execute(
                """SELECT timestamp, open, high, low, close, volume FROM candles
                   WHERE dataset_id = ? AND timestamp = ? LIMIT 1""",
                [dataset_id, target],
            ).fetchone()
        if row is None:
            return None
        return Candle(timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])

    def summary(self) -> dict:
        with self._lock:
            row = self._connection.execute(
                "SELECT count(*), coalesce(sum(candle_count), 0) FROM datasets"
            ).fetchone()
            active = self.active_state()
            size = self.database.stat().st_size if self.database.exists() else 0
            return {
                "engine": "DuckDB",
                "path": str(self.database),
                "datasets_archived": int(row[0]),
                "candles_archived": int(row[1]),
                "active_dataset_id": active["dataset_id"] if active else None,
                "active_pair": active["pair"] if active else None,
                "active_source": active["source"] if active else "SYNTHETIC DEMO",
                "active_candles": int(active.get("candle_count", 0)) if active else 0,
                "database_bytes": size,
                "tail_rows": self.tail_rows,
            }

    def list_datasets(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT dataset_id, pair, timeframe, candle_count, first_timestamp,
                          latest_timestamp, content_sha256, created_at, updated_at
                   FROM datasets ORDER BY created_at DESC LIMIT ?""",
                [max(1, min(int(limit), 500))],
            ).fetchall()
        names = (
            "dataset_id", "pair", "timeframe", "candle_count", "first_timestamp",
            "latest_timestamp", "content_sha256", "created_at", "updated_at",
        )
        with self._lock:
            active = self._active_row()
        active_id = active["dataset_id"] if active else None
        results = [dict(zip(names, row)) for row in rows]
        for item in results:
            item["active"] = item["dataset_id"] == active_id
            for timestamp_field in ("first_timestamp", "latest_timestamp", "created_at", "updated_at"):
                if item[timestamp_field] is not None:
                    item[timestamp_field] = parse_datetime(item[timestamp_field])
        return results

    def close(self) -> None:
        with self._lock:
            self._connection.close()
