from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbix.env import load_env


class RootEnvFileTests(unittest.TestCase):
    def test_missing_file_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.env"
            with patch.dict(os.environ, {"ORBIX_ENV_FILE": str(missing)}):
                self.assertIsNone(load_env())

    def test_file_loads_without_overriding_real_environment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                "ORBIX_TEST_FROM_FILE=from-file\n"
                "ORBIX_TEST_EXISTING=from-file\n",
                encoding="utf-8",
            )
            environment = {
                "ORBIX_ENV_FILE": str(path),
                "ORBIX_TEST_EXISTING": "from-process",
            }
            with patch.dict(os.environ, environment, clear=False):
                os.environ.pop("ORBIX_TEST_FROM_FILE", None)
                self.assertEqual(load_env(), path)
                self.assertEqual(os.environ["ORBIX_TEST_FROM_FILE"], "from-file")
                self.assertEqual(os.environ["ORBIX_TEST_EXISTING"], "from-process")
                os.environ.pop("ORBIX_TEST_FROM_FILE", None)


if __name__ == "__main__":
    unittest.main()
