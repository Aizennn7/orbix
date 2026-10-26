"""
Orbix Mobile API - offline-first API for the mobile client
Works without server, uses local SQLite, syncs to Supabase when online
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .mobile_db import get_mobile_store

logger = logging.getLogger(__name__)

# Root level database config - single source of truth
try:
    from database import get_database_config, get_config
    _root_db_config = get_database_config()
    logger.info(
        "[MobileAPI] Root DB config: backend=%s data_dir=%s",
        _root_db_config.effective_backend.value,
        _root_db_config.data_dir,
    )
except Exception as e:
    _root_db_config = None
    # Loud, not silent: with no root config the mobile API serves a different
    # data directory from the server, so the two clients stop agreeing.
    logger.warning("[MobileAPI] Root config not available: %s", e)

# Also try orbix.database re-export
try:
    from orbix.database import get_database_config as _get_cfg2
    if _root_db_config is None:
        _root_db_config = _get_cfg2()
except Exception:
    pass

# Mobile app instance - lightweight, no heavy dependencies
mobile_app = FastAPI(title="Orbix Mobile - Offline", version="0.4.0-mobile")

mobile_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data dir for the mobile client
MOBILE_DATA_DIR = Path(os.environ.get("ORBIX_MOBILE_DATA_DIR", "./mobile_data"))
candle_store, ledger = get_mobile_store(MOBILE_DATA_DIR)


@mobile_app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": "mobile-offline", "data_dir": str(MOBILE_DATA_DIR)}


@mobile_app.get("/api/health")
def health():
    active = candle_store.get_active_dataset()
    signals = ledger.get_signals(limit=1)
    return {
        "status": "ok",
        "project": "Orbix Mobile",
        "mode": "offline-first",
        "data_source": active["dataset_id"] if active else "NONE",
        "live_feed_connected": False,
        "event_calendar_connected": False,
        "dashboard_auth_enabled": False,
        "offline_ready": True,
        "signal_count": len(ledger.get_signals(limit=1000)),
        "dataset_count": len(candle_store.list_datasets()),
    }


@mobile_app.get("/api/storage")
def storage():
    datasets = candle_store.list_datasets()
    return {
        "datasets": datasets,
        "active": candle_store.get_active_dataset(),
        "total_candles": sum(d.get("candle_count", 0) for d in datasets),
    }


@mobile_app.get("/api/signals")
def get_signals(limit: int = 100):
    return {"signals": ledger.get_signals(limit=limit)}


@mobile_app.get("/api/market")
def market():
    active = candle_store.get_active_dataset()
    if not active:
        return {"candles": [], "pair": "EUR/USD", "source": "NONE"}
    candles = candle_store.get_candles(active["dataset_id"], limit=500)
    return {
        "candles": [
            {
                "timestamp": c.timestamp.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ],
        "pair": active["pair"],
        "source": "mobile-local",
        "dataset_id": active["dataset_id"],
    }


@mobile_app.post("/api/import")
async def import_csv(request: Request):
    """Import CSV from mobile file picker"""
    form = await request.form()
    file = form.get("file")
    pair = form.get("pair", "EUR/USD")
    if not file:
        return JSONResponse({"error": "No file"}, status_code=400)
    
    temp_path = MOBILE_DATA_DIR / f"temp_{file.filename}"
    content = await file.read()
    temp_path.write_bytes(content)
    try:
        dataset_id = candle_store.import_csv(temp_path, pair=pair)
        return {"status": "ok", "dataset_id": dataset_id, "pair": pair}
    finally:
        if temp_path.exists():
            temp_path.unlink()


@mobile_app.get("/api/settings/{key}")
def get_setting(key: str):
    val = ledger.get_setting(key)
    return {"key": key, "value": val}


@mobile_app.post("/api/settings/{key}")
async def set_setting(key: str, request: Request):
    body = await request.json()
    ledger.set_setting(key, body.get("value"))
    return {"status": "ok", "key": key}


@mobile_app.get("/api/sync/status")
def sync_status():
    """For future Supabase sync"""
    return {
        "local_signals": len(ledger.get_signals(limit=10000)),
        "local_datasets": len(candle_store.list_datasets()),
        "supabase_configured": bool(os.environ.get("SUPABASE_URL")),
        "last_sync": ledger.get_setting("last_sync"),
    }


# Serve static web UI for mobile
WEB_DIR = Path(__file__).parent.parent / "web"
if WEB_DIR.exists():
    mobile_app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @mobile_app.get("/")
    def root():
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        # Inject mobile flag
        html = html.replace("</head>", '<script>window.ORBIX_MOBILE=true;window.ORBIX_OFFLINE=true;</script></head>')
        return JSONResponse(content=html, media_type="text/html") if False else \
               __import__("fastapi.responses").responses.HTMLResponse(html)
