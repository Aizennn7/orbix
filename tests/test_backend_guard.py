"""Tests for the database backend guard: explicit requests must fail loudly.

Under ``DATABASE_BACKEND=auto`` the fallback chain *is* the feature - pick the
best backend this machine can actually open. But an operator who wrote
``DATABASE_BACKEND=postgres`` and got a local SQLite file instead has something
worse than a crash: two servers that believe they share state when they do not.

Every branch below previously degraded silently. ``psycopg2`` was not declared
in either requirements file, so the Postgres backend could not work from a clean
install at all, and the ImportError was caught and swallowed.

Drivers are forced absent via ``sys.modules[name] = None``, which makes ``import``
raise ``ModuleNotFoundError`` - a subclass of ``ImportError``, so it exercises the
real except clause. That keeps these tests deterministic regardless of which
extras happen to be installed in the environment running them.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from database.config import DatabaseBackend, get_database_config
from database.connection import (
    _backend_is_explicit,
    get_duckdb_connection,
    get_postgres_connection,
    get_supabase_client,
)


def fake_supabase_module(create_client):
    """A stand-in for supabase-py, so tests do not depend on the extra."""
    module = types.ModuleType("supabase")
    module.create_client = create_client
    return module


def fake_psycopg2_module(connect):
    """A stand-in for psycopg2 whose connect() fails on demand."""
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object()
    module = types.ModuleType("psycopg2")
    module.extras = extras
    module.connect = connect
    return module, extras


class BackendIsExplicitTest(unittest.TestCase):
    def test_auto_is_not_explicit(self) -> None:
        config = get_database_config({"ORBIX_DATA_DIR": tempfile.mkdtemp()})
        self.assertEqual(config.backend, DatabaseBackend.AUTO)
        self.assertFalse(_backend_is_explicit(config))

    def test_every_named_backend_is_explicit(self) -> None:
        for backend in (
            DatabaseBackend.SQLITE,
            DatabaseBackend.DUCKDB,
            DatabaseBackend.POSTGRES,
            DatabaseBackend.SUPABASE,
        ):
            with self.subTest(backend=backend.value):
                config = get_database_config(
                    {
                        "ORBIX_DATA_DIR": tempfile.mkdtemp(),
                        "DATABASE_BACKEND": backend.value,
                    }
                )
                self.assertTrue(_backend_is_explicit(config))


class PostgresGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-pg-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def config(self, backend, **extra):
        return get_database_config(
            {
                **os.environ,
                "ORBIX_DATA_DIR": str(self.root),
                "DATABASE_BACKEND": backend,
                **extra,
            }
        )

    def test_explicit_postgres_without_psycopg2_raises(self) -> None:
        config = self.config("postgres", DATABASE_URL="postgres://u:p@localhost:5432/db")
        with patch.dict(sys.modules, {"psycopg2": None, "psycopg2.extras": None}):
            with self.assertRaises(RuntimeError) as ctx:
                get_postgres_connection(config)
        message = str(ctx.exception)
        self.assertIn("postgres", message)
        self.assertIn("psycopg2", message)
        # The message has to carry the fix, not just the complaint.
        self.assertIn("pip install '.[postgres]'", message)

    def test_auto_postgres_without_psycopg2_falls_back_and_warns(self) -> None:
        # DATABASE_URL must be set: get_postgres_connection() short-circuits to
        # SQLite when neither Postgres nor Supabase is configured at all, so
        # without a URL it never reaches the psycopg2 import this is testing.
        # supabase is forced absent too, so the fallback chain is deterministic
        # regardless of which extras the environment happens to have installed.
        config = self.config("auto", DATABASE_URL="postgres://u:p@localhost:5432/db")
        with patch.dict(
            sys.modules,
            {"psycopg2": None, "psycopg2.extras": None, "supabase": None},
        ):
            with self.assertLogs("database.connection", level="WARNING") as captured:
                result = get_postgres_connection(config)
        self.assertIsInstance(result, sqlite3.Connection)
        self.assertIn("psycopg2", "\n".join(captured.output))
        result.close()

    def test_auto_with_nothing_configured_uses_sqlite_without_warning(self) -> None:
        # Pinned deliberately. "auto" means pick the best backend this machine can
        # open, and a deployment with no DATABASE_URL and no Supabase credentials
        # has simply chosen local storage. That is the documented default, not a
        # degraded state, so it must stay quiet - a warning here would train
        # operators to ignore the warnings that do matter.
        config = self.config("auto")
        with self.assertNoLogs("database.connection", level="WARNING"):
            result = get_postgres_connection(config)
        self.assertIsInstance(result, sqlite3.Connection)
        result.close()

    def test_explicit_postgres_connection_failure_does_not_become_sqlite(self) -> None:
        # A reachable driver that cannot connect is the case that matters most in
        # production: a wrong DATABASE_URL or an unreachable host. Falling back to
        # a local file here would hide a misconfiguration behind a working app.
        def boom(*args, **kwargs):
            raise OSError("connection refused")

        module, extras = fake_psycopg2_module(boom)
        config = self.config("postgres", DATABASE_URL="postgres://u:p@localhost:5432/db")
        with patch.dict(sys.modules, {"psycopg2": module, "psycopg2.extras": extras}):
            with self.assertRaises(RuntimeError) as ctx:
                get_postgres_connection(config)
        self.assertIn("connection refused", str(ctx.exception))
        self.assertIn("DATABASE_URL", str(ctx.exception))

    def test_auto_postgres_connection_failure_falls_back(self) -> None:
        def boom(*args, **kwargs):
            raise OSError("connection refused")

        module, extras = fake_psycopg2_module(boom)
        config = self.config("auto", DATABASE_URL="postgres://u:p@localhost:5432/db")
        with patch.dict(sys.modules, {"psycopg2": module, "psycopg2.extras": extras}):
            with self.assertLogs("database.connection", level="WARNING"):
                result = get_postgres_connection(config)
        self.assertIsInstance(result, sqlite3.Connection)
        result.close()


class SupabaseGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-sb-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def config(self, backend, **extra):
        return get_database_config(
            {
                "ORBIX_DATA_DIR": str(self.root),
                "DATABASE_BACKEND": backend,
                **extra,
            }
        )

    def test_explicit_supabase_without_the_library_raises(self) -> None:
        config = self.config("supabase", SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="k")
        with patch.dict(sys.modules, {"supabase": None}):
            with self.assertRaises(RuntimeError) as ctx:
                get_supabase_client(config)
        message = str(ctx.exception)
        self.assertIn("supabase-py", message)
        self.assertIn("pip install '.[supabase]'", message)

    def test_explicit_supabase_without_credentials_raises(self) -> None:
        # The library is present but URL/key are not. Silently using a local file
        # here means cloud sync just never happens, with no indication anywhere.
        config = self.config("supabase")
        module = fake_supabase_module(lambda *a, **k: object())
        with patch.dict(sys.modules, {"supabase": module}):
            with self.assertRaises(RuntimeError) as ctx:
                get_supabase_client(config)
        message = str(ctx.exception)
        self.assertIn("SUPABASE_URL", message)
        self.assertIn("SUPABASE_ANON_KEY", message)

    def test_auto_supabase_without_the_library_falls_back_and_warns(self) -> None:
        config = self.config("auto")
        with patch.dict(sys.modules, {"supabase": None}):
            with self.assertLogs("database.connection", level="WARNING") as captured:
                result = get_supabase_client(config)
        self.assertIsInstance(result, sqlite3.Connection)
        self.assertIn("supabase-py", "\n".join(captured.output))
        result.close()

    def test_explicit_supabase_client_failure_raises(self) -> None:
        def boom(*args, **kwargs):
            raise ValueError("bad project url")

        config = self.config(
            "supabase", SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="k"
        )
        module = fake_supabase_module(boom)
        with patch.dict(sys.modules, {"supabase": module}):
            with self.assertRaises(RuntimeError) as ctx:
                get_supabase_client(config)
        self.assertIn("bad project url", str(ctx.exception))


class DuckDBGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-duck-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def config(self, backend):
        return get_database_config(
            {"ORBIX_DATA_DIR": str(self.root), "DATABASE_BACKEND": backend}
        )

    def test_explicit_duckdb_without_the_driver_raises(self) -> None:
        config = self.config("duckdb")
        with patch.dict(sys.modules, {"duckdb": None}):
            with self.assertRaises(RuntimeError) as ctx:
                get_duckdb_connection(config)
        message = str(ctx.exception)
        self.assertIn("duckdb", message)
        self.assertIn("requirements.txt", message)

    def test_auto_duckdb_without_the_driver_falls_back_and_warns(self) -> None:
        config = self.config("auto")
        with patch.dict(sys.modules, {"duckdb": None}):
            with self.assertLogs("database.connection", level="WARNING") as captured:
                result = get_duckdb_connection(config)
        self.assertIsInstance(result, sqlite3.Connection)
        self.assertIn("DuckDB", "\n".join(captured.output))
        result.close()


class BackendNameValidationTest(unittest.TestCase):
    """An unrecognised DATABASE_BACKEND must not silently mean 'auto'."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orbix-name-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def build(self, **extra):
        return get_database_config({"ORBIX_DATA_DIR": str(self.root), **extra})

    def test_unset_defaults_to_auto(self) -> None:
        self.assertEqual(self.build().backend, DatabaseBackend.AUTO)

    def test_every_valid_value_parses(self) -> None:
        for backend in DatabaseBackend:
            with self.subTest(backend=backend.value):
                self.assertEqual(
                    self.build(DATABASE_BACKEND=backend.value).backend, backend
                )

    def test_value_is_case_insensitive(self) -> None:
        self.assertEqual(
            self.build(DATABASE_BACKEND="Postgres").backend, DatabaseBackend.POSTGRES
        )

    def test_alias_variable_is_honoured(self) -> None:
        self.assertEqual(
            self.build(ORBIX_DATABASE_BACKEND="sqlite").backend, DatabaseBackend.SQLITE
        )

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        # Every other env value in get_database_config is stripped. A stray space
        # must not turn a valid backend into a startup failure.
        self.assertEqual(
            self.build(DATABASE_BACKEND="  postgres ").backend, DatabaseBackend.POSTGRES
        )

    def test_typo_raises_instead_of_becoming_auto(self) -> None:
        # "postgress" used to resolve to AUTO, which starts a local SQLite file
        # while the operator believes two servers share Postgres state.
        for bad in ("postgress", "sqlite3", "nope", "AUTO_DETECT", "duck-db"):
            with self.subTest(value=bad):
                with self.assertRaises(RuntimeError) as ctx:
                    self.build(DATABASE_BACKEND=bad)
                message = str(ctx.exception)
                self.assertIn(bad.strip().lower(), message.lower())
                # The error must list what would have worked.
                self.assertIn("auto", message)
                self.assertIn("postgres", message)

    def test_empty_string_is_treated_as_unset(self) -> None:
        # docker-compose passes ${DATABASE_BACKEND:-auto}, and a .env with
        # "DATABASE_BACKEND=" is a common way of commenting a value out.
        self.assertEqual(self.build(DATABASE_BACKEND="").backend, DatabaseBackend.AUTO)


if __name__ == "__main__":
    unittest.main()
