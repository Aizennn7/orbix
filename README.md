# Orbix Signal Lab

Orbix is a beginner-friendly **signal research and paper-testing** starter app for one-minute FX candles. It studies completed candles and reports **BUY**, **SELL**, or **NO SIGNAL** with an entry time, prediction expiry, and a short reason.

## What the two reference projects do

- [Freqtrade](https://github.com/freqtrade/freqtrade) is a Python crypto bot with strategy rules, historical backtesting, dry-run trading, and optional adaptive ML. It is exchange-focused and does not provide a Quotex integration.
- [Intelligent Trading Bot](https://github.com/asavinov/intelligent-trading-bot) separates offline feature/label/model training from online prediction, combines model scores into signals, and includes rolling tests. Its examples focus on crypto and MT5 data.

Orbix follows those **architectural ideas** but uses its own code: shared candle features, future-only labels for training, chronological walk-forward testing, explicit paper feedback, and a signal-only output. It does not copy either project's source code.

The dashboard is split into seven hash-routed pages—Dashboard, Signal Engine, Backtest, Data Workspace, Connections, Paper Log, and Strategy Lab. Its black-hole visuals are drawn locally with Canvas/CSS; the animation pauses for hidden tabs and honors reduced-motion preferences.

## Run it

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn orbix.api:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` on the machine running the server.

## Current status

- The dashboard defaults to **synthetic demo prices**. They are clearly labelled and are not live market data.
- Four executable research prototypes are available: ML + trend agreement, trend pullback, range reversal, and a 20-candle breakout.
- Thirty strategy ideas are catalogued. The trend-pullback and range-reversal entries are labelled as approximate prototypes; the breakout-and-retest candidate is still research-only because the active breakout prototype does not require a retest.
- Signals use the latest completed M1 candle, become actionable at the next minute boundary, and are scheduled one horizon before prediction expiry (one minute by default). The API exposes `entry_time` and `prediction_time`; the UI shows the time while the stored entry quote remains available internally for paper outcome scoring.
- Walk-forward tests automatically rerun after importing/reactivating history or changing pair, strategy, or payout. They use earlier candles for training and later candles for testing. Reports include the observed win rate, a 95% Wilson interval, signal coverage, drawdown, and a last-20-trade check against the 19/20 target. Synthetic demo candles are excluded from automatic accuracy reports. Results are historical measurements, not a performance promise. The assumed payout is adjustable; it is not fetched from a broker.
- CSV import accepts `timestamp,open,high,low,close[,volume]` and needs at least 120 candles. Use UTC ISO-8601 candle-open timestamps; timezone-naive timestamps are assumed UTC. The browser uploads the file directly and the server validates/loads it in bounded chunks (1 GB maximum per import).
- Candle history is persisted in the local DuckDB archive at `data/candles.duckdb`; earlier imports remain stored when a new dataset or demo mode is selected, and the Data Workspace can reactivate an archived import. The app loads only a 720-candle signal window into memory, uses up to 5,000 recent candles for initial model fitting, and caps a walk-forward run at 2,500 candles.
- `POST /api/candles/append` accepts new completed candles for the active imported dataset. When the exact M1 candle corresponding to a signal's expiry arrives, Orbix auto-resolves that paper signal and performs one incremental model update. Tied outcomes and synthetic demo data never change model weights.
- The learning loop measures each prediction before applying its outcome label and exposes the update count and pre-update accuracy via `GET /api/learning`. Updates are not guarantees of improvement; recent errors should be reviewed and the model should be evaluated chronologically.
- The Connections page allows up to 20 API keys and shows global configuration status. A compact top-bar credit popup lists configured key slots, but usage and limits stay unavailable until a provider-specific quota adapter exists. Keys stay in browser memory for the current tab session; they are not persisted or sent.
- No live FX provider, provider quota feed, Quotex login, real-money order, or broker automation is included. DuckDB is used as a single-process local store; keep one Uvicorn worker for this file. Move to a server database such as PostgreSQL/TimescaleDB if multiple application processes need to write concurrently.

## API

- `GET /api/health`, `GET /api/storage`, and `GET /api/learning` — runtime, local archive, and online-update status.
- `POST /api/datasets/{dataset_id}/activate` — switch the active dataset to a previously archived import.
- `GET /api/market?pair=EUR/USD` — recent M1 candles from the active dataset.
- `GET /api/strategies` — executable prototypes and the 30-item research catalogue.
- `POST /api/signal` — generate a signal. Example body: `{"pair":"EUR/USD","strategy":"ml_consensus","horizon":1}`.
- `POST /api/backtest` — chronological paper test on the latest bounded research window. Example body: `{"pair":"EUR/USD","strategy":"ml_consensus","horizon":1,"payout":0.8}`.
- `POST /api/import?pair=EUR/USD` — stream a CSV into the persistent local candle archive.
- `POST /api/candles/append?pair=EUR/USD` — append later completed candles to the active imported dataset; this can auto-resolve matured signals and update the model.
- `GET /api/signals` and `POST /api/feedback` — view signals or manually resolve an imported-data paper outcome after expiry. A signal can be learned from only once; synthetic demo outcomes are excluded.
- `POST /api/reset-demo?pair=EUR/USD` — activate generated sample data without deleting archived CSV datasets.

## Tests

```bash
python -m unittest discover -s tests -v
```

This project is for software learning and market research, not financial advice. M1 signals are uncertain; the displayed model estimate is raw and uncalibrated, not a dependable probability or guarantee. Check current local rules and data-provider permissions before connecting any live source.
