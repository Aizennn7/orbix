from __future__ import annotations

from datetime import timedelta

from .domain import format_datetime, parse_datetime

BROKER_NAME = "Pocket Option"
BROKER_URL = "https://pocketoption.com/"
HANDOFF_NOTE = (
    "Pocket Option has no official public trading API. Orbix formats a manual "
    "ticket and never logs in, stores broker credentials, or sends orders. A "
    "broker integration that needs a session cookie would be unofficial, "
    "against the broker's terms, and unsafe to automate."
)


def capabilities() -> dict:
    """Describe what this integration can and cannot do."""
    return {
        "name": BROKER_NAME,
        "url": BROKER_URL,
        "official_api": False,
        "order_execution": False,
        "credentials_required": False,
        "manual_handoff": True,
        "asset_format": "EURUSD",
        "note": HANDOFF_NOTE,
    }


def pocket_option_ticket(signal: dict) -> dict | None:
    """Build a copyable manual ticket for a BUY/SELL read.

    This is deliberately not an order object: there is no endpoint, no session,
    and no credentials. The user still enters the trade in the broker's own UI.
    """
    side = str(signal.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None
    pair = str(signal.get("pair") or "").upper()
    if not pair:
        return None
    horizon = max(1, int(signal.get("horizon") or 1))
    entry_raw = signal.get("entry_time") or signal.get("generated_at")
    expiry_raw = signal.get("prediction_time") or signal.get("expires_at")
    if entry_raw is None:
        return None
    entry = parse_datetime(entry_raw)
    expiry = parse_datetime(expiry_raw) if expiry_raw is not None else entry + timedelta(minutes=horizon)
    asset = pair.replace("/", "").replace("-", "").replace(" ", "")
    direction = "CALL" if side == "BUY" else "PUT"
    text = "\n".join(
        [
            f"ORBIX · {pair} · {direction}",
            f"{BROKER_NAME} · {horizon} min",
            f"Entry {format_datetime(entry)}",
            f"Expiry {format_datetime(expiry)}",
            "Manual entry only. No order was sent.",
            "Use the standard asset that matches this feed, not an OTC variant.",
        ]
    )
    return {
        "broker": BROKER_NAME,
        "url": BROKER_URL,
        "asset": asset,
        "pair": pair,
        "direction": direction,
        "side": side,
        "expiry_minutes": horizon,
        "entry_time": format_datetime(entry),
        "expiry_time": format_datetime(expiry),
        "mode": "MANUAL",
        "orders_sent": False,
        "text": text,
    }
