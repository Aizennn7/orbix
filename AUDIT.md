# Orbix — Status Report

**Date:** 2026-10-26 · **Branch:** `arena/915a9e38-orbix`
**Target:** manual trading — Orbix produces signals, you place the trades yourself.

---

## Present condition

**Green. Every finding from the audit is now closed, and the suite passes.**

| | |
|---|---|
| Tests | **178 passing, 0 failing** (was 161 with 19 failing at audit start) |
| CI coverage | ✅ every push and PR, no path filter |
| Azure deploys | ✅ unblocked |
| App imports | ✅ FastAPI loads, 50 routes |
| Mobile PWA builds | ✅ Vite, 10 modules |
| Secrets in repo | ✅ none |
| Dev/prod code path | ✅ same |
| Broker orders sent | ✅ never — by design |
| Web assets referenced that 404 | ✅ **0** (was 2) |
| Host paths published to GitHub Pages | ✅ **0** |
| Error-masking operators in CI | ✅ **0** (was 62) |
| Undocumented env vars | ✅ **0** (22 read in code, 63 documented) |
| Naive `datetime.utcnow()` | ✅ **0** |
| Tracked files | 139 |

Verified against a running server, not just the test suite: 18 endpoints swept for
error-shaped fields, and all 8 that exist are `null`. No swallowed failures remain.

---

## Findings

| | Finding | Status |
|---|---|---|
| **C1** | Supabase `service_role` key written into git-tracked, publicly-published files | ✅ Closed structurally — the generator, the config sinks and the publishing workflow are gone; `live_db.js` accepts only the anon key |
| **C2** | `Runtime(data_dir=…)` silently ignored its argument | ✅ Fixed — caused **18 of the 19** test failures |
| **C3** | Tests ran only inside `deploy-azure.yml`, path-gated to a job that never runs | ✅ Fixed — `tests.yml` runs on every push and PR with no path filter |
| **H1** | A test that failed every weekend | ✅ Fixed — patched `ORBIX_WEEKDAYS_ONLY` alongside `ORBIX_TRADING_SESSIONS` |
| **H2** | Two Pages workflows racing on one environment | ✅ One publisher remains |
| **H3** | `database/` missing from the Docker image and the wheel | ✅ Fixed — production ran a different storage path than dev |
| **H4** | Stale build artifacts tracked despite `.gitignore` | ✅ Deleted and genuinely ignored |
| **H5** | **Found this pass.** `database/__init__.py` re-exported 7 of 16 public names | ✅ Fixed + regression test |
| **M1–M9** | Drift guard, undeclared deps, CI error masks, dead triggers, `mkdir` side effect, import caps, published host paths, undocumented env vars, naive `utcnow()` | ✅ **All nine closed** |

### C2 — the bug that broke 18 tests

`DatabaseConfig` derived `duckdb_path`/`sqlite_path` from `data_dir` once, in
`__post_init__`; `runtime.py` reassigned `data_dir` *afterwards*, so the paths
stayed stale and every Runtime opened the repo-root database. Two Runtime
instances in one deployment corrupted each other. The config is now built *with*
the directory, `set_data_dir()` recomputes every derived path, and both store
caches key on resolved file paths so key and target cannot diverge.

Fixing it exposed a latent bug the shared database had been masking:
`CandleStore.close()` never evicted its cache entry, so the next Runtime got a
store holding a dead DuckDB connection. `close()` is now idempotent and sets a
`closed` flag; both caches rebuild a closed entry.

### H5 — how a missing re-export shipped

`database/__init__.py` omitted `check_database_health` and `get_config`. Both
call sites wrap the import in `except Exception`:

- `orbix/api.py` → `/api/storage` returned a `database_config_error` *string*
  instead of the health block it advertises.
- `orbix/mobile_api.py` → ran with `_root_db_config = None`, so the mobile API
  could serve a different data directory from the server.

HTTP 200 throughout. No crash, no failing test, no log line anyone would see.
This is the same silent-degradation class as H3, and it had shipped.

It was found by regenerating the Pages snapshot and grepping the captured JSON
for host paths — the swallowed exception message embedded an absolute path to
`database/__init__.py`. `tests/test_database_exports.py` (5 tests) now AST-scans
every `from database import X` in the repo and asserts the name is exported,
importable, and resolves to the same object through the `orbix.database` shim.

### M7 — the snapshot was un-regenerable

`scripts/build_static_demo.py` had two defects that compounded:

1. It enumerated four exact `/static/` strings while `web/index.html` had grown
   to seven, so its own guard raised `SystemExit`.
2. It called `shutil.rmtree(DOCS)` *before* building the replacement — so that
   failure **deleted the committed snapshot**.

Rewritten to match `/static/` by pattern, derive the asset list from the HTML via
`resolve_asset_closure()`, and compute everything before destroying anything.
Added `redact_paths()`, which scrubs host paths from *every* string value rather
than a fixed list of keys — keying off field names is insufficient, because an
exception message embeds whatever directory the server happened to run from.

