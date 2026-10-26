"""Regression tests for stores whose data directory disappears at runtime.

Found in production, not in review: ``/api/preflight`` began returning HTTP 500
with ``sqlite3.OperationalError("unable to open database file")`` and never
recovered. The data directory had been deleted while the server was running.

Every store created its parent directory once, in ``__init__``, and never looked
again. For a process that starts, opens its database and runs, that is correct.
For a long-lived server it is not: a volume remount, a cleanup job or an
operator's ``rm -rf`` removes the directory, and from that moment every request
fails until the process is restarted. ``/healthz`` kept returning 200 the whole
time, so uptime monitoring stayed green while the app served nothing.

The DuckDB path failed differently, and worse. ``CandleStore`` opens one
connection in ``__init__`` and reuses it at every query site, so a deleted file
leaves the handle writing to an unlinked inode: no exception, and every candle
written afterwards silently discarded when the process exits. ``get_candle_store``
now treats a vanished database file the way it already treated a closed store -
evict and rebuild.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database.connection as db_connection
from database.config import get_database_config
from orbix.ledger import PaperLedger
from orbix.mobile_db import MobileCandleStore, MobileLedger
from orbix.storage_guard import ensure_parent


def table_names(store) -> set[str]:
    connection = store._connect()
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows}
    finally:
        connection.close()


class EnsureParentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-guard-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_returns_false_when_the_directory_exists(self) -> None:
        data = self.root / "data"
        data.mkdir(parents=True)
        self.assertFalse(ensure_parent(data / "ledger.sqlite3", "Test"))

    def test_recreates_a_missing_directory_and_returns_true(self) -> None:
        data = self.root / "data" / "nested"
        self.assertFalse(data.exists())
        self.assertTrue(ensure_parent(data / "ledger.sqlite3", "Test"))
        self.assertTrue(data.is_dir())

    def test_names_the_store_that_lost_its_directory(self) -> None:
        # Several stores can share one data dir, so the log line has to say which
        # one recovered or the warning is not actionable.
        with self.assertLogs("orbix.storage_guard", level="WARNING") as captured:
            ensure_parent(self.root / "data" / "ledger.sqlite3", "PaperLedger")
        self.assertIn("PaperLedger", "\n".join(captured.output))


class StoreSelfHealTest(unittest.TestCase):
    """Each store must survive its data directory being deleted underneath it."""

    # (class, filename, a public call that touches the schema)
    stores = (
        (PaperLedger, "ledger.sqlite3", lambda s: s.list_recent(limit=1)),
        (MobileCandleStore, "candles.sqlite3", lambda s: s.list_datasets()),
        (MobileLedger, "mobile.sqlite3", lambda s: s.get_signals(limit=1)),
    )

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-heal-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_recovers_after_the_directory_is_deleted(self) -> None:
        for cls, filename, public_call in self.stores:
            with self.subTest(store=cls.__name__):
                data = self.root / cls.__name__
                database = data / filename
                store = cls(database)
                before = table_names(store)
                self.assertTrue(before, f"{cls.__name__} created no schema")
                public_call(store)

                shutil.rmtree(data)
                self.assertFalse(database.parent.is_dir())

                # The old behaviour raised OperationalError here, on every call,
                # forever. This must both return and recreate the directory.
                self.assertEqual(public_call(store), [])
                self.assertTrue(
                    data.is_dir(), f"{cls.__name__} did not recreate its directory"
                )

    def test_revived_schema_matches_the_original(self) -> None:
        # Recreating the directory is not enough: the new database file is empty,
        # so without a schema rebuild every query would trade one exception for
        # another - "no such table" instead of "unable to open database file".
        for cls, filename, _ in self.stores:
            with self.subTest(store=cls.__name__):
                data = self.root / f"schema-{cls.__name__}"
                store = cls(data / filename)
                before = table_names(store)

                shutil.rmtree(data)
                table_names(store)

                self.assertEqual(
                    table_names(store),
                    before,
                    f"{cls.__name__} revived with a different schema",
                )


class ReviveRecursionTest(unittest.TestCase):
    def test_revive_flag_bounds_recursion(self) -> None:
        # mkdir reporting success while is_dir() still fails is a permissions or
        # stale-mount case. Without the _reviving flag, _connect() would call
        # _initialize(), which calls _connect(), forever - inside a request
        # handler, where a RecursionError is indistinguishable from a hang.
        root = Path(tempfile.mkdtemp(prefix="orbix-recur-"))
        self.addCleanup(shutil.rmtree, root, True)
        store = PaperLedger(root / "ledger.sqlite3")

        with patch("orbix.ledger.ensure_parent", return_value=True):
            connection = store._connect()
        connection.close()

        self.assertFalse(
            store._reviving, "flag left set, so a later real revive would be skipped"
        )


class OrphanedCandleStoreTest(unittest.TestCase):
    """A DuckDB store whose file vanished must be evicted, not handed back."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-duck-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(db_connection._candle_store_cache.clear)
        db_connection._candle_store_cache.clear()

    def config(self):
        return get_database_config(
            {
                **os.environ,
                "ORBIX_DATA_DIR": str(self.root),
                "DATABASE_BACKEND": "duckdb",
            }
        )

    def test_rebuilds_instead_of_writing_to_an_unlinked_file(self) -> None:
        config = self.config()
        first = db_connection.get_candle_store(config)
        self.assertTrue(Path(first.database).exists())

        # The cache must still work, or this test would pass for the wrong reason.
        self.assertIs(db_connection.get_candle_store(config), first)

        shutil.rmtree(self.root)
        self.assertFalse(Path(first.database).exists())

        with self.assertLogs("database.connection", level="WARNING") as captured:
            second = db_connection.get_candle_store(config)

        self.assertIsNot(
            second, first, "orphaned store was handed back: writes go to nothing"
        )
        self.assertTrue(
            Path(second.database).exists(), "rebuilt store has no database file"
        )
        self.assertTrue(
            getattr(first, "closed", False), "orphaned DuckDB handle was left open"
        )
        self.assertIn("disappeared", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
