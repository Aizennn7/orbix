# Orbix Signal Lab

Orbix is a signal-research and paper-testing dashboard for completed one-minute candles. It reports BUY, SELL, or NO SIGNAL, archives candle history locally, tests strategies chronologically, and can optionally refresh configured market and economic-calendar providers. It does not place broker orders.

## Strategy and safety

- **Orbix Confluence Gate (OCG)** is the project's original, explicitly coded rule set: EMA regime, multi-bar momentum, RSI band, directional ADX, candle quality, and a 20-bar breakout. It requires at least five of six checks, mandatory trend/ADX alignment, and abstains during extreme volatility. When trained, the prequential model can veto a rule signal if it disagrees; it never turns its raw estimate into a guaranteed probability.
- Four additional executable prototypes remain available: ML + trend agreement, trend pullback, range reversal, and a 20-candle breakout. Thirty more ideas are catalogued as research candidates, not proven strategies.
- No “sureshot” or 19/20 result is guaranteed. Markets change, providers differ, and even a perfect-looking small sample has wide uncertainty. The default backtest reports a Wilson interval and payout-adjusted results; it is a research tool, not evidence of future performance.
- Confirmed, non-tied outcomes from imported/provider candles update the online model at most once, after the expiry is known. This is test-before-learn behavior; it can adjust later estimates but cannot promise never to repeat an error. Synthetic demo outcomes never train the model.
- M1 entry time is the next candle boundary after the most recent completed candle; the default prediction/expiry is one candle (one minute) later. Signal generation time is recorded separately from scheduled entry time. Automatic signals arriving more than the configured late-entry limit (10 seconds by default) are discarded instead of being backdated. The browser displays local timezone; API timestamps are UTC.
- High-impact economic events are a **no-signal/blackout filter**, not a directional news prediction. CPI, central-bank decisions, employment releases, and similar events can produce unpredictable whipsaws; Orbix abstains around relevant high-impact releases rather than guessing “up” or “down.”

## Optional automated web data

Built-in server-side adapters use [Twelve Data's time-series endpoint](https://twelvedata.com/docs/market-data/time-series) for UTC one-minute OHLCV CSV and [Finnhub's economic calendar](https://finnhub.io/docs/api/economic-calendar). Provider plans, quotas, symbols, and access rights vary. Finnhub labels its economic-calendar endpoint as premium in its documentation; a working API key/plan is required. Orbix makes no provider request until a server key is configured.

1. Copy `.env.example` to `.env` and add your own Twelve Data and Finnhub keys. Do not paste keys into the browser-only notes form, commit `.env`, or share secrets in chat.
2. Install requirements and start one Uvicorn worker with `uvicorn --env-file .env orbix.api:app --host 0.0.0.0 --port 8000`.
3. The server polls closed M1 bars every 60 seconds (configurable, minimum 60) and the calendar every 15 minutes (configurable). It filters out incomplete candles, imports a provider dataset on first sync, and appends only new bars to that provider dataset. If a manual CSV is active, background sync will not overwrite it; click **Sync / Activate Live Feed** to intentionally switch.
4. Auto-signals run only when the provider dataset is active, the candle is fresh, and a recent event calendar is available with no active blackout. One evaluation is made per newly closed bar. On a provider or calendar error, automation pauses and reports the cause.
5. Browser notifications require clicking **Enable Alerts**, granting browser permission, and keeping the dashboard open. The in-app signal feed continues while the page is open; no web-push service is configured for a closed browser/device.

Environment settings are in `.env.example`: pair, polling intervals, event-time timezone, pre/post news-blackout windows, maximum candle age, maximum late-entry delay, auto strategy, and whether automated signals are enabled. Finnhub event times without an explicit timezone use `ORBIX_FINNHUB_TIMEZONE`; verify that setting against the provider's account/feed. Credentials stay server-side. The manual **Return to demo data** action pauses the provider loop until you explicitly sync it again.

## Run it without live credentials

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn orbix.api:app --host 0.0.0.0 --port 8000
```

Without provider keys, Orbix remains in clearly labelled synthetic demo mode; automatic live signals are off. You can still import a validated local CSV or inspect the application with demo data. No result from synthetic candles is real-market accuracy.

## Data, tests, and API

Candle history and auto-resolved paper outcomes are stored locally in DuckDB/SQLite under ignored `data/`. Keep one Uvicorn worker because this is a single-process local store. CSV upload accepts `timestamp`/`datetime`, `open`, `high`, `low`, `close`, and optional `volume`; use UTC candle-open timestamps and at least 120 ordered candles. Earlier imported datasets are archived when another dataset is activated.

Walk-forward tests train on earlier data and test on later data; automatic UI reruns happen after import/reactivation and pair/strategy/payout changes, then after each 50 additional closed feed bars to avoid running a full walk-forward job every minute. Manual rerun remains available. Synthetic candles are excluded from automatic accuracy reporting.

```bash
python -m unittest discover -s tests -v
```

Useful endpoints:

- `GET /api/health`, `/api/storage`, `/api/learning`, `/api/automation/status`
- `GET /api/market?pair=EUR/USD`, `/api/strategies`, `/api/signals`
- `POST /api/automation/sync` and `/api/automation/calendar/refresh`
- `POST /api/signal` and `/api/backtest`
- `POST /api/import?pair=EUR/USD`, `/api/candles/append?pair=EUR/USD`, `/api/feedback`
- `POST /api/reset-demo?pair=EUR/USD`

This project is for research and software learning, not financial advice. One-minute signals can lose; no model, indicator, or news feed can ensure profit or eliminate repeat errors. Verify provider timestamps and quotes against the intended venue before interpreting paper results.
