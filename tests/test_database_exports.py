"""Guard the `database` package's public surface against silent drift.

Both bugs this prevents were invisible in production:

* ``orbix/api.py`` wrote ``from database import check_database_health`` but
  ``database/__init__.py`` did not re-export it. The call site wraps the import
  in ``except Exception``, so /api/storage returned a ``database_config_error``
  string instead of a health block - a 200 response with silently missing data.
* ``orbix/mobile_api.py`` imported ``get_config`` the same way and fell back to
  ``_root_db_config = None``.

Neither raised, neither failed a test, and both shipped. The tests below fail
the build the moment a name is imported from ``database`` that ``database`` does
not actually export, so the drift is caught at commit time instead of being
discovered in a published snapshot.
"""
from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories whose Python is shipped or exercised. .venv is excluded because it
# holds third-party packages that legitimately import whatever they like.
SCAN_DIRS = ("orbix", "database", "scripts", "tests")


def _python_files():
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            yield path


def _names_imported_from_database():
    """Every name any module pulls in via ``from database import ...``."""
    found = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - would fail elsewhere first
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "database":
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    found.setdefault(alias.name, []).append(
                        f"{path.relative_to(ROOT)}:{node.lineno}"
                    )
    return found


class DatabaseExportTests(unittest.TestCase):
    def test_database_package_imports(self):
        """The package itself must import cleanly - no partial initialisation."""
        module = importlib.import_module("database")
        self.assertTrue(hasattr(module, "__all__"))
        self.assertGreater(len(module.__all__), 0)

    def test_every_imported_name_is_actually_exported(self):
        """The regression that shipped: an import that only fails at runtime."""
        module = importlib.import_module("database")
        missing = {}
        for name, sites in _names_imported_from_database().items():
            if not hasattr(module, name):
                missing[name] = sites
        self.assertEqual(
            missing,
            {},
            "database/__init__.py does not re-export names that the codebase "
            f"imports: {missing}. Both known call sites swallow the resulting "
            "ImportError, so this degrades silently rather than crashing.",
        )

    def test_dunder_all_is_importable_and_complete(self):
        """__all__ must not advertise names that do not resolve."""
        module = importlib.import_module("database")
        for name in module.__all__:
            self.assertTrue(
                hasattr(module, name),
                f"database.__all__ advertises {name!r} but it is not importable",
            )

        imported = set(_names_imported_from_database())
        advertised = set(module.__all__)
        self.assertEqual(
            imported - advertised,
            set(),
            "Names imported from `database` but absent from __all__. Add them so "
            "the public surface stays honest.",
        )

    def test_no_name_is_exported_from_nowhere(self):
        """Each exported name must come from config or connection, as documented."""
        module = importlib.import_module("database")
        for name in module.__all__:
            owner = getattr(module, name)
            origin = getattr(owner, "__module__", "")
            self.assertTrue(
                origin.startswith("database."),
                f"{name!r} resolves to {origin!r}, not a database submodule",
            )

    def test_orbix_database_shim_still_resolves(self):
        """orbix.database re-exports the same surface; keep the two in step."""
        shim = importlib.import_module("orbix.database")
        package = importlib.import_module("database")
        for name in getattr(shim, "__all__", []):
            self.assertTrue(
                hasattr(shim, name),
                f"orbix.database.__all__ advertises {name!r} but it is missing",
            )
            if name in package.__all__:
                self.assertIs(
                    getattr(shim, name),
                    getattr(package, name),
                    f"orbix.database.{name} is not the same object as database.{name}",
                )


if __name__ == "__main__":
    unittest.main()
