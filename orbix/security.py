from __future__ import annotations

import base64
import binascii
import hmac
import os


AUTH_USER_ENV = "ORBIX_DASHBOARD_USER"
AUTH_PASSWORD_ENV = "ORBIX_DASHBOARD_PASSWORD"


def dashboard_credentials() -> tuple[str, str] | None:
    """Return the configured dashboard login, or None when auth is disabled.

    Authentication is opt-in so a local-only install keeps working. Any
    deployment reachable from another machine should set the password and run
    behind HTTPS; the README lists the rest of the live checklist.
    """
    password = os.environ.get(AUTH_PASSWORD_ENV, "").strip()
    if not password:
        return None
    user = os.environ.get(AUTH_USER_ENV, "").strip() or "orbix"
    return user, password


def basic_auth_allowed(header: str | None) -> bool:
    credentials = dashboard_credentials()
    if credentials is None:
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
    return hmac.compare_digest(user, credentials[0]) and hmac.compare_digest(password, credentials[1])
