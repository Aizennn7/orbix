from __future__ import annotations

import base64
import os
from unittest.mock import patch
import unittest

from orbix.security import basic_auth_allowed, dashboard_credentials


def _header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


class DashboardAuthTests(unittest.TestCase):
    def test_auth_is_disabled_without_a_password(self) -> None:
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD": "", "ORBIX_DASHBOARD_USER": ""}, clear=False):
            self.assertIsNone(dashboard_credentials())
            self.assertTrue(basic_auth_allowed(None))
            self.assertTrue(basic_auth_allowed("Basic anything"))

    def test_a_password_enables_basic_auth(self) -> None:
        with patch.dict(
            os.environ,
            {"ORBIX_DASHBOARD_PASSWORD": "correct horse", "ORBIX_DASHBOARD_USER": "trader"},
            clear=False,
        ):
            self.assertEqual(dashboard_credentials(), ("trader", "correct horse"))
            self.assertTrue(basic_auth_allowed(_header("trader", "correct horse")))
            self.assertFalse(basic_auth_allowed(_header("trader", "wrong")))
            self.assertFalse(basic_auth_allowed(_header("other", "correct horse")))
            self.assertFalse(basic_auth_allowed(None))
            self.assertFalse(basic_auth_allowed("Bearer token"))
            self.assertFalse(basic_auth_allowed("Basic not-base64!"))
            self.assertFalse(basic_auth_allowed("Basic " + base64.b64encode(b"no-colon").decode()))

    def test_the_default_user_is_orbix(self) -> None:
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD": "secret", "ORBIX_DASHBOARD_USER": ""}, clear=False):
            self.assertEqual(dashboard_credentials(), ("orbix", "secret"))
            self.assertTrue(basic_auth_allowed(_header("orbix", "secret")))


if __name__ == "__main__":
    unittest.main()
