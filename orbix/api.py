from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from html import escape as html_escape
import hmac
import os
from pathlib import Path
import tempfile
from typing import Callable
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    basic_attempt_allowed,
    clear_basic_failures,
    clear_login_failures,
    create_session,
    load_session_secret,
    login_attempt_allowed,
    record_basic_failure,
    record_login_failure,
    safe_next,
    verify_session,
)
from .automation import AutomationService, ProviderError
from .backtest import run_walk_forward_backtest
from .brokers import capabilities as broker_capabilities
from .brokers import pocket_option_ticket
from .domain import parse_datetime
from .env import load_env
from .future import build_future_plan
from .provider_config import ProviderConfigStore
from .runtime import BACKTEST_WINDOW, Runtime
from .security import auth_configured, basic_auth_allowed, dashboard_user, password_hash_error, verify_password
from .strategies import strategy_catalog_payload
from .telegram import TelegramError


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
MAX_APPEND_BYTES = 64 * 1024 * 1024

# Load ORBIX root .env before Runtime or AutomationService read configuration.
# Real process variables keep precedence, so `uvicorn --env-file` still works.
ENV_FILE = load_env()

_STARTUP_AUTH_ERROR = password_hash_error() if auth_configured() else None
if _STARTUP_AUTH_ERROR:
    # Fail before opening the data store or serving a dashboard nobody can log
    # in to. A process manager will surface this message in the logs.
    raise RuntimeError(
        f"ORBIX_DASHBOARD_PASSWORD_HASH is unusable: {_STARTUP_AUTH_ERROR}. "
        "Regenerate it with: python -m orbix.security"
    )

# Created here rather than in runtime.py so importing Runtime (tests, tooling)
# does not open the live data store or fight a running preview for the lock.
runtime = Runtime()
automation = AutomationService(runtime)
provider_config = ProviderConfigStore(runtime.data_dir)
automation.apply_provider_config(provider_config.effective())
SESSION_SECRET = load_session_secret(runtime.data_dir) if auth_configured() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await automation.start()
    try:
        yield
    finally:
        await automation.stop()


app = FastAPI(
    title="Orbix Signal Lab",
    description="A signal-only research and paper-testing dashboard. No broker orders are sent.",
    version="0.4.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


PUBLIC_PATHS = {"/login", "/healthz", "/api/auth/status", "/favicon.ico"}


def _cookie_secure(request: Request) -> bool:
    configured = os.getenv("ORBIX_COOKIE_SECURE", "").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _session_payload(request: Request) -> dict | None:
    return verify_session(request.cookies.get(SESSION_COOKIE), SESSION_SECRET)


def _login_page(next_url: str = "/", error: str = "") -> str:
    template = (WEB_DIR / "login.html").read_text(encoding="utf-8")
    error_html = f'<p class="login-error" role="alert">{html_escape(error)}</p>' if error else ""
    return template.replace("{{error}}", error_html).replace("{{next}}", html_escape(next_url))


def _login_response(next_url: str = "/", error: str = "", status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_login_page(next_url, error), status_code=status_code, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def dashboard_auth(request: Request, call_next):
    """Session login (with HTTP Basic fallback) for pages and API routes.

    Authentication is opt-in through ORBIX_DASHBOARD_PASSWORD or
    ORBIX_DASHBOARD_PASSWORD_HASH. Static assets and the login page stay public;
    every other page redirects to /login and every API route returns 401 until
    the browser has a valid signed session.
    """
    if not auth_configured():
        return await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in PUBLIC_PATHS:
        return await call_next(request)
    session = _session_payload(request)
    client_ip = request.client.host if request.client else "unknown"
    basic_header = request.headers.get("authorization", "")
    basic_ok = basic_auth_allowed(basic_header)
    if basic_ok:
        clear_basic_failures(client_ip)
    if not session and not basic_ok:
        if basic_header.lower().startswith("basic "):
            if not basic_attempt_allowed(client_ip):
                return JSONResponse(
                    {"detail": "Too many authentication attempts. Try again later."},
                    status_code=429,
                )
            record_basic_failure(client_ip)
        if path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": "Authentication required."}, status_code=401)
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
    if session and not basic_ok and request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get(CSRF_HEADER, "").encode("utf-8")
        expected = str(session.get("csrf", "")).encode("utf-8")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "CSRF token missing or invalid."}, status_code=403)
    request.state.user = (session or {}).get("u") or dashboard_user()
    return await call_next(request)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'"
    ),
}


