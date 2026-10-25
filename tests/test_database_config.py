"""Regression tests for database path resolution and store isolation.

These guard three bugs that were fixed together:

1. `DatabaseConfig` derived `duckdb_path` / `sqlite_path` from `data_dir` only
   inside `__post_init__`, so reassigning `data_dir` afterwards left the stores
   pointing at the previous directory. Every Runtime then silently shared one
   database - which is what made 18 tests fail at once.
2. The store caches in `database/connection.py` were keyed on `data_dir` while
   opening the derived paths, so the key and the target could diverge.
3. `CandleStore.close()` did not evict the cache entry, so a later Runtime was
   handed a store whose DuckDB connection was already closed.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database.config import get_database_config
from orbix.domain import Signal
from orbix.runtime import Runtime


UTC = timezone.utc


def _signal(signal_id: str) -> Signal:
    """A minimal recordable signal (side must not be NO SIGNAL)."""
    moment = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    return Signal(
        signal_id=signal_id,
        pair="EUR/USD",
        strategy="ml_consensus",
        side="BUY",
        entry_price=1.1,
        generated_at=moment,
        expires_at=moment + timedelta(minutes=1),
        probability_up=0.6,
        source="IMPORTED CSV",
        reason="regression fixture",
        entry_time=moment,
    )


DERIVED_FIELDS = (
    "duckdb_path",
    "sqlite_path",
    "sqlite_candles_path",
    "mobile_data_dir",
    "mobile_candles_db",
    "mobile_ledger_db",
)


class DatabaseConfigPathTests(unittest.TestCase):
    def test_a_config_built_with_a_data_dir_derives_paths_under_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "one"
            config = get_database_config({"ORBIX_DATA_DIR": str(target)})
            self.assertEqual(config.data_dir, target.resolve())
            for field in DERIVED_FIELDS:
                path = getattr(config, field)
                self.assertTrue(
                    str(path).startswith(str(config.data_dir)),
                    f"{field}={path} is not under data_dir={config.data_dir}",
                )

    def test_set_data_dir_recomputes_every_derived_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "one"
            second = Path(folder) / "two"
            config = get_database_config({"ORBIX_DATA_DIR": str(first)})
            before = {field: getattr(config, field) for field in DERIVED_FIELDS}

            config.set_data_dir(second)

            self.assertEqual(config.data_dir, second.resolve())
            for field in DERIVED_FIELDS:
                path = getattr(config, field)
                self.assertNotEqual(path, before[field], f"{field} did not change")
                self.assertTrue(
                    str(path).startswith(str(config.data_dir)),
                    f"{field}={path} still points outside {config.data_dir}",
                )


class RuntimeIsolationTests(unittest.TestCase):
    def test_two_runtimes_with_different_data_dirs_do_not_share_a_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = Runtime(data_dir=root / "a")
            second = Runtime(data_dir=root / "b")
            try:
                for runtime in (first, second):
                    self.assertIsNotNone(
                        runtime.db_config,
                        "the database/ package is not importable, so backend "
                        "selection is inactive and this test cannot verify isolation",
                    )
                    self.assertTrue(
                        str(runtime.db_config.duckdb_path).startswith(
                            str(runtime.db_config.data_dir)
                        ),
                        f"candle store {runtime.db_config.duckdb_path} is outside "
                        f"its own data dir {runtime.db_config.data_dir}",
                    )

                self.assertNotEqual(
                    first.db_config.duckdb_path, second.db_config.duckdb_path
                )
                self.assertNotEqual(
                    first.db_config.sqlite_path, second.db_config.sqlite_path
                )

                # Writing through one ledger must not be visible to the other.
                first.ledger.record(_signal("isolation-a"), None, "dataset-a")
                self.assertEqual(len(first.ledger.list_recent()), 1)
                self.assertEqual(second.ledger.list_recent(), [])

                second.ledger.record(_signal("isolation-b"), None, "dataset-b")
                self.assertEqual(len(second.ledger.list_recent()), 1)
                self.assertEqual(
                    [row["signal_id"] for row in first.ledger.list_recent()],
                    ["isolation-a"],
                )
            finally:
                first.candle_store.close()
                second.candle_store.close()


class StoreCacheTests(unittest.TestCase):
    def test_a_closed_candle_store_is_not_handed_back_by_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            first = Runtime(data_dir=data)
            store = first.candle_store
            store.close()
            self.assertTrue(store.closed)
            # close() must be idempotent - Runtime teardown can call it twice.
            store.close()

            # Constructing a second Runtime for the same directory used to raise
            # "Connection already closed!" because the cache returned the dead
            # store during _restore_active_dataset().
            second = Runtime(data_dir=data)
            try:
                self.assertIsNot(second.candle_store, store)
                self.assertFalse(second.candle_store.closed)
                second.candle_store.active_state()
            finally:
                second.candle_store.close()


if __name__ == "__main__":
    unittest.main()
