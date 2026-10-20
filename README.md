# Orbix Signal Lab

Orbix is a signal-research and paper-testing dashboard for completed one-minute candles. It reports BUY, SELL, or NO SIGNAL, archives candle history locally, tests strategies chronologically, and can optionally refresh configured market and economic-calendar providers. It does not place broker orders.

## Strategy and safety

- **Orbix Confluence Gate (OCG)** is the project's original, explicitly coded rule set: EMA regime, multi-bar momentum, RSI band, directional ADX, candle quality, and a 20-bar breakout. It requires at least five of six checks, mandatory trend/ADX alignment, and abstains during extreme volatility. When trained, the prequential model can veto a rule signal if it disagrees; it never turns its raw estimate into a guaranteed probability.
- Four additional executable prototypes remain available: ML + trend agreement, trend pullback, range reversal, and a 20-candle breakout. Thirty more ideas are catalogued as research candidates, not proven strategies.
- No “sureshot” or 19/20 result is guaranteed. Markets change, providers differ, and even a perfect-looking small sample has wide uncertainty. The default backtest reports a Wilson interval and payout-adjusted results; it is a research tool, not evidence of future performance.
- Confirmed, non-tied outcomes from imported/provider candles update the online model at most once, after the expiry is known. This is test-before-learn behavior; it can adjust later estimates but cannot promise never to repeat an error. Synthetic demo outcomes never train the model.
- Probabilities are calibrated with isotonic regression once at least 60 resolved, non-synthetic outcomes exist. Calibration is fitted on the *raw* model score and describes how often past estimates at that level were right; it is not a promise about the next candle. Before 60 outcomes the signal is marked **raw**. If pool-adjacent-violators merges every bin into one level, the map is labelled **flat**: expected calibration error looks low only because every score receives the same base rate. The gate then abstains instead of presenting a constant as a ranking edge.
- M1 entry time is the next candle boundary after the most recent completed candle; the default prediction/expiry is one candle (one minute) later. Signal generation time is recorded separately from scheduled entry time. Automatic signals arriving more than the configured late-entry limit (10 seconds by default) are discarded instead of being backdated. The UI uses UTC, the bot's clock, and the header shows server, data, and your local time side by side. When the active imported file is historical the bot enters **REPLAY**: `generated_at` follows the last closed candle instead of wall-clock time, so generated, entry, expiry, and journal times agree, and the exact gap is labelled rather than hidden.
- High-impact economic events are a **no-signal/blackout filter**, not a directional news prediction. CPI, central-bank decisions, employment releases, and similar events can produce unpredictable whipsaws; Orbix abstains around relevant high-impact releases rather than guessing “up” or “down.”

## Optional automated web data