def _allow_embedding() -> bool:
    """Preview hosts embed the dashboard; production should keep this off."""
    return os.getenv("ORBIX_ALLOW_EMBED", "").strip().lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Send standards-based hardening headers on every response.

    Added after the auth middleware so its 401/redirect responses are covered
    too. Dashboard HTML and JSON are never cached; versioned static assets are.
    Frame denial is removed only when ORBIX_ALLOW_EMBED is explicitly set.
    """
    response = await call_next(request)
    headers = dict(SECURITY_HEADERS)
    if not _allow_embedding():
        headers["X-Frame-Options"] = "DENY"
        headers["Content-Security-Policy"] += "; frame-ancestors 'none'"
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    else:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = ""):
    if not auth_configured():
        return RedirectResponse("/", status_code=303)
    target = safe_next(next)
    if _session_payload(request):
        return RedirectResponse(target, status_code=303)
    config_error = password_hash_error()
    if config_error:
        return _login_response(
            target,
            f"Server login is misconfigured ({config_error}). Fix ORBIX_DASHBOARD_PASSWORD_HASH and restart.",
        )
    return _login_response(target)


@app.post("/login")
async def login_submit(request: Request) -> Response:
    if not auth_configured():
        return RedirectResponse("/", status_code=303)
    config_error = password_hash_error()
    if config_error:
        return _login_response(
            "/",
            f"Server login is misconfigured ({config_error}). Fix ORBIX_DASHBOARD_PASSWORD_HASH and restart.",
            503,
        )
    body = await request.body()
    if len(body) > 4096:
        return _login_response("/", "Login request is too large.", 413)
    form = parse_qs(body.decode("utf-8", "replace"))
    username = (form.get("username") or [""])[0].strip()
    password = (form.get("password") or [""])[0]
    next_url = safe_next((form.get("next") or ["/"])[0])
    client_ip = request.client.host if request.client else "unknown"
    if not login_attempt_allowed(client_ip):
        return _login_response(next_url, "Too many attempts. Wait a few minutes and try again.", 429)
    user_ok = hmac.compare_digest(username.encode("utf-8"), dashboard_user().encode("utf-8"))
    password_ok = verify_password(password)
    if user_ok and password_ok:
        clear_login_failures(client_ip)
        secret = SESSION_SECRET or load_session_secret(runtime.data_dir)
        token, _ = create_session(dashboard_user(), secret)
        response = RedirectResponse(next_url, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=_cookie_secure(request),
            path="/",
        )
        return response
    record_login_failure(client_ip)
    return _login_response(next_url, "Wrong username or password.", 401)


@app.post("/logout")
def logout(request: Request) -> Response:
    wants_json = "application/json" in request.headers.get("accept", "")
    response = JSONResponse({"status": "signed_out"}) if wants_json else RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    if not auth_configured():
        return {"auth_enabled": False, "authenticated": True, "user": None, "csrf": None}
    session = _session_payload(request)
    if session:
        return {
            "auth_enabled": True,
            "authenticated": True,
            "user": session.get("u"),
            "csrf": session.get("csrf"),
            "via": "session",
        }
    if basic_auth_allowed(request.headers.get("authorization")):
        return {
            "auth_enabled": True,
            "authenticated": True,
            "user": dashboard_user(),
            "csrf": None,
            "via": "basic",
        }
    return {"auth_enabled": True, "authenticated": False, "user": None, "csrf": None}


class SignalRequest(BaseModel):
    pair: str = "EUR/USD"
    strategy: str = "ml_consensus"
    horizon: int = Field(default=1, ge=1, le=60)


class ScanRequest(BaseModel):
    pair: str = "EUR/USD"
    horizon: int = Field(default=1, ge=1, le=60)
    record: bool = True


class BacktestRequest(BaseModel):
    pair: str = "EUR/USD"
    strategy: str = "ml_consensus"
    horizon: int = Field(default=1, ge=1, le=60)
    payout: float = Field(default=0.80, gt=0, le=1)
    threshold: float = Field(default=0.57, gt=0.5, lt=1)


class FeedbackRequest(BaseModel):
    signal_id: str
    exit_price: float = Field(gt=0)


class TelegramSendRequest(BaseModel):
    signal_ids: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)
    include_demo: bool = False
    force: bool = False
    dry_run: bool = False


class TelegramAutoRequest(BaseModel):
    enabled: bool


class ProviderConfigRequest(BaseModel):
    twelve_data_api_key: str | None = Field(default=None, max_length=512)
    finnhub_api_key: str | None = Field(default=None, max_length=512)
    telegram_bot_token: str | None = Field(default=None, max_length=512)
    telegram_chat_id: str | None = Field(default=None, max_length=512)


def _telegram_http_error(exc: TelegramError) -> HTTPException:
    message = str(exc)
    if message.startswith(("Set ORBIX_", "Configure ORBIX_")):
        status_code = 409
    elif message.startswith("Daily cap"):
        status_code = 429
    else:
        status_code = 502
    return HTTPException(status_code=status_code, detail=message)


async def _with_streamed_csv(request: Request, max_bytes: int, processor: Callable[[Path], dict]) -> dict:
    upload_root = runtime.data_dir / "tmp"
    upload_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="csv-upload-", dir=upload_root) as temp_dir:
            path = Path(temp_dir) / "candles.csv"
            byte_count = 0
            with path.open("wb") as destination:
                async for chunk in request.stream():
                    byte_count += len(chunk)
                    if byte_count > max_bytes:
                        raise HTTPException(status_code=413, detail="CSV upload exceeds the configured size limit")
                    destination.write(chunk)
            if byte_count == 0:
                raise HTTPException(status_code=400, detail="CSV upload is empty")
            return await asyncio.to_thread(processor, path)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
def healthz() -> dict:
    """Unauthenticated liveness probe for container and platform checks.

    Deliberately returns nothing about the feed, the model, or the trading
    state; that detail stays behind authentication on /api/health.
    """
    return {"status": "ok"}


@app.get("/api/health")
def health() -> dict:
    automation_state = automation.status()
    market_state = automation_state["market"]
    with runtime.lock:
        storage = runtime.storage_summary()
        return {
            "status": "ok",
            "project": "Orbix Signal Lab",
            "data_source": runtime.source,
            "live_feed_connected": bool(market_state["connected"] and market_state["active_dataset_is_provider"] and market_state["data_fresh"]),
            "event_calendar_connected": automation_state["calendar"]["connected"],
            "order_execution": False,
            "candles_loaded": runtime.candle_count,
            "candles_in_memory": len(runtime.candles),
            "storage_engine": storage["engine"],
            "storage_path": storage["path"],
            "archived_candles": storage["candles_archived"],
            "online_learning_updates": runtime.ledger.learning_summary()["online_updates"],
            "automatic_signals_active": automation_state["automatic_signals_active"],
            "clock": automation_state.get("clock"),
            "env_file_loaded": ENV_FILE is not None,
            "dashboard_auth_enabled": auth_configured(),
            "dashboard_auth_error": password_hash_error(),
        }


@app.get("/api/storage")
def storage_status() -> dict:
    with runtime.lock:
        summary = runtime.storage_summary()
        summary["datasets"] = runtime.candle_store.list_datasets(limit=50)
        # Add root-level database config
        try:
            from database import get_database_config, check_database_health
            db_cfg = get_database_config()
            summary["database_config"] = db_cfg.to_dict()
            summary["database_health"] = check_database_health(db_cfg)
        except Exception as e:
            summary["database_config_error"] = str(e)
        return summary


@app.get("/api/database/config")
def database_config() -> dict:
    """Root level database config - single source of truth, permanent"""
    try:
        from database import get_database_config
        cfg = get_database_config()
        # Also check permanent APK config at root level
        root = Path(__file__).resolve().parents[1]
        apk_config = {}
        apk_config_path = root / "database" / "apk_config.json"
        if apk_config_path.exists():
            import json
            try:
                apk_config = json.loads(apk_config_path.read_text())
            except:
                pass
        return {
            "status": "ok",
            "config": cfg.to_dict(),
            "apk_config": apk_config,
            "root_level": True,
            "permanent": True,
            "location": "database/ + data/ + .env at root - single source of truth",
            "data_dir": str(cfg.data_dir),
            "note": "All data inside APK configured permanently at root level, shared by web, mobile, laptop, APK"
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/database/apk-config")
def database_apk_config() -> dict:
    """Permanent APK config at root level - all data inside APK configured at root"""
    try:
        root = Path(__file__).resolve().parents[1]
        apk_config_path = root / "database" / "apk_config.json"
        root_manifest_path = root / "database" / "root_manifest.json"
        data_root_config_path = root / "data" / "root_config.json"
        
        import json
        apk_config = {}
        manifest = {}
        data_config = {}
        
        if apk_config_path.exists():
            apk_config = json.loads(apk_config_path.read_text())
        if root_manifest_path.exists():
            manifest = json.loads(root_manifest_path.read_text())
        if data_root_config_path.exists():
            data_config = json.loads(data_root_config_path.read_text())
        
        from database import get_database_config
        cfg = get_database_config()
        
        return {
            "status": "ok",
            "config": apk_config,
            "manifest": manifest,
            "data_config": data_config,
            "current": cfg.to_dict(),
            "root_level": True,
            "permanent": True,
            "location": "database/apk_config.json at root - permanent APK config",
            "note": "All data inside APK configured permanently at root level"
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/database/root-manifest")
def database_root_manifest() -> dict:
    """Root manifest - all data inside APK at root level"""
    try:
        root = Path(__file__).resolve().parents[1]
        manifest_path = root / "database" / "root_manifest.json"
        import json
        if manifest_path.exists():
            return {"status": "ok", "manifest": json.loads(manifest_path.read_text()), "permanent": True, "location": "database/root_manifest.json at root"}
        return {"status": "error", "error": "Manifest not found, run scripts/configure_root_apk.py --build"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/database/config")
async def database_config_update(request: Request) -> dict:
    """Update root level Supabase config from mobile APK - makes live online"""
    try:
        body = await request.json()
        url = body.get("supabase_url") or body.get("url") or ""
        key = body.get("supabase_key") or body.get("anon_key") or body.get("key") or ""
        if not url or not key:
            return {"status": "error", "error": "Need supabase_url + supabase_key"}
        # Save to data dir for persistence, and note that root .env should be updated
        from database import get_database_config
        cfg = get_database_config()
        # Write to a runtime config file at root data/ (does not overwrite .env, just caches)
        runtime_cfg_path = cfg.data_dir / "runtime_supabase.json"
        runtime_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        runtime_cfg_path.write_text(json.dumps({"supabase_url": url, "supabase_anon_key": key, "updated_at": __import__("datetime").datetime.utcnow().isoformat()}, indent=2))
        # Also set env for current process
        os.environ["SUPABASE_URL"] = url
        os.environ["SUPABASE_ANON_KEY"] = key
        os.environ["DATABASE_BACKEND"] = "supabase"
        return {
            "status": "ok",
            "message": "Live config saved at root data/runtime_supabase.json, set in current process. For persistence across restarts, also set at root .env: SUPABASE_URL + SUPABASE_ANON_KEY + DATABASE_BACKEND=supabase",
            "live": True,
            "location": str(runtime_cfg_path)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/database/sync")
async def database_sync(request: Request) -> dict:
    """Sync mobile APK data to root database (live when Supabase configured)"""
    try:
        body = await request.json()
        signals = body.get("signals", [])
        datasets = body.get("datasets", [])
        candles = body.get("candles", [])
        
        from database import get_database_config
        from database.connection import get_connection
        cfg = get_database_config()
        
        saved = {"signals": 0, "datasets": 0, "candles": 0}
        
        # Save signals to root DB (will go to Supabase if configured at root)
        try:
            conn = get_connection(cfg)
            if hasattr(conn, 'cursor'):
                # Postgres/Supabase via psycopg or supabase-py handles differently
                pass
            # For now, save to local file as sync log
            import json
            from datetime import datetime
            sync_log = cfg.data_dir / "mobile_sync.json"
            existing = []
            if sync_log.exists():
                try:
                    existing = json.loads(sync_log.read_text())
                except:
                    existing = []
            existing.append({"at": datetime.utcnow().isoformat(), "signals": len(signals), "datasets": len(datasets), "candles": len(candles)})
            # Keep last 100
            existing = existing[-100:]
            sync_log.write_text(json.dumps(existing, indent=2))
            saved["signals"] = len(signals)
            saved["datasets"] = len(datasets)
            saved["candles"] = len(candles)
        except Exception as e:
            print(f"[Sync] Error: {e}")
        
        return {
            "status": "ok",
            "saved": saved,
            "backend": cfg.effective_backend.value,
            "is_live": cfg.is_supabase or cfg.is_postgres,
            "message": f"Synced to {cfg.effective_backend.value} at root level - {'LIVE ONLINE' if cfg.is_supabase else 'offline local'}"
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/database/health")
def database_health() -> dict:
    """Database health check - root level"""
    try:
        from database import get_database_config
        from database.connection import check_database_health
        cfg = get_database_config()
        health = check_database_health(cfg)
        return {"status": "ok", "health": health, "config": cfg.to_dict()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/database/schema")
def database_schema() -> dict:
    """Get database schema info - root level"""
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        sqlite_schema = (root / "database" / "sqlite_schema.sql").read_text()[:2000] if (root / "database" / "sqlite_schema.sql").exists() else "Not found"
        pg_schema = (root / "database" / "schema.sql").read_text()[:2000] if (root / "database" / "schema.sql").exists() else "Not found"
        return {
            "status": "ok",
            "sqlite_schema_preview": sqlite_schema,
            "postgres_schema_preview": pg_schema,
            "location": "database/ at root level",
            "files": ["database/schema.sql", "database/sqlite_schema.sql"]
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/abstentions")
def abstentions(days: int = 7, limit: int = 25) -> dict:
    days = max(1, min(days, 90))
    limit = max(0, min(limit, 100))
    with runtime.lock:
        return runtime.abstention_summary(days=days, limit=limit)


@app.get("/api/learning")
def learning_status() -> dict:
    with runtime.lock:
        return runtime.learning_summary()


@app.get("/api/model")
def model_status(pair: str = "EUR/USD") -> dict:
    try:
        with runtime.lock:
            return runtime.model_card(pair)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/model/retrain")
def model_retrain(pair: str = "EUR/USD") -> dict:
    try:
        return runtime.retrain_model(pair)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/model/rollback")
def model_rollback(pair: str = "EUR/USD") -> dict:
    try:
        return runtime.rollback_model(pair)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/model/maintenance")
def model_maintenance() -> dict:
    return runtime.learning_maintenance()


@app.get("/api/automation/status")
def automation_status() -> dict:
    return automation.status()


def _providers_payload() -> dict:
    payload = provider_config.status()
    automation_state = automation.status()
    calendar_state = automation.calendar.status()
    telegram_state = automation.telegram.status()
    payload["providers"]["market"]["live"] = bool(automation_state["market"]["connected"])
    payload["providers"]["calendar"]["live"] = bool(calendar_state["connected"])
    payload["providers"]["calendar"]["detail"] = {
        "last_updated": calendar_state["last_updated"],
        "last_error": calendar_state["last_error"],
        "event_count": calendar_state["event_count"],
    }
    auto_post = bool(automation_state["telegram"].get("auto_post_enabled", False))
    payload["providers"]["telegram"]["live"] = bool(telegram_state["configured"] and auto_post)
    payload["providers"]["telegram"]["target_chat"] = telegram_state["target_chat"]
    payload["providers"]["telegram"]["auto_post"] = auto_post
    return payload


@app.get("/api/providers")
def provider_status() -> dict:
    return _providers_payload()


@app.post("/api/providers")
def save_provider_config(payload: ProviderConfigRequest) -> dict:
    try:
        provider_config.update(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    automation.apply_provider_config(provider_config.effective())
    return _providers_payload()


@app.delete("/api/providers")
def clear_provider_config() -> dict:
    provider_config.clear()
    automation.apply_provider_config(provider_config.effective())
    return _providers_payload()


@app.post("/api/providers/test")
def test_provider_config() -> dict:
    results: dict[str, dict] = {}
    checks = {
        "market": automation.market_provider.probe,
        "calendar": automation.calendar.probe,
        "telegram": automation.telegram.probe,
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {name: pool.submit(check) for name, check in checks.items()}
        done, _ = wait(futures.values(), timeout=25)
    for name, future in futures.items():
        if future not in done:
            results[name] = {"accessible": False, "message": "The check timed out."}
            continue
        try:
            results[name] = future.result()
        except Exception:
            results[name] = {"accessible": False, "message": "The check could not be completed."}
    provider_config.record_tests(results)
    payload = _providers_payload()
    for name, result in results.items():
        provider = payload["providers"][name]
        provider["accessible"] = result.get("accessible")
        provider["message"] = str(result.get("message") or "")[:300]
    return payload


@app.get("/api/trader")
def trader(days: int = 7) -> dict:
    """Human-style discipline state, recent journal, and daily reviews.

    This describes what the automatic path would do, not a forecast. The rules
    can only reduce activity; they cannot create an edge.
    """
    clock = runtime.clock()
    reference = parse_datetime(clock["reference"])
    with runtime.lock:
        pair = runtime.pair
        source = runtime.source
        dataset_id = runtime.dataset_id()
        status = runtime.discipline.status(
            pair=pair,
            reference=reference,
            apply_rules=source != "SYNTHETIC DEMO",
            dataset_id=dataset_id,
        )
        reviews = runtime.discipline.reviews(
            pair=pair, days=days, reference=reference, dataset_id=dataset_id
        )
        contexts = runtime.discipline.contexts(pair=pair, dataset_id=dataset_id)
        journal = runtime.ledger.list_engine_events(
            max(20, min(60, days * 10)),
            dataset_id=dataset_id,
            kinds=("journal",),
        )
    return {
        "pair": pair,
        "source": source,
        "dataset_id": dataset_id,
        "reference": reference.isoformat(),
        "clock": clock,
        "status": status,
        "reviews": reviews,
        "contexts": contexts,
        "journal": journal,
    }


@app.get("/api/preflight")
def preflight_status() -> dict:
    return automation.preflight()


@app.post("/api/automation/sync")
async def sync_live_feed() -> dict:
    try:
        return await asyncio.to_thread(automation.sync_market, True)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/automation/calendar/refresh")
async def refresh_calendar() -> dict:
    try:
        return await asyncio.to_thread(automation.sync_calendar)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/telegram/status")
def telegram_status() -> dict:
    state = automation.status()
    return {
        **state["telegram"],
        "recent_signals": state["recent_auto_signals"],
        "latest_auto_signal": state["latest_auto_signal"],
        "automatic_signals_active": state["automatic_signals_active"],
        "setup": {
            "bot_token_env": "ORBIX_TELEGRAM_BOT_TOKEN",
            "chat_id_env": "ORBIX_TELEGRAM_CHAT_ID",
            "timezone_env": "ORBIX_TELEGRAM_TIMEZONE",
            "auto_post_env": "ORBIX_TELEGRAM_AUTO_POST",
            "steps": [
                "Create the bot with @BotFather and copy its token into ORBIX_TELEGRAM_BOT_TOKEN.",
                "Add the bot to the target channel as an administrator with 'Post messages'.",
                "Use the numeric -100... channel id (or @channelusername) in ORBIX_TELEGRAM_CHAT_ID.",
                "Restart the server, then press TEST CONNECTION before the one-click send.",
            ],
        },
    }


@app.post("/api/telegram/send")
def telegram_send(payload: TelegramSendRequest) -> dict:
    try:
        return automation.send_telegram_batch(
            signal_ids=payload.signal_ids or None,
            limit=payload.limit,
            include_demo=payload.include_demo,
            force=payload.force,
            dry_run=payload.dry_run,
        )
    except TelegramError as exc:
        raise _telegram_http_error(exc) from exc


@app.post("/api/telegram/test")
def telegram_test() -> dict:
    try:
        return automation.send_telegram_test()
    except TelegramError as exc:
        raise _telegram_http_error(exc) from exc


@app.post("/api/telegram/auto")
def telegram_auto(payload: TelegramAutoRequest) -> dict:
    try:
        return automation.set_telegram_auto_post(payload.enabled)
    except TelegramError as exc:
        raise _telegram_http_error(exc) from exc


@app.get("/api/future/plan")
def future_plan(pair: str | None = None, count: int = 6) -> dict:
    count = max(1, min(count, 8))
    if pair:
        requested = pair.upper().strip()
        if requested != runtime.pair:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The active dataset is {runtime.pair}. Import {requested} candles from the Data page "
                    "before switching the single-pair view."
                ),
            )
    try:
        return build_future_plan(
            runtime, automation, count=count, now=parse_datetime(runtime.clock()["reference"])
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/datasets/{dataset_id}/activate")
def activate_dataset(dataset_id: str) -> dict:
    try:
        return {"status": "active", **runtime.activate_dataset(dataset_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/market")
def market(pair: str = "EUR/USD", limit: int = 180) -> dict:
    pair = pair.upper().strip()
    limit = max(40, min(limit, 720))
    try:
        with runtime.lock:
            if runtime.source == "SYNTHETIC DEMO":
                runtime.set_demo_pair(pair)
            elif pair != runtime.pair:
                raise ValueError(f"Imported CSV data is for {runtime.pair}; reset to demo to switch pairs")
            candles = runtime.candles[-limit:]
            automation_market = automation.status()["market"]
            live_feed_connected = bool(
                automation_market["connected"]
                and automation_market["active_dataset_is_provider"]
                and automation_market["data_fresh"]
            )
            return {
                "pair": runtime.pair,
                "timeframe": "M1",
                "source": runtime.source,
                "source_label": "LIVE TWELVE DATA" if live_feed_connected else runtime.source,
                "live_feed_connected": live_feed_connected,
                "candles": [candle.to_dict() for candle in candles],
                "latest": candles[-1].to_dict() if candles else None,
                "total_candles": runtime.candle_count,
                "candles_in_memory": len(runtime.candles),
                "dataset_id": runtime.dataset_id() if runtime.source == "IMPORTED CSV" else None,
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/strategies")
def strategies() -> dict:
    return strategy_catalog_payload()


@app.get("/api/broker")
def broker_status() -> dict:
    """Broker handoff capabilities, including what is deliberately not wired."""
    return broker_capabilities()


@app.post("/api/signals/scan")
def scan_signals(payload: ScanRequest) -> dict:
    """Test every executable strategy on the same closed candle.

    The scan itself is read-only. When `record` is true it creates one paper
    signal from the strictest qualified method, never one per strategy.
    """
    pair = payload.pair.upper().strip()
    clock = runtime.clock()
    guard = automation.evaluate_news(pair)
    safety_reason = guard["message"] if guard["status"] == "BLACKOUT" else None
    market_status = automation.status()["market"]
    is_live = bool(
        market_status["connected"]
        and market_status["active_dataset_is_provider"]
        and market_status["data_fresh"]
    )
    try:
        scan = runtime.scan_strategies(
            pair,
            payload.horizon,
            generated_at=parse_datetime(clock["reference"]),
            is_live=is_live,
            safety_filter_reason=safety_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scan["news_guard"] = guard
    scan["automatic"] = False
    scan["clock"] = clock
    scan["recorded"] = None
    best = scan.get("best")
    if payload.record and best is not None:
        strategy_names = {
            item["id"]: item["name"] for item in strategy_catalog_payload()["executable"]
        }
        best_name = strategy_names.get(best["strategy"], best["strategy"])
        already_recorded = False
        if best.get("entry_time"):
            already_recorded = runtime.ledger.has_signal_for_time(
                pair, best["strategy"], scan["dataset_id"], parse_datetime(best["entry_time"])
            )
        if already_recorded:
            # Clicking the scan twice on the same candle must not pretend to
            # write a second paper trade: the ledger keeps one per entry.
            scan["recorded"] = {
                **best,
                "heading": f"BEST · {best_name} · {best['side']} · ALREADY RECORDED",
                "already_recorded": True,
                "news_guard": guard,
                "automatic": False,
                "broker_ticket": pocket_option_ticket(best),
            }
        else:
            try:
                recorded = runtime.generate_signal(
                    pair,
                    best["strategy"],
                    payload.horizon,
                    generated_at=parse_datetime(clock["reference"]),
                    is_live=is_live,
                    safety_filter_reason=safety_reason,
                )
            except ValueError as exc:
                scan["record_error"] = str(exc)[:240]
            else:
                recorded["heading"] = f"BEST · {best_name} · {recorded['side']}"
                recorded["already_recorded"] = False
                recorded["news_guard"] = guard
                recorded["automatic"] = False
                recorded["clock"] = clock
                recorded["broker_ticket"] = pocket_option_ticket(recorded)
                scan["recorded"] = recorded
    return scan


@app.post("/api/signal")
def generate_signal(payload: SignalRequest) -> dict:
    clock = runtime.clock()
    guard = automation.evaluate_news(payload.pair.upper().strip())
    safety_reason = guard["message"] if guard["status"] == "BLACKOUT" else None
    market_status = automation.status()["market"]
    try:
        signal = runtime.generate_signal(
            payload.pair,
            payload.strategy,
            payload.horizon,
            generated_at=parse_datetime(clock["reference"]),
            is_live=bool(
                market_status["connected"]
                and market_status["active_dataset_is_provider"]
                and market_status["data_fresh"]
            ),
            safety_filter_reason=safety_reason,
        )
        strategy_names = {item["id"]: item["name"] for item in strategy_catalog_payload()["executable"]}
        signal["heading"] = f"{strategy_names.get(payload.strategy, payload.strategy)} · {signal['side']}"
        signal["news_guard"] = guard
        signal["automatic"] = False
        signal["clock"] = clock
        signal["broker_ticket"] = pocket_option_ticket(signal)
        return signal
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/backtest")
def backtest(payload: BacktestRequest) -> dict:
    pair = payload.pair.upper().strip()
    try:
        with runtime.lock:
            if runtime.source == "SYNTHETIC DEMO":
                runtime.set_demo_pair(pair)
            elif pair != runtime.pair:
                raise ValueError(f"Imported CSV data is for {runtime.pair}; reset to demo to switch pairs")
            candles = runtime.backtest_candles(BACKTEST_WINDOW)
            report = run_walk_forward_backtest(
                candles=candles,
                strategy_id=payload.strategy,
                horizon=payload.horizon,
                payout=payload.payout,
                threshold=payload.threshold,
            )
            report["pair"] = runtime.pair
            report["data_source"] = runtime.source
            report["data_window_candles"] = len(candles)
            report["stored_dataset_candles"] = runtime.candle_count
            if runtime.candle_count > len(candles):
                report["note"] += f" The test used the latest {len(candles):,} candles to bound memory and runtime."
            return report
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/import")
async def import_csv(request: Request, pair: str = "CSV/UNKNOWN") -> dict:
    result = await _with_streamed_csv(
        request,
        MAX_IMPORT_BYTES,
        lambda path: runtime.import_csv_file(path, pair),
    )
    return {
        "status": "loaded",
        "pair": result["pair"],
        "source": result["source"],
        "candles_loaded": result["count"],
        "total_candles": result["count"],
        "dataset_id": result["dataset_id"],
        "latest": result["latest"].to_dict(),
        "note": "Stored locally in DuckDB. Historical CSV is not a live price feed.",
    }


@app.post("/api/candles/append")
async def append_candles(request: Request, pair: str = "") -> dict:
    result = await _with_streamed_csv(
        request,
        MAX_APPEND_BYTES,
        lambda path: runtime.append_csv_file(path, pair),
    )
    return {
        "status": "appended",
        "pair": result["pair"],
        "source": result["source"],
        "candles_added": result["count"],
        "candles_stored": result["candle_count"],
        "latest": result["latest"].to_dict(),
        "auto_resolved": result["auto_resolved"],
        "automatic_model_updates": result["automatic_model_updates"],
        "note": "Completed candles can auto-resolve matching expired paper signals and update the model. Synthetic demo data is never used for learning.",
    }


@app.post("/api/reset-demo")
def reset_demo(pair: str = "EUR/USD") -> dict:
    try:
        runtime.reset_demo(pair)
        automation.pause_market_feed()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "reset",
        "pair": runtime.pair,
        "source": runtime.source,
        "live_feed_connected": False,
        "note": "Stored CSV datasets remain archived; demo is now active and the live feed is paused until explicitly reactivated.",
    }


@app.get("/api/signals")
def recent_signals() -> dict:
    with runtime.lock:
        signals = runtime.ledger.list_recent()
        active_dataset = runtime.dataset_id()
        for signal in signals:
            signal["entry_time"] = signal.get("entry_time") or signal["generated_at"]
            signal["prediction_time"] = signal["expires_at"]
            signal["can_resolve"] = (
                signal["status"] == "PENDING"
                and signal["source"] == "IMPORTED CSV"
                and signal["source"] == runtime.source
                and signal["pair"] == runtime.pair
                and signal["dataset_id"] == active_dataset
            )
            signal.pop("dataset_id", None)
        return {"signals": signals}


@app.post("/api/feedback")
def paper_feedback(payload: FeedbackRequest) -> dict:
    with runtime.lock:
        existing = runtime.ledger.get(payload.signal_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Signal was not found")
        if existing["source"] == "SYNTHETIC DEMO":
            raise HTTPException(status_code=400, detail="Synthetic demo outcomes cannot train the model")
        if existing["pair"] != runtime.pair or existing["source"] != runtime.source:
            raise HTTPException(status_code=409, detail="Reactivate the matching imported candle dataset before resolving this signal")
        if existing["dataset_id"] != runtime.dataset_id():
            raise HTTPException(status_code=409, detail="This signal belongs to a different imported dataset")

        try:
            record = runtime.ledger.resolve(payload.signal_id, payload.exit_price)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            learning = runtime.learn_from_record(record)
        except ValueError as exc:
            learning = {"learned": False, "reason": str(exc)}

        note = "Paper outcome saved and scored before the online model update; this does not prove future accuracy."
        if not learning["learned"]:
            note = f"Paper outcome saved. {learning.get('reason', 'No model update was made for this outcome.')}"
        return {
            "status": "resolved",
            "outcome": record["outcome"],
            "learned_from_confirmed_paper_result": learning["learned"],
            "prequential_prediction_correct": learning.get("prediction_correct_before_update"),
            "training_samples": learning.get("training_samples"),
            "note": note,
        }
