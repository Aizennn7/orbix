from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from .security import AUTH_HASH_ENV, AUTH_PASSWORD_ENV


SESSION_COOKIE = "orbix_session"
CSRF_HEADER = "X-Orbix-CSRF"
SESSION_MAX_AGE = 7 * 24 * 60 * 60
SESSION_SECRET_ENV = "ORBIX_SESSION_SECRET"
SESSION_SECRET_FILE = "session_secret"

LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_FAILURES = 5
BASIC_MAX_FAILURES = 20

_failure_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _password_tag() -> str:
    """Fingerprint the configured password so changing it ends old sessions."""
    material = (
        os.environ.get(AUTH_HASH_ENV, "").strip()
        or os.environ.get(AUTH_PASSWORD_ENV, "").strip()
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def load_session_secret(data_dir: str | Path) -> bytes:
    """Use ORBIX_SESSION_SECRET, else a generated 0600 file in the data dir."""
    configured = os.environ.get(SESSION_SECRET_ENV, "").strip()
    if configured:
        return configured.encode("utf-8")
    path = Path(data_dir) / SESSION_SECRET_FILE
    try:
        existing = path.read_bytes().strip()
        if existing:
            return existing
    except OSError:
        pass
    secret = secrets.token_urlsafe(48).encode("ascii")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(secret)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(path)
    except OSError:
        # A read-only data directory must not prevent the login page from
        # working; sessions simply do not survive this process.
        return secrets.token_urlsafe(48).encode("ascii")
    return secret


def _signature(payload: str, secret: bytes) -> str:
    return hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()


def create_session(user: str, secret: bytes) -> tuple[str, str]:
    now = int(time.time())
    csrf = secrets.token_urlsafe(24)
    payload = {
        "u": user,
        "iat": now,
        "exp": now + SESSION_MAX_AGE,
        "csrf": csrf,
        "pv": _password_tag(),
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{_signature(encoded, secret)}", csrf


def verify_session(token: str | None, secret: bytes | None) -> dict | None:
    if not token or not secret or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    try:
        expected = _signature(encoded, secret)
    except (UnicodeEncodeError, ValueError):
        return None
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if expires <= int(time.time()):
        return None
    if not hmac.compare_digest(str(payload.get("pv", "")), _password_tag()):
        return None
    if not payload.get("u") or not payload.get("csrf"):
        return None
    return payload


def safe_next(value: str | None) -> str:
    """Only allow a same-site absolute path after login."""
    text = str(value or "").strip()
    if not text.startswith("/") or text.startswith("//") or "\\" in text:
        return "/"
    if any(ord(character) < 32 for character in text):
        return "/"
    return text


def _bucket(kind: str, ip: str) -> str:
    # Login-form and Basic-auth failures have separate limits so a script with
    # a stale password cannot lock the browser form out, and vice versa.
    return f"{kind}:{ip}"


def _prune(key: str, now: float | None = None) -> list[float]:
    moment = time.time() if now is None else now
    recent = [stamp for stamp in _failures.get(key, []) if moment - stamp < LOGIN_WINDOW_SECONDS]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def _allowed(kind: str, ip: str, limit: int) -> bool:
    with _failure_lock:
        return len(_prune(_bucket(kind, ip))) < limit


def _record(kind: str, ip: str) -> None:
    key = _bucket(kind, ip)
    with _failure_lock:
        recent = _prune(key)
        recent.append(time.time())
        _failures[key] = recent


def _clear(kind: str, ip: str) -> None:
    with _failure_lock:
        _failures.pop(_bucket(kind, ip), None)


def login_attempt_allowed(ip: str) -> bool:
    return _allowed("login", ip, LOGIN_MAX_FAILURES)


def record_login_failure(ip: str) -> None:
    _record("login", ip)


def clear_login_failures(ip: str) -> None:
    _clear("login", ip)


def basic_attempt_allowed(ip: str) -> bool:
    return _allowed("basic", ip, BASIC_MAX_FAILURES)


def record_basic_failure(ip: str) -> None:
    _record("basic", ip)


def clear_basic_failures(ip: str) -> None:
    _clear("basic", ip)
