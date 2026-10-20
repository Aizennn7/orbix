# Orbix Signal Lab

Orbix is a signal-research and paper-testing dashboard for completed one-minute candles. It reports BUY, SELL, or NO SIGNAL, archives candle history locally, tests strategies chronologically, and can optionally refresh configured market and economic-calendar providers. It does not place broker orders.

## Strategy and safety

- **Orbix Confluence Gate (OCG)** is the project's original, explicitly coded rule set: EMA regime, multi-bar momentum, RSI band, directional ADX, candle quality, and a 20-bar breakout. It requires at least five of six checks, mandatory trend/ADX alignment, and abstains during extreme volatility. When trained, the prequential model can veto a rule signal if it disagrees; it never turns its raw estimate into a guaranteed probability.
- Four additional executable prototypes remain available: ML + trend agreement, trend pullback, range reversal, and a 20-candle breakout. Thirty more ideas are catalogued as research candidates, not proven strategies.
- No “sureshot” or 19/20 result is guaranteed. Markets change, providers differ, and even a perfect-looking small sample has wide uncertainty. The default backtest reports a Wilson interval and payout-adjusted results; it is a research tool, not evidence of future performance.
- Confirmed, non-tied outcomes from imported/provider candles update the online model at most once, after the expiry is known. This is test-before-learn behavior; it can adjust later estimates but cannot promise never to repeat an error. Synthetic demo outcomes never train the model.
- Probabilities are calibrated with isotonic regression once at least 60 resolved, non-synthetic outcomes exist. Calibration is fitted on the *raw* model score and describes how often past estimates at that level were right; it is not a promise about the next candle. Before 60 outcomes the signal is marked **raw**. If pool-adjacent-violators merges every bin into one level, the map is labelled **flat**: expected calibration error looks low only because every score receives the same base rate. The gate then abstains instead of presenting a constant as a ranking edge.
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

## Telegram delivery and the Future page

Two pages are separate from the signal engine and change none of its rules.

**Telegram** (`#telegram`) publishes already-recorded M1 signals to one channel through a server-side admin bot. The token and channel id are read only from `ORBIX_TELEGRAM_BOT_TOKEN` and `ORBIX_TELEGRAM_CHAT_ID`; the browser never receives them. Add the bot to the channel as an administrator with "Post messages". One-click sends are deduplicated by signal id, synthetic demo rows are blocked unless explicitly allowed, and timestamps follow `ORBIX_TELEGRAM_TIMEZONE` (default `Asia/Kolkata`). Automatic posting is off until enabled on the page or with `ORBIX_TELEGRAM_AUTO_POST=true`, and a paper result follows after an expiry resolves when the original signal was delivered. This is a publishing channel, not an order route.

**Delivery guardrails.** Automatic posting is capped per UTC day (`ORBIX_TELEGRAM_DAILY_CAP`, default 40 messages) and spaced by `ORBIX_TELEGRAM_COOLDOWN_SECONDS` (default 60). Manual one-click sends count toward the same cap and are refused once it is reached; the connection test is exempt. When a paper outcome resolves for a signal that was delivered, Orbix edits the original message to show the result instead of posting a second one, and only falls back to a new message if Telegram refuses the edit.

**Abstention ledger.** Every `NO SIGNAL` decision is stored with a stable reason code (volatility band, model veto, ensemble uncertainty, flat calibration, score below threshold, news blackout, stale bar, duplicate entry, provider unavailable), the UTC session, and the hour. `GET /api/abstentions` reports totals, a per-hour histogram, a per-session split, the share of abstentions relative to decided signals, and the resolved paper win/loss record for each session. Abstentions never enter the paper-signal or learning tables.

**Future** (`#future`) analyses one pair at a time and lists the next minute boundaries as ARMED or BLOCKED. It never fills in a direction for a candle that has not closed: the gate runs after the close, exactly as it does for automatic signals. It also replays the gate over the most recent closed bars and shows walk-forward hit rate, its Wilson 95% interval, signal count, break-even rate, and the payout assumption. The replay uses the same calibrated probability and ensemble-uncertainty limit as live signals, so a bar the live gate would have skipped is shown as NO SIGNAL with the gate's reason. The replay is a transparency check, not a forecast.

Liquidity ordering in the Future page uses the BIS Triennial Central Bank Survey, where EUR/USD has the largest share of turnover. Liquidity lowers spread and gap cost; it does not make a pair's next minute more predictable. Claims such as "95% accuracy" or "sureshot" seen on Telegram channels are marketing, not verified performance.

## Prediction engine and self-learning