### M1 — the guard now shares the generator's source of truth

The drift guard started as a shell loop over a hardcoded `app.js styles.css`
list. That is the same anti-pattern that broke M7, so it now lives in
`tests/test_static_demo_drift.py` (8 tests), which imports the builder's own
`resolve_asset_closure()`. It checks that every referenced asset exists in
`web/`, was copied to `docs/`, is byte-identical, had its `/static/` prefixes
rewritten, and publishes no host paths. Guard and generator cannot drift apart.

### M2 — silent backend downgrade removed

`psycopg2` was imported but declared in neither `requirements.txt` nor
`pyproject.toml`, so the Postgres backend could not work from a clean install.
Added `postgres`, `supabase`, `dev` and `all` extras — as *extras*, not core
dependencies, because both are lazily imported and forcing a native build on
every install would be worse than the bug.

An explicit `DATABASE_BACKEND=postgres|supabase|duckdb` with a missing driver now
raises `RuntimeError` naming the install command, instead of quietly serving
SQLite. Auto-detection still falls back, but logs a warning.

### Two more defects found and fixed this pass

- **The published PWA manifest 404'd.** `web/manifest.json` declared
  `screenshot-wide.png` and `screenshot-narrow.png`; neither was ever committed.
  Confirmed live: both returned 404. The dead `screenshots` block is removed —
  the icons it also declares do exist and still work. Swept every asset the app
  references: **8 of 8 resolve, 0 broken.**
- **Twelve failure paths logged to stdout.** Library and server code used
  `print()`, which bypasses log levels, cannot be filtered, and is invisible to
  anything consuming the process log. All twelve converted to module loggers
  across seven files. The `print()` calls in `orbix/study.py` and
  `orbix/security.py` are deliberate CLI output and were left alone.

---

## What's remaining

**Nothing in the code.** All three critical, all five high and all nine medium
findings are closed, plus two defects found while verifying the fixes.

What remains is not code, and cannot be closed by code:

| | |
|---|---|
| **Live market data** | No feed key is configured. `/api/health` reports `live_feed_connected: false`, and the provider raises `ProviderError("Set ORBIX_TWELVE_DATA_API_KEY …")` on any fetch. Today the app runs on synthetic demo candles or imported CSV. |
| **Forward-test evidence** | No sample of paper signals on live data exists yet. The repo says so itself: *"this does not prove future accuracy."* |
| **Broker reality checks** | Payout ratios, spreads and execution latency on the actual venue are unmeasured. |

---

## Next steps

1. **Merge this branch.** CI is green and the PR carries the full diff.
2. **Connect a live feed.** Set `ORBIX_TWELVE_DATA_API_KEY` in the deployment
   environment — never in the repo, never in a config file that ships. This is
   the real gate on trading.
3. **Paper-trade forward.** Leave the scheduler running on live data and
   accumulate signals across different market conditions. Expect weeks to
   months; no amount of code review substitutes for the sample.
4. **Read the walk-forward and calibration numbers honestly**, then size a
   position small enough that being wrong is survivable.

---

## When will it be ready for actual trading?

For **manual** trading the broker layer is already correct and needs no work.
`orbix/brokers.py` is deliberately handoff-only:

```python
"official_api": False, "order_execution": False,
"credentials_required": False, "manual_handoff": True, "orders_sent": False
```

Pocket Option has no official public API, so the app formats a copyable
CALL/PUT ticket and you enter the trade in the broker's own UI. That is the right
design and it is finished.

**Software-ready: done.** Green CI, working deploys, dev and prod on the same
code path, a ledger whose isolation is regression-tested, and no known defect
open at any severity.

**Money-ready: not yet — and this is not a code problem.** Nothing in the repo
demonstrates a profitable edge. Before risking real money you need the three
items in the table above: a live feed, forward-test evidence, and broker-side
reality checks. On 1-minute expiries the gap between modelled and actual
execution dominates the result.

The honest sequence: merge → connect a live feed → paper-trade forward long
enough to get a real sample → only then size a position.

One structural caution: the discipline layer already models sessions, daily
budgets, cool-offs and loss stops. Keep it enabled when you go live — it is the
part of the system designed to stop a bad streak from becoming a worse one. Note
that H1 was caused by that weekend gate, so if you ever disable
`ORBIX_WEEKDAYS_ONLY` in production, do it deliberately rather than inheriting it
from a test.

---

## Session commits

| | |
|---|---|
| `a0712ef` | Remove the retired mobile packaging surface (65 files, −12,992 / +874) |
| `f81ae54` | Replace the detailed audit with a consolidated status report |
| `aeecefb` | Clean the last stale packaging reference and narrow the H3 finding |
| `7a80a23` | Fix the data_dir isolation bug and the three issues it blocked |
| `94f5f56` | Refresh the report |
| *(this commit)* | Close every remaining finding; fix H5, the manifest 404s and the snapshot generator |
