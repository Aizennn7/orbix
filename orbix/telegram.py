from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_TELEGRAM_RESPONSE_BYTES = 256_000


class TelegramError(RuntimeError):
    """Safe-to-display Telegram Bot API error without leaking the bot token."""


class TelegramNotifier:
    """One-channel Telegram publisher. Credentials are read only server-side."""

    def __init__(self, bot_token: str | None, chat_id: str | None, timeout: float = 10.0):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "target_chat": self.chat_id or None,
        }

    def send_message(self, text: str) -> dict[str, Any]:
        if not self.configured:
            raise TelegramError(
                "Set ORBIX_TELEGRAM_BOT_TOKEN and ORBIX_TELEGRAM_CHAT_ID on the server."
            )
        message = str(text).strip()
        if not message:
            raise TelegramError("Telegram message cannot be empty.")
        if len(message) > 4096:
            raise TelegramError("Telegram message exceeds the 4096-character Bot API limit.")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": message}).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload_bytes = response.read(MAX_TELEGRAM_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            description = self._description(exc.read(MAX_TELEGRAM_RESPONSE_BYTES))
            raise TelegramError(f"Telegram rejected the message (HTTP {exc.code}): {description}") from None
        except (URLError, TimeoutError, OSError):
            raise TelegramError("Telegram could not be reached; check server egress and bot configuration.") from None

        if len(payload_bytes) > MAX_TELEGRAM_RESPONSE_BYTES:
            raise TelegramError("Telegram response exceeded the safety limit.")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramError("Telegram returned an unreadable response.") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = str(payload.get("description", "Telegram API reported an unknown error."))[:220]
            raise TelegramError(f"Telegram rejected the message: {description}")
        result = payload.get("result") or {}
        return {
            "message_id": result.get("message_id"),
            "chat_id": result.get("chat", {}).get("id", self.chat_id),
        }

    @staticmethod
    def _description(body: bytes) -> str:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "check that the bot is an administrator with permission to post messages."
        description = str(payload.get("description", "unknown Bot API error"))[:220]
        return description
