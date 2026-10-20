from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets


AUTH_USER_ENV = "ORBIX_DASHBOARD_USER"
AUTH_PASSWORD_ENV = "ORBIX_DASHBOARD_PASSWORD"
AUTH_HASH_ENV = "ORBIX_DASHBOARD_PASSWORD_HASH"
DEFAULT_USER = "orbix"


def auth_configured() -> bool:
    """True when either a plaintext password or a password hash is set."""
    return bool(
        os.environ.get(AUTH_PASSWORD_ENV, "").strip()
        or os.environ.get(AUTH_HASH_ENV, "").strip()
    )


def dashboard_user() -> str:
    return os.environ.get(AUTH_USER_ENV, "").strip() or DEFAULT_USER


def dashboard_credentials() -> tuple[str, str] | None:
    """Return the configured plaintext login, or None when not configured.

    Kept for callers that need to know whether a plaintext password exists.
    Prefer `auth_configured()` for decisions because a hashed password also
    enables authentication.
    """
    password = os.environ.get(AUTH_PASSWORD_ENV, "").strip()
    if not password:
        return None
    return dashboard_user(), password


def hash_password(password: str, *, iterations: int = 260_000) -> str:
    """Create a PBKDF2-SHA256 value suitable for ORBIX_DASHBOARD_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_hash(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 10_000 or iterations > 5_000_000:
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
    except (ValueError, binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def verify_password(candidate: str) -> bool:
    hashed = os.environ.get(AUTH_HASH_ENV, "").strip()
    if hashed:
        return _verify_hash(candidate, hashed)
    plaintext = os.environ.get(AUTH_PASSWORD_ENV, "").strip()
    if not plaintext:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), plaintext.encode("utf-8"))


def basic_auth_allowed(header: str | None) -> bool:
    if not auth_configured():
        return True
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, password = decoded.split(":", 1)
    return hmac.compare_digest(user.encode("utf-8"), dashboard_user().encode("utf-8")) and verify_password(password)


def _main() -> None:
    """Print a PBKDF2 hash for ORBIX_DASHBOARD_PASSWORD_HASH."""
    import getpass

    password = getpass.getpass("Dashboard password: ")
    if len(password) < 8:
        raise SystemExit("Use at least 8 characters.")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    _main()
