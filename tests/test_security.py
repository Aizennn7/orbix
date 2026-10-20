from __future__ import annotations

import base64
import os
from unittest.mock import patch
import unittest

from orbix.security import (
    auth_configured,
    basic_auth_allowed,
    dashboard_credentials,
    hash_password,
    password_hash_error,
    verify_password,
)


def _header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


AUTH_ENV = {"ORBIX_DASHBOARD_PASSWORD": "", "ORBIX_DASHBOARD_PASSWORD_HASH": "", "ORBIX_DASHBOARD_USER": ""}


class DashboardAuthTests(unittest.TestCase):
    def test_auth_is_disabled_without_a_password(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            self.assertFalse(auth_configured())
            self.assertIsNone(dashboard_credentials())
            self.assertTrue(basic_auth_allowed(None))
            self.assertTrue(basic_auth_allowed("Basic anything"))

    def test_a_password_enables_basic_auth(self) -> None:
        with patch.dict(
            os.environ,
            {"ORBIX_DASHBOARD_PASSWORD": "correct horse", "ORBIX_DASHBOARD_USER": "trader"},
            clear=False,
        ):
            self.assertTrue(auth_configured())
            self.assertEqual(dashboard_credentials(), ("trader", "correct horse"))
            self.assertTrue(basic_auth_allowed(_header("trader", "correct horse")))
            self.assertFalse(basic_auth_allowed(_header("trader", "wrong")))
            self.assertFalse(basic_auth_allowed(_header("other", "correct horse")))
            self.assertFalse(basic_auth_allowed(None))
            self.assertFalse(basic_auth_allowed("Bearer token"))
            self.assertFalse(basic_auth_allowed("Basic not-base64!"))
            self.assertFalse(basic_auth_allowed("Basic " + base64.b64encode(b"no-colon").decode()))
            non_ascii_user = "Basic " + base64.b64encode("träder:correct horse".encode("utf-8")).decode("ascii")
            self.assertFalse(basic_auth_allowed(non_ascii_user))
            self.assertFalse(basic_auth_allowed(_header("trader", "pässword")))

    def test_the_default_user_is_orbix(self) -> None:
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD": "secret", "ORBIX_DASHBOARD_USER": ""}, clear=False):
            self.assertEqual(dashboard_credentials(), ("orbix", "secret"))
            self.assertTrue(basic_auth_allowed(_header("orbix", "secret")))

    def test_a_hash_alone_enables_auth(self) -> None:
        encoded = hash_password("longer secret", iterations=10_000)
        with patch.dict(
            os.environ,
            {"ORBIX_DASHBOARD_PASSWORD": "", "ORBIX_DASHBOARD_PASSWORD_HASH": encoded, "ORBIX_DASHBOARD_USER": "trader"},
            clear=False,
        ):
            self.assertTrue(auth_configured())
            self.assertIsNone(dashboard_credentials())
            self.assertTrue(verify_password("longer secret"))
            self.assertFalse(verify_password("longer secre"))
            self.assertTrue(basic_auth_allowed(_header("trader", "longer secret")))
            self.assertFalse(basic_auth_allowed(_header("trader", "longer secre")))

    def test_the_hash_wins_over_plaintext(self) -> None:
        encoded = hash_password("hashed value", iterations=10_000)
        with patch.dict(
            os.environ,
            {"ORBIX_DASHBOARD_PASSWORD": "plain value", "ORBIX_DASHBOARD_PASSWORD_HASH": encoded},
            clear=False,
        ):
            self.assertTrue(verify_password("hashed value"))
            self.assertFalse(verify_password("plain value"))

    def test_malformed_hash_values_are_rejected(self) -> None:
        for value in (
            "plaintext",
            "bcrypt$10000$c2FsdA==$ZGlnZXN0",
            "pbkdf2_sha256$100$c2FsdA==$ZGlnZXN0",
            "pbkdf2_sha256$10000$not base64!$ZGlnZXN0",
            "pbkdf2_sha256$10000$c2FsdA==$not base64!",
            "pbkdf2_sha256$10000$c2FsdA==",
        ):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"ORBIX_DASHBOARD_PASSWORD_HASH": value, "ORBIX_DASHBOARD_PASSWORD": ""},
                    clear=False,
                ):
                    self.assertFalse(verify_password("anything"))

    def test_hash_validation_catches_a_lockout_configuration(self) -> None:
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD_HASH": ""}, clear=False):
            self.assertIsNone(password_hash_error())
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD_HASH": hash_password("valid", iterations=10_000)}, clear=False):
            self.assertIsNone(password_hash_error())
        cases = {
            "plaintext": "expected",
            "bcrypt$10000$c2FsdA==$ZGlnZXN0": "unsupported",
            "pbkdf2_sha256$nope$c2FsdA==$ZGlnZXN0": "integer",
            "pbkdf2_sha256$100$c2FsdA==$ZGlnZXN0": "between",
            "pbkdf2_sha256$10000$bad!$ZGlnZXN0": "base64",
            "pbkdf2_sha256$10000$c2FsdA==$bad!": "base64",
            "pbkdf2_sha256$10000$c2Fs$ZGlnZXN0": "too short",
            "pbkdf2_sha256$10000$c2FsdA==$ZGln": "too short",
        }
        for value, fragment in cases.items():
            with self.subTest(value=value):
                with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD_HASH": value}, clear=False):
                    error = password_hash_error()
                    self.assertIsNotNone(error)
                    self.assertIn(fragment, error)

    def test_hash_format_round_trips(self) -> None:
        encoded = hash_password("another secret", iterations=12_000)
        scheme, iterations, salt, digest = encoded.split("$", 3)
        self.assertEqual(scheme, "pbkdf2_sha256")
        self.assertEqual(int(iterations), 12_000)
        self.assertTrue(base64.b64decode(salt))
        self.assertTrue(base64.b64decode(digest))
        with patch.dict(os.environ, {"ORBIX_DASHBOARD_PASSWORD_HASH": encoded, "ORBIX_DASHBOARD_PASSWORD": ""}, clear=False):
            self.assertTrue(verify_password("another secret"))


if __name__ == "__main__":
    unittest.main()
