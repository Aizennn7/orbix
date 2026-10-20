from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .automation import AutomationService, ProviderError
from .backtest import run_walk_forward_backtest
from .brokers import capabilities as broker_capabilities
from .brokers import pocket_option_ticket
from .domain import parse_datetime
from .env import load_env
from .future import build_future_plan
from .runtime import BACKTEST_WINDOW, Runtime
from .strategies import strategy_catalog_payload
from .telegram import TelegramError


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
MAX_APPEND_BYTES = 64 * 1024 * 1024

# Load ORBIX root .env before Runtime or AutomationService read configuration.
# Real process variables keep precedence, so `uvicorn --env-file` still works.
ENV_FILE = load_env()

# Created here rather than in runtime.py so importing Runtime (tests, tooling)
# does not open the live data store or fight a running preview for the lock.
runtime = Runtime()
automation = AutomationService(runtime)


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
        }


@app.get("/api/storage")
def storage_status() -> dict:
    with runtime.lock:
        summary = runtime.storage_summary()
        summary["datasets"] = runtime.candle_store.list_datasets(limit=50)
        return summary


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
        )
        reviews = runtime.discipline.reviews(pair=pair, days=days, reference=reference)
        contexts = runtime.discipline.contexts(pair=pair)
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