Built-in server-side adapters use [Twelve Data's time-series endpoint](https://twelvedata.com/docs/market-data/time-series) for UTC one-minute OHLCV CSV and [Finnhub's economic calendar](https://finnhub.io/docs/api/economic-calendar). Provider plans, quotas, symbols, and access rights vary. Finnhub labels its economic-calendar endpoint as premium in its documentation; a working API key/plan is required. Orbix makes no provider request until a server key is configured.

1. Install requirements and start one Uvicorn worker with `uvicorn orbix.api:app --host 0.0.0.0 --port 8000`.
2. Open **Data connections** in the dashboard and enter the Twelve Data, Finnhub, and optional Telegram credentials. **SAVE & APPLY** writes them to `provider_settings.json` in the active data directory (mode 600, ignored by Git, unencrypted at rest), applies them to the running service without a restart, and **TEST ACCESS** checks provider read access and bot/channel posting rights without posting a message or storing candles. Each test makes one lightweight provider request per configured provider. The page only ever receives masks of the saved keys; the full values never return to the browser. Server environment variables take precedence and cannot be replaced from the page.
3. For a headless or container deployment, copy `.env.example` to `.env` in the project root instead and add the keys there. The server loads this file on startup; existing shell/Docker variables take precedence. `--env-file .env` is optional, and `ORBIX_ENV_FILE=/path/to/file` selects a different file. Never commit `.env` or share secrets in chat.
4. The server polls closed M1 bars every 60 seconds (configurable, minimum 60) and the calendar every 15 minutes (configurable). It filters out incomplete candles, imports a provider dataset on first sync, and appends only new bars to that provider dataset. If a manual CSV is active, background sync will not overwrite it; click **Sync / Activate Live Feed** to intentionally switch.
5. Auto-signals run only when the provider dataset is active, the candle is fresh, and a recent event calendar is available with no active blackout. One evaluation is made per newly closed bar. On a provider or calendar error, automation pauses and reports the cause.
6. Browser notifications require clicking **Enable Alerts**, granting browser permission, and keeping the dashboard open. The in-app signal feed continues while the page is open; no web-push service is configured for a closed browser/device.

Environment settings are in `.env.example`: pair, polling intervals, event-time timezone, pre/post news-blackout windows, maximum candle age, maximum late-entry delay, auto strategy, and whether automated signals are enabled. Finnhub event times without an explicit timezone use `ORBIX_FINNHUB_TIMEZONE`; verify that setting against the provider's account/feed. Credentials stay server-side. Precedence is server environment, then keys saved from the Connections page, then unconfigured. The manual **Return to demo data** action pauses the provider loop until you explicitly sync it again.

## Live deployment checklist

Orbix can run on a real Twelve Data feed with a live news guard, but it does **not** place broker orders. Treat this as a signal and paper-trading system, not a trading bot. Before pointing it at real money:

1. **Credentials.** Save and test the Twelve Data, Finnhub, and optional Telegram credentials on **Data connections**. All required preflight checks must be green; a blocked check means no automatic signals.
2. **Network exposure.** Set `ORBIX_DASHBOARD_PASSWORD` (and optionally `ORBIX_DASHBOARD_USER`) to turn on the login page. With auth enabled, pages redirect to `/login`; the browser gets an HttpOnly, SameSite=Lax `orbix_session` cookie after signing in, state-changing API calls must carry the `X-Orbix-CSRF` header, and `/logout` ends the session. Scripts can keep using HTTP Basic auth. For a shared server prefer a PBKDF2 hash over plaintext: run `python -m orbix.security`, put the printed value in `ORBIX_DASHBOARD_PASSWORD_HASH`, and leave `ORBIX_DASHBOARD_PASSWORD` empty. Session cookies are signed with `ORBIX_SESSION_SECRET`, or with a generated `0600` file inside `ORBIX_DATA_DIR` when that variable is empty. Serve the dashboard only behind HTTPS, for example with a reverse proxy that terminates TLS; set `ORBIX_COOKIE_SECURE=1` if HTTPS is terminated upstream. Without a password or hash, anyone who can reach the URL can replace keys, pause signals, or post to Telegram.
3. **One process.** Run exactly one Uvicorn worker. DuckDB, SQLite, and the JSON markers are single-process stores. Keep the data directory on persistent storage and back it up before upgrades; it contains candles, paper outcomes, learned models, and `provider_settings.json`.
4. **Clocks.** The live path evaluates a closed M1 bar within 10 seconds of its boundary (`ORBIX_MAX_ENTRY_DELAY_SECONDS`). Keep NTP enabled on the host. A slow provider response is skipped rather than signalled late; raise the limit only if you understand that a late entry shortens the expiry window.
5. **Feed behaviour.** Automatic sync never overwrites a manual CSV. The first provider sync activates a new dataset; later syncs append only new closed bars. A restart reuses the recorded provider dataset instead of re-importing it.
6. **Failure handling.** If the calendar refresh fails, it retries within 60 seconds. Automatic signals stay paused while the calendar is unavailable, stale, or blacked out, and while the feed is stale or paused.
7. **Human limits.** Sessions, a daily budget, minimum spacing, a loss stop, cool-offs, and dataset-scoped open-trade checks are on by default. They can only remove trades. They cannot create an edge.
8. **Execution is manual.** `GET /api/broker` reports `official_api: false` and `order_execution: false`. Pocket Option has no official public trading API, so Orbix shows a manual CALL/PUT ticket instead of sending an order. No cookie scraping or unofficial order bridge is included.
9. **No profit claim.** The collected cadence sample was 149 signals, 43.4% wins (95% CI 35.3–51.8%), below the 55.6% break-even at an 80% payout. That is a measurement, not a forecast. Start in paper mode and keep the size small until you have your own resolved record.

`GET /api/preflight` reports the configuration and warm-up checks; `/api/health` exposes `live_feed_connected`, `event_calendar_connected`, `dashboard_auth_enabled`, and `clock`. Neither is a performance forecast.

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

## Pocket Option handoff (manual only)

Pocket Option has no official public trading API. `GET /api/broker` reports that plainly, and the Engine page builds a copyable **CALL/PUT ticket** (asset, direction, expiry, entry in UTC) from the current signal. Orbix does not log in, store a session cookie, or send orders; the `orders_sent` flag is always false. Unofficial session automation is deliberately not implemented: it would require your broker credentials, break the broker's terms, and put real money behind an unverified signal stream. Binary options can expire worthless, and speculative trading is restricted for residents of some countries, including India. The handoff is a convenience, not a claim that the signal is profitable.

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
- **Automatic imported-dataset loop.** A 30-second scanner watches the active imported dataset even when no market-provider key is set. A static historical file is left idle; when a streaming append adds a fresh closed bar, the configured strategy generates a paper-only signal without a click. Imported reads are marked `is_live=false`, the news guard is recorded but not required for paper output, and Telegram auto-posting is explicitly refused. A live provider feed keeps its stricter path: fresh data, a clear news guard, and the normal entry-delay check.
- **Learning from every completed bar.** On every append, Orbix labels each bar whose expiry is known and has not been seen before, scores it with the current champion *before* updating, and feeds the result through the same replay guard. This happens whether or not a signal was delivered, so a quiet or abstaining gate no longer starves the learner. Bars already learned through paper or shadow outcomes are skipped rather than double-weighted; gaps that would fake a one-minute horizon are skipped; ties are recorded but do not update weights. The cursor and deterministic event ids live in SQLite, so a restart cannot relearn a bar. Learning is incremental from the import point, not a silent backfill of the whole file. Calibration refits from the shared replay on its normal cadence, and the dataset stream has its own tracker, drift monitor, and `/api/model` + `/api/learning` counters. `ORBIX_DATASET_LEARNING=false` disables it.
- **Counterfactual (shadow) reads.** A flat calibration map blocks every model signal, which stops the outcome stream the map needs to recover. On imported data, when a model strategy hits exactly that `flat_calibration` abstention, Orbix stores the raw, uncalibrated read in `shadow_signals`, resolves it at the same expiry, and feeds the label into the model, replay guard, and calibration. Shadow rows are labelled separately: they never enter paper signals, win rate, Telegram delivery, per-strategy records, or the live drift monitor. `/api/model` and `/api/learning` expose shadow totals, resolved/learned counts, and a shadow-only accuracy/Brier tracker. They are counterfactual evidence, not trades.

- **Promotion and rollback.** Retraining fits a challenger on the older 85% of imported candles and validates it on the most recent 15%. The challenger replaces the champion only if held-out log loss improves and Brier score does not worsen. Each promoted snapshot is versioned under `data/models/`; the last 12 are kept. `POST /api/model/rollback` restores the previous snapshot. The comparison is deliberately conservative because the champion may already have learned from some recent bars online.
- **Scheduled maintenance.** The background loop checks calibration and drift every 15 minutes while imported data is active. It uses the dataset-wide stream once it has at least 40 observations, so drift can be detected even when few signals were delivered. An automatic retrain is attempted only after drift plus at least 100 newly resolved outcomes, and still passes through the same validation gate.
- **Live preflight.** `GET /api/preflight` checks the market-provider key, active dataset, bar freshness, calendar connection, news guard, scheduler switch, champion model, calibration state, and optional Telegram delivery. It also reports how many resolved outcomes are still needed before online updates and calibration begin, and includes a non-blocking `paper_automation` check plus the dataset-learning counters. Readiness is a configuration check, not a performance forecast; on synthetic demo data it is expected to report blockers.
- **All-strategy scan.** `POST /api/signals/scan` evaluates every executable method on the same closed candle with the same model, ensemble, and news guard, and returns each method's side, reason, calibrated probability, and A/B/C conviction. The **TEST ALL STRATEGIES** button on the Engine page renders that comparison. The scan writes nothing; only the strictest qualified read is recorded, once, as a paper signal, and loading the same candle twice is reported as already recorded instead of creating a duplicate. Ordering is `orbix_confluence`, `ml_consensus`, `trend_pullback`, `range_reversal`, `breakout`, then `orbix_cadence`, so the deliberately loose cadence read can never outrank a rule-confirmed setup.
- **No fabricated accuracy.** These metrics describe recorded paper outcomes. They do not include spread, payout, or execution, and they cannot establish a future win rate.

A real-data shakeout on 1,500 streamed EUR/USD M1 bars (after importing 87,692 historical bars), driven through the default three-minute cadence, produced **149 paper signals** — 59 wins, 77 losses, 13 ties, **43.4%** (95% CI 35.3–51.8%), net −4.9 pips. That interval sits entirely below the 55.6% break-even at an 80% payout, so the stream is not evidence of an edge; it is evidence that the cadence runs. The stricter 5/6 gate produced 57 signals on the same stream, and a 6-per-day discipline preset cut that to 6. Dataset learning still consumed 1,188 resolved outcomes and kept 1,160; dataset accuracy was 54.0% (95% CI 51.1–56.8%) with Brier 0.261 and negative Brier skill. The paper record's Brier was 0.261, expected calibration error 0.119, and Brier skill −0.043. No prior profit claim is implied.

## Cadence, limits, and a journal

The automatic path can run at two speeds. The default is a **three-minute cadence collection** (`orbix_cadence`, surfaced on the **Trader** page and `GET /api/trader`). A separate layer (`orbix/discipline.py`) paces and limits it, and can only remove a trade:

- **Sessions.** Weekdays only, inside the UTC windows `07:00-11:00` and `12:30-16:30` (London and London/New York). The first 10 minutes of a window are left to settle. `ORBIX_TRADING_SESSIONS=all` removes the clock.
- **Cadence.** At most one paper signal every 3 minutes and 200 a day, with one open trade at a time. The cadence strategy takes the calibrated model's direction when the edge is at least 0.005 (0.01 on an uncalibrated read), and reports whether it agrees with the short-term EMA. It is deliberately less selective than the 5/6 gate.
- **Hard stops.** Twelve losses in a row trigger a 10-minute cool-off; 100 losses end the day; a strategy/session context is vetoed after 200 resolved trades below 40%. These are catastrophic brakes, not a safety claim.
- **Conviction, not accuracy.** Each taken trade gets an A/B/C grade from the distance of the calibrated estimate from 50% and the ensemble spread. It is a label, not a probability of profit.
- **Journal.** Taken trades and meaningful passes are written to the audit log, and `/api/trader` returns seven days of derived reviews (trades, wins/losses, net pips, sessions, and a plain-language lesson).

This profile collects a stream; it does not make each read accurate. On the same 1,500-bar real stream it produced 149 signals at 43.4% (95% CI 35.3–51.8%) and −4.9 pips, with the interval entirely below break-even. To run the stricter profile instead, set `ORBIX_AUTO_STRATEGY=orbix_confluence`, `ORBIX_MAX_SIGNALS_PER_DAY=6`, `ORBIX_MIN_SIGNAL_SPACING_MINUTES=20`, `ORBIX_MAX_DAILY_LOSSES=3`, `ORBIX_COOLDOWN_AFTER_LOSSES=2`, `ORBIX_COOLDOWN_MINUTES=30`, `ORBIX_CONTEXT_MIN_SAMPLES=20`, and `ORBIX_CONTEXT_MIN_WIN_RATE=0.45`. The learner still consumes every completed bar either way, so pacing cannot starve the model.

## Walk-forward study

`orbix.study` is a separate research harness. It walks one series chronologically, refits the model on a rolling window of matured labels, recalibrates from the same 200-outcome replay the runtime uses, and scores every variant on identical entry/exit prices. It reports the production-gated path, a raw counterfactual read, the pure raw score, and six baselines, with a Wilson interval, net pips after an explicit spread, and a t-statistic. The verdict refuses a tradeable-edge claim unless the interval clears break-even, the cost-adjusted mean is positive with t ≥ 2, and the model beats the best baseline.

```bash
python -m orbix.study --csv /path/to/EURUSD_M1.csv --tz-offset 5 \
  --horizon 5 --spread-pips 1.0 --payout 0.80 --out data/studies/eurusd.json
```

A local run on third-party HistData-derived 2024 EUR/USD M1 data (372,379 rows; not committed because its licence is unresolved), with a 40,000-bar warm-up, two-day refits, a five-minute horizon, 1.0 pip spread, and an 80% payout assumption:

| Variant | Signals | Win rate | 95% low | Net pips/signal | Units |
|---|---:|---:|---:|---:|---:|
| Production gate | 2,798 | 49.4% | 47.5% | −1.031 | −299.8 |
| Raw counterfactual | 1,560 | 47.5% | 45.0% | −1.061 | −219.8 |
| Pure raw score | 87,410 | 51.2% | 50.8% | −0.988 | −6,682.0 |
| Always up | 332,332 | 50.5% | 50.3% | −1.006 | −29,143.4 |
| RSI reversal (best baseline) | 32,451 | 52.4% | 51.8% | −0.910 | −1,776.6 |

Verdict: **no tradeable edge after costs**. Break-even at an 80% payout is 55.56%; nothing in the study reached it, and the pure score's gross edge was about +0.012 pips before the spread. The model abstained or was flat for most bars (2.6% flat) and the gate allowed 0.84% coverage. This is one year, one pair, and one cost assumption; a future run can differ, which is exactly why the harness reports intervals instead of a headline accuracy.

## Data, tests, and API

Candle history and auto-resolved paper outcomes are stored locally in DuckDB/SQLite under ignored `data/` by default; set `ORBIX_DATA_DIR` to use another location. `ORBIX_AUTO_IMPORTED` (default true) controls the imported-dataset scanner, `ORBIX_IMPORTED_MAX_ENTRY_DELAY_SECONDS` (default 120) allows a paper feed more delivery slack than the live 10-second limit, and `ORBIX_DATASET_LEARNING` (default true) controls every-bar learning. Keep one Uvicorn worker because this is a single-process local store. CSV upload accepts `timestamp`/`datetime`, `open`, `high`, `low`, `close`, and optional `volume`; use UTC candle-open timestamps and at least 120 ordered candles. `GET /api/trader` reports the discipline state, journal, context memory, and seven daily reviews; `/api/automation/status` includes the same live verdict plus the bot clock, and `/api/broker` describes the manual Pocket Option handoff. In REPLAY the clock follows the active imported file, and `paper_automation_active` is false until a fresh bar arrives; the status exposes `paper_automation_waiting` instead. Earlier imported datasets are archived when another dataset is activated.

Walk-forward tests train on earlier data and test on later data; automatic UI reruns happen after import/reactivation and pair/strategy/payout changes, then after each 50 additional closed feed bars to avoid running a full walk-forward job every minute. Manual rerun remains available. Synthetic candles are excluded from automatic accuracy reporting.

```bash
python -m unittest discover -s tests -v
```

Useful endpoints:

- `GET /api/health`, `/api/storage`, `/api/learning`, `/api/automation/status`
- `GET /api/providers` (masked status), `POST /api/providers`, `DELETE /api/providers`, `POST /api/providers/test`
- `GET /api/model?pair=EUR/USD` (calibration, drift, contributions, versions, shadow and dataset-learning counters, per-strategy record)
- `POST /api/model/retrain`, `/api/model/rollback`, `/api/model/maintenance`
- `GET /api/preflight` (provider, calendar, model, calibration and delivery readiness)
- `GET /api/market?pair=EUR/USD`, `/api/strategies`, `/api/signals`
- `POST /api/automation/sync` and `/api/automation/calendar/refresh`
- `POST /api/signal`, `/api/signals/scan` (all executable strategies on one closed candle), and `/api/backtest`
- `POST /api/import?pair=EUR/USD`, `/api/candles/append?pair=EUR/USD`, `/api/feedback`
- `POST /api/reset-demo?pair=EUR/USD`
- `GET /api/telegram/status`, `GET /api/future/plan?count=6`, `GET /api/abstentions?days=7`
- `POST /api/telegram/send`, `/api/telegram/test`, `/api/telegram/auto`

This project is for research and software learning, not financial advice. One-minute signals can lose; no model, indicator, or news feed can ensure profit or eliminate repeat errors. Verify provider timestamps and quotes against the intended venue before interpreting paper results.