- **Inputs.** Feature set **v2** feeds 26 values into a logistic model: multi-bar returns, EMA gap/slope, RSI level and slope, candle shape and wicks, ATR/volatility ratios, directional ADX, volume ratio, VWAP distance, 20-bar range position, streak, lag-1 autocorrelation, and time-of-day sine/cosine. Every feature uses only the current and earlier candles; labels use the close one horizon later.
- **Explainability.** Each read returns the signed contribution of every feature (standardised value × weight, largest absolute first), so a BUY/SELL can be traced instead of asserted. Standardised inputs are bounded at ±8 so one abnormal bar cannot dominate the score.
- **Uncertainty.** On imported data, three bootstrap models are fitted once per dataset and refreshed on the 15-minute maintenance cycle or after a retrain. Their disagreement on raw member scores is reported as an ensemble spread. If the spread exceeds `ORBIX_MAX_UNCERTAINTY` (default 0.10), the engine abstains instead of guessing; uncertainty can only remove a signal, never create one. Backtests report how many model bars the ensemble covered, how many crossed the limit, and how many would-be signals the gate removed (a recent demo run crossed the limit on many bars but removed only a fraction of would-be signals), so the filter is measured rather than assumed to help.
- **Honest evaluation.** Accuracy, Brier score, log loss, expected calibration error, and Brier skill are computed prequentially: the probability issued with the signal is scored, then the model learns. Nothing is scored with a model that has already seen the outcome. Per-strategy statistics are joined from the SQLite ledger and include Wilson 95% intervals.
- **Guarded online updates.** After every resolved outcome, an update is kept only if replay log loss on the last 200 stored feature rows does not degrade by more than `ORBIX_MAX_UPDATE_DEGRADATION` (default 2%). The replay guard starts after `max(20, ORBIX_MIN_LEARNING_SAMPLES)` stored outcomes (default 30); before that, each outcome updates the running model without a rollback check. When the guard fails the weights are restored and the rejection is counted and shown.
- **Drift and decay.** A two-window error monitor compares the last 40 resolved predictions with the preceding 40. A rise of more than 10 percentage points is labelled `drift`; a fall is `improving`. This is a signal to inspect, not proof of a regime change.
- **Calibration refit.** The isotonic mapping is refitted from stored resolved outcomes every `ORBIX_RECALIBRATE_EVERY` accepted updates (default 50) and whenever an existing mapping has drifted. With 140 or more stored outcomes the fit uses the older 70% and must not worsen either Brier score or expected calibration error on the newer 30%; otherwise the previous mapping is kept. A fit that collapses to a single level is stored and reported as flat, and the gate abstains until the score regains rank structure. Demo outcomes are never used.
- **Promotion and rollback.** Retraining fits a challenger on the older 85% of imported candles and validates it on the most recent 15%. The challenger replaces the champion only if held-out log loss improves and Brier score does not worsen. Each promoted snapshot is versioned under `data/models/`; the last 12 are kept. `POST /api/model/rollback` restores the previous snapshot. The comparison is deliberately conservative because the champion may already have learned from some recent bars online.
- **Scheduled maintenance.** The background loop checks calibration and drift every 15 minutes while imported data is active. An automatic retrain is attempted only after drift plus at least 100 newly resolved outcomes, and still passes through the same validation gate.
- **Live preflight.** `GET /api/preflight` checks the market-provider key, active dataset, bar freshness, calendar connection, news guard, scheduler switch, champion model, calibration state, and optional Telegram delivery. It also reports how many resolved outcomes are still needed before online updates and calibration begin. Readiness is a configuration check, not a performance forecast; on synthetic demo data it is expected to report blockers.
- **No fabricated accuracy.** These metrics describe recorded paper outcomes. They do not include spread, payout, or execution, and they cannot establish a future win rate.

## Data, tests, and API

Candle history and auto-resolved paper outcomes are stored locally in DuckDB/SQLite under ignored `data/` by default; set `ORBIX_DATA_DIR` to use another location. Keep one Uvicorn worker because this is a single-process local store. CSV upload accepts `timestamp`/`datetime`, `open`, `high`, `low`, `close`, and optional `volume`; use UTC candle-open timestamps and at least 120 ordered candles. Earlier imported datasets are archived when another dataset is activated.

Walk-forward tests train on earlier data and test on later data; automatic UI reruns happen after import/reactivation and pair/strategy/payout changes, then after each 50 additional closed feed bars to avoid running a full walk-forward job every minute. Manual rerun remains available. Synthetic candles are excluded from automatic accuracy reporting.

```bash
python -m unittest discover -s tests -v
```

Useful endpoints:

- `GET /api/health`, `/api/storage`, `/api/learning`, `/api/automation/status`
- `GET /api/model?pair=EUR/USD` (calibration, drift, contributions, versions, per-strategy record)
- `POST /api/model/retrain`, `/api/model/rollback`, `/api/model/maintenance`
- `GET /api/preflight` (provider, calendar, model, calibration and delivery readiness)
- `GET /api/market?pair=EUR/USD`, `/api/strategies`, `/api/signals`
- `POST /api/automation/sync` and `/api/automation/calendar/refresh`
- `POST /api/signal` and `/api/backtest`
- `POST /api/import?pair=EUR/USD`, `/api/candles/append?pair=EUR/USD`, `/api/feedback`
- `POST /api/reset-demo?pair=EUR/USD`
- `GET /api/telegram/status`, `GET /api/future/plan?count=6`, `GET /api/abstentions?days=7`
- `POST /api/telegram/send`, `/api/telegram/test`, `/api/telegram/auto`

This project is for research and software learning, not financial advice. One-minute signals can lose; no model, indicator, or news feed can ensure profit or eliminate repeat errors. Verify provider timestamps and quotes against the intended venue before interpreting paper results.
