from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import unittest

from orbix import auth
from orbix.auth import (
    SESSION_MAX_AGE,
    clear_login_failures,
    create_session,
    load_session_secret,
    login_attempt_allowed,
    record_login_failure,
    safe_next,
    verify_session,
)

SECRET = b"test-session-secret"
AUTH_ENV = {"ORBIX_DASHBOARD_PASSWORD": "correct horse", "ORBIX_DASHBOARD_PASSWORD_HASH": ""}


class SessionSecretTests(unittest.TestCase):
    def test_secret_is_generated_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ORBIX_SESSION_SECRET": ""}, clear=False):
            first = load_session_secret(directory)
            second = load_session_secret(directory)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            stored = Path(directory, auth.SESSION_SECRET_FILE)
            self.assertEqual(stored.read_bytes().strip(), first)
            if os.name == "posix":
                self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    def test_env_secret_wins_over_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ORBIX_SESSION_SECRET": "configured-secret"}, clear=False
        ):
            self.assertEqual(load_session_secret(directory), b"configured-secret")
            self.assertFalse(Path(directory, auth.SESSION_SECRET_FILE).exists())

    def test_an_unwritable_directory_still_yields_a_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ORBIX_SESSION_SECRET": ""}, clear=False):
            blocker = Path(directory, "blocker")
            blocker.write_text("not a directory", encoding="utf-8")
            first = load_session_secret(blocker / "nested")
            second = load_session_secret(blocker / "nested")
            self.assertGreaterEqual(len(first), 32)
            self.assertNotEqual(first, second)


class SessionTests(unittest.TestCase):
    def test_a_session_round_trips(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            token, csrf = create_session("orbix", SECRET)
            payload = verify_session(token, SECRET)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["u"], "orbix")
            self.assertEqual(payload["csrf"], csrf)
            self.assertEqual(payload["exp"] - payload["iat"], SESSION_MAX_AGE)
            self.assertTrue(csrf)

    def test_each_session_gets_a_fresh_csrf_token(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            first, first_csrf = create_session("orbix", SECRET)
            second, second_csrf = create_session("orbix", SECRET)
            self.assertNotEqual(first, second)
            self.assertNotEqual(first_csrf, second_csrf)

    def test_tampered_or_foreign_tokens_are_rejected(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            token, _ = create_session("orbix", SECRET)
            encoded, signature = token.rsplit(".", 1)
            forged = json.loads(auth._b64decode(encoded))
            forged["u"] = "attacker"
            forged_encoded = auth._b64encode(json.dumps(forged, separators=(",", ":")).encode("utf-8"))
            for candidate in (
                None,
                "",
                "not-a-token",
                token + "x",
                f"{encoded}.{'0' * 64}",
                f"{forged_encoded}.{signature}",
                token + ".",
            ):
                with self.subTest(candidate=candidate):
                    self.assertIsNone(verify_session(candidate, SECRET))
            self.assertIsNone(verify_session(token, b"another-secret"))

    def test_expired_sessions_are_rejected(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False), patch("orbix.auth.time.time", return_value=1_000_000):
            token, _ = create_session("orbix", SECRET)
        with patch.dict(os.environ, AUTH_ENV, clear=False), patch(
            "orbix.auth.time.time", return_value=1_000_000 + SESSION_MAX_AGE + 1
        ):
            self.assertIsNone(verify_session(token, SECRET))
        with patch.dict(os.environ, AUTH_ENV, clear=False), patch(
            "orbix.auth.time.time", return_value=1_000_000 + SESSION_MAX_AGE - 1
        ):
            self.assertIsNotNone(verify_session(token, SECRET))

    def test_changing_the_password_ends_old_sessions(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            token, _ = create_session("orbix", SECRET)
            self.assertIsNotNone(verify_session(token, SECRET))
        with patch.dict(
            os.environ,
            {"ORBIX_DASHBOARD_PASSWORD": "a different password", "ORBIX_DASHBOARD_PASSWORD_HASH": ""},
            clear=False,
        ):
            self.assertIsNone(verify_session(token, SECRET))

    def test_a_session_without_csrf_is_rejected(self) -> None:
        with patch.dict(os.environ, AUTH_ENV, clear=False):
            payload = {
                "u": "orbix",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "csrf": "",
                "pv": auth._password_tag(),
            }
            encoded = auth._b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            token = f"{encoded}.{auth._signature(encoded, SECRET)}"
            self.assertIsNone(verify_session(token, SECRET))


class SafeNextTests(unittest.TestCase):
    def test_local_paths_are_allowed(self) -> None:
        for value in ("/", "/#trader", "/data?x=1", "/a/b"):
            with self.subTest(value=value):
                self.assertEqual(safe_next(value), value)

    def test_external_or_malformed_targets_fall_back_to_root(self) -> None:
        for value in (None, "", "https://example.com", "//example.com", "\\\\evil", "/ok\nSet-Cookie: x", "javascript:alert(1)", "relative"):
            with self.subTest(value=value):
                self.assertEqual(safe_next(value), "/")


class LoginThrottleTests(unittest.TestCase):
    IP = "203.0.113.9"

    def tearDown(self) -> None:
        clear_login_failures(self.IP)

    def test_five_failures_lock_the_ip_for_the_window(self) -> None:
        now = 1_000_000.0
        with patch("orbix.auth.time.time", return_value=now):
            clear_login_failures(self.IP)
            for _ in range(auth.LOGIN_MAX_FAILURES):
                self.assertTrue(login_attempt_allowed(self.IP))
                record_login_failure(self.IP)
            self.assertFalse(login_attempt_allowed(self.IP))
        with patch("orbix.auth.time.time", return_value=now + auth.LOGIN_WINDOW_SECONDS + 1):
            self.assertTrue(login_attempt_allowed(self.IP))

    def test_a_successful_login_clears_failures(self) -> None:
        with patch("orbix.auth.time.time", return_value=1_000_000.0):
            for _ in range(auth.LOGIN_MAX_FAILURES - 1):
                record_login_failure(self.IP)
            self.assertTrue(login_attempt_allowed(self.IP))
            clear_login_failures(self.IP)
            self.assertTrue(login_attempt_allowed(self.IP))


if __name__ == "__main__":
    unittest.main()
