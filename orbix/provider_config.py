from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any

CONFIG_FILENAME = "provider_settings.json"
MAX_FIELD_LENGTH = 512

ENV_NAMES = {
    "twelve_data_api_key": "ORBIX_TWELVE_DATA_API_KEY",
    "finnhub_api_key": "ORBIX_FINNHUB_API_KEY",
    "telegram_bot_token": "ORBIX_TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "ORBIX_TELEGRAM_CHAT_ID",
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "market": {
        "label": "Twelve Data",
        "fields": ("twelve_data_api_key",),
        "empty": "No market key saved.",
    },
    "calendar": {
        "label": "Finnhub",
        "fields": ("finnhub_api_key",),
        "empty": "No calendar key saved.",
    },
    "telegram": {
        "label": "Telegram",
        "fields": ("telegram_bot_token", "telegram_chat_id"),
        "empty": "Bot token and chat id are both required.",
    },
}


def mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "••••"
    return f"••••{text[-4:]}"


def _clean(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_FIELD_LENGTH:
        raise ValueError(f"A key is longer than {MAX_FIELD_LENGTH} characters.")
    if any(character in text for character in ("\n", "\r", "\0")):
        raise ValueError("Keys cannot contain line breaks or null characters.")
    return text


class ProviderConfigStore:
    """Persist provider keys on the server, with environment variables winning.

    The file lives under the active data directory (ignored by Git), is written
    atomically with mode 600, and is never returned by the status API. The
    browser only ever receives a mask of the last four characters.
    """

    def __init__(self, data_dir: str | Path):
        override = os.environ.get("ORBIX_PROVIDER_SETTINGS_FILE", "").strip()
        self.path = Path(override).expanduser() if override else Path(data_dir) / CONFIG_FILENAME
        self._lock = threading.RLock()
        self._keys: dict[str, str] = {}
        self._tests: dict[str, dict[str, Any]] = {}
        self._updated_at: str | None = None
        self._read()

    # ------------------------------------------------------------- persistence
    def _read(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        keys = payload.get("keys")
        if not isinstance(keys, dict):
            # Tolerate a flat legacy file of key/value pairs.
            keys = payload
        self._keys = {
            field: str(value).strip()
            for field, value in keys.items()
            if field in ENV_NAMES and isinstance(value, (str, int, float)) and str(value).strip()
        }
        tests = payload.get("tests")
        if isinstance(tests, dict):
            self._tests = {
                provider: {
                    "accessible": bool(result.get("accessible")),
                    "message": str(result.get("message") or "")[:300],
                    "checked_at": result.get("checked_at"),
                }
                for provider, result in tests.items()
                if provider in PROVIDERS and isinstance(result, dict)
            }
        self._updated_at = payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None

    def _write(self) -> None:
        payload = {
            "updated_at": self._updated_at,
            "keys": dict(self._keys),
            "tests": dict(self._tests),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------------ values
    def environment_value(self, field: str) -> str:
        return os.environ.get(ENV_NAMES[field], "").strip()

    def value(self, field: str) -> tuple[str, str | None]:
        env_value = self.environment_value(field)
        if env_value:
            return env_value, "environment"
        saved = str(self._keys.get(field, "")).strip()
        if saved:
            return saved, "web"
        return "", None

    def effective(self) -> dict[str, str]:
        return {field: self.value(field)[0] for field in ENV_NAMES}

    def update(self, values: dict[str, str | None]) -> None:
        with self._lock:
            changed_providers: set[str] = set()
            for field, raw in values.items():
                if field not in ENV_NAMES or raw is None:
                    continue
                value = _clean(raw)
                env_value = self.environment_value(field)
                if env_value:
                    if value and value != env_value:
                        raise ValueError(
                            f"{ENV_NAMES[field]} is managed by the server environment and cannot be replaced here."
                        )
                    continue
                if value:
                    self._keys[field] = value
                else:
                    self._keys.pop(field, None)
                for provider, definition in PROVIDERS.items():
                    if field in definition["fields"]:
                        changed_providers.add(provider)
            for provider in changed_providers:
                self._tests.pop(provider, None)
            self._updated_at = datetime.now(timezone.utc).isoformat()
            self._write()

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
            self._tests.clear()
            self._updated_at = datetime.now(timezone.utc).isoformat()
            self._write()

    def record_tests(self, results: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for provider, result in results.items():
                if provider not in PROVIDERS or not isinstance(result, dict):
                    continue
                if result.get("accessible") is None:
                    self._tests.pop(provider, None)
                    continue
                self._tests[provider] = {
                    "accessible": bool(result.get("accessible")),
                    "message": str(result.get("message") or "")[:300],
                    "checked_at": now,
                }
            self._updated_at = now
            self._write()

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        with self._lock:
            fields: dict[str, dict[str, Any]] = {}
            for field in ENV_NAMES:
                value, source = self.value(field)
                fields[field] = {
                    "configured": bool(value),
                    "source": source,
                    "masked": mask_secret(value) if value else None,
                }
            providers: dict[str, dict[str, Any]] = {}
            for provider, definition in PROVIDERS.items():
                configured = all(fields[field]["configured"] for field in definition["fields"])
                sources = {fields[field]["source"] for field in definition["fields"]}
                source = (
                    "environment" if "environment" in sources
                    else "web" if "web" in sources
                    else None
                )
                if configured:
                    token_field = definition["fields"][0]
                    masked = fields[token_field]["masked"]
                else:
                    masked = None
                test = self._tests.get(provider) if configured else None
                if configured and test:
                    message = test.get("message") or "Last check completed."
                elif configured:
                    message = "Configured; not tested yet."
                else:
                    message = definition["empty"]
                providers[provider] = {
                    "label": definition["label"],
                    "configured": configured,
                    "source": source,
                    "masked": masked,
                    "accessible": test.get("accessible") if test else None,
                    "checked_at": test.get("checked_at") if test else None,
                    "message": message,
                }
            stored_fields = [field for field in ENV_NAMES if self._keys.get(field)]
            return {
                "fields": fields,
                "providers": providers,
                "stored": bool(stored_fields),
                "updated_at": self._updated_at,
                "note": (
                    "Keys are stored in a server-local file readable only by the server user (mode 600). "
                    "The file is not encrypted at rest, is never returned in full, and is masked when read back. "
                    "Server environment variables take precedence over saved keys."
                ),
            }
