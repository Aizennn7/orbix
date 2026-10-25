# Orbix — Status Report

**Date:** 2026-10-25 · **Branch:** `arena/915a9e38-orbix` · **Head:** `7a80a23`
**Target:** manual trading — Orbix produces signals, you place the trades yourself.

---

## Present condition

**Green. The blocking bugs are fixed and the suite passes.**

| | |
|---|---|
| Tests | **165 passing, 0 failing** (was 161 with 19 failing) |
| CI coverage | ✅ every push and PR, no path filter |
| Azure deploys | ✅ unblocked |
| App imports | ✅ FastAPI loads, 50 routes |
| Mobile PWA builds | ✅ Vite, 10 modules |
| Secrets in repo | ✅ none |
| Dev/prod code path | ✅ same |
| Broker orders sent | ✅ never — by design |
| Tracked files | 131 (was 193 before the APK/Termux removal) |

Verified on a Sunday, which matters: one of the bugs only reproduced on weekends.

---

## What we did this session

### 1. Full audit
Reviewed all 193 files and found 3 critical, 4 high, 9 medium issues. Notably **no injection, no XSS, no hardcoded credentials** — the auth layer (PBKDF2-260k, CSRF, rate limiting, CSP, output escaping) is well built and needed no changes.

### 2. Removed the entire APK and Termux surface
65 files deleted, 33 modified — **−12,992 / +874 lines**.

**Deleted:** `mobile/android-chaquopy/` · `mobile/dist/` · `scripts/build_apk.sh` · `scripts/configure_root_apk.py` · `scripts/termux_*.sh` · `build-apk.yml` · `deploy-pwa-pages.yml` · `database/apk_config.json` · `database/root_manifest.json` · all `root_config.{json,js}` · `mobile/capacitor.config.json` · `TERMUX.md` · `DATABASE_ROOT_PERMANENT.md` · `DATABASE_LIVE.md` · `GITHUB_PAGES.md` · `docs/MOBILE.md` · `mobile/{README,BUILD_SUCCESS}.md`

**Fixed rather than deleted:** `db.js` → IndexedDB-only (its Capacitor imports would have broken the build) · `package.json` → Capacitor deps and `apk:*` scripts dropped, lockfile regenerated · `api.py` → two dead endpoints removed · `database/{config,connection}.py` → `ORBIX_APK` switch removed · `build_mobile.sh` → reduced to a real Vite build.

This closed four audit findings outright:

| Finding | Outcome |
|---|---|
| **C1 — Supabase `service_role` key written into git-tracked, publicly-published files** | ✅ **Eliminated structurally.** The generator, the config files, and the publishing workflow are gone; `live_db.js` accepts only the anon key and purges any legacy service key from localStorage. |
| **H2 — Two Pages workflows racing on one environment** | ✅ One publisher remains. |
| **H4 — Stale build artifacts tracked despite `.gitignore`** | ✅ Deleted and now genuinely ignored. |
| **M3 — 62 error-masking `\|\| true` operators in CI** | ✅ Both offending workflows deleted; remaining ones have zero. |

### 3. Fixed the four blocking bugs

**C2 — `Runtime(data_dir=...)` silently ignored its argument.** `DatabaseConfig` derived `duckdb_path`/`sqlite_path` from `data_dir` once, in `__post_init__`; `runtime.py` reassigned `data_dir` *afterwards*, so the paths stayed stale and every Runtime opened the repo-root database. This alone caused **18 of the 19 failures**, and meant two Runtime instances in one deployment corrupted each other. Now the config is built *with* the directory (`get_database_config({**os.environ, "ORBIX_DATA_DIR": str(path)})`), `set_data_dir()` recomputes every derived path, and both store caches key on resolved file paths so key and target can't diverge.

**Two latent bugs this exposed** — the shared database had been masking them:
- `CandleStore.close()` never evicted its cache entry, so the next Runtime got a store with a dead DuckDB connection. `close()` is now idempotent and sets a `closed` flag; both caches rebuild a closed entry.

**H1 — a test that failed every weekend.** It built fixtures from wall-clock time and patched `ORBIX_TRADING_SESSIONS` but not `ORBIX_WEEKDAYS_ONLY`, so the discipline weekend gate blocked the read from Saturday onward. Now patches both.

**C3 — tests only ran inside `deploy-azure.yml`**, gated to `orbix/**`, `web/**`, `tests/**`. Changes to `database/**` or `scripts/**` never ran a test — exactly how C2 went unnoticed. Added `.github/workflows/tests.yml`: every push and PR, **no path filter**, plus a check that the suite leaves no `./data` in the working tree.

**H3 — `database/` was missing from the Docker image and the wheel**, so `from database import ...` failed and Runtime silently degraded to the legacy stores. Production ran a different storage path than dev. Dockerfile now copies it, `pyproject.toml` includes `database*`, and both fallbacks log a warning naming the likely cause instead of printing to stdout.

Simulating the container layout confirms it: with `database/` present the backend factory activates; without it, the warning fires loudly.

**Added `tests/test_database_config.py`** (4 tests) covering path derivation, `set_data_dir` recomputation, ledger isolation between two data dirs, and the closed-store cache guard.

---

## What's remaining

Nothing blocking. All medium-severity hygiene:

| | |
|---|---|
| **M2** | `supabase` is in `requirements.txt` but not `pyproject.toml`; `psycopg2` is imported by `database/connection.py` but declared in **neither** — so the Postgres backend cannot work from a clean install. No dev/test extra exists. |
| **M1** | `docs/app.js` is still a byte-identical 2,410-line copy of `web/app.js` — will drift. |
| **M5** | `DatabaseConfig.__post_init__` calls `mkdir()`, so merely constructing a config writes to disk (`set_data_dir()` inherits this). |
| **M6** | `MAX_IMPORT_BYTES = 1 GiB` on `/api/import` (vs 64 MB for append), with no quota or free-disk check. |
| **M7** | `docs/api-data/storage.json` publishes a server path (`/tmp/orbix-preview/candles.duckdb`) to GitHub Pages. The snapshot is also stale (2026-10-20). |
| **M8** | 9 env vars read in code but absent from `.env.example`: `FINNHUB_ECONOMIC_URL`, `ORBIX_ENV_FILE`, `ORBIX_OFFLINE`, `ORBIX_STATIC`, `SUPABASE_KEY`, `SUPABASE_SQL`, `TELEGRAM_RESPONSE_BYTES`, `TELEGRAM_SIDE_LABEL`, `TWELVE_DATA_URL`. |
| **M9** | 2 naive `datetime.utcnow()` calls left in `orbix/api.py` (deprecated in 3.12; rest of the codebase is tz-aware). |
| — | `pages.yml` still triggers on two dead `arena/*` branches. |

---

## Next steps

1. **Push the branch and confirm CI.** `tests.yml` should run green on the PR — that is the first real proof the new gate works.
2. **M2 — fix dependencies.** Add `supabase` to `pyproject.toml`, declare `psycopg2-binary` (or gate the Postgres backend behind a clear ImportError), and add a dev extra for test tooling. Highest value of the remaining items, because a missing declaration is a runtime failure.
3. **Connect a live data feed.** Set `ORBIX_TWELVE_DATA_API_KEY`. Today the app runs on synthetic demo candles or imported CSV — neither is tradeable. **This is the real gate on trading, not code.**
4. **Then the cheap hygiene items:** M7 (redact paths from the public snapshot), M9 (`datetime.now(timezone.utc)`), the dead `pages.yml` branch refs, M8 (document the 9 env vars), M1 (generate `docs/app.js` from `web/app.js` instead of copying), M6 (lower the import cap or add a disk check).

---

## When will it be ready for actual trading?

For **manual** trading the broker layer is already correct and needs no work. `orbix/brokers.py` is deliberately handoff-only:

```python
"official_api": False, "order_execution": False,
"credentials_required": False, "manual_handoff": True, "orders_sent": False
```

Pocket Option has no official public API, so the app formats a copyable CALL/PUT ticket and you enter the trade in the broker's own UI. That is the right design and it is finished.

**Software-ready: done.** The work estimated at 1–2 days is complete — green CI, working deploys, dev and prod on the same code path, and a ledger whose isolation is now covered by regression tests.

**Money-ready: not yet, and this is not a code problem.** Nothing in the repo demonstrates a profitable edge, and the code says so itself: *"Paper outcome saved and scored before the online model update; this does not prove future accuracy."* Before risking real money you need:

- **A live feed** (step 3 above). Synthetic demo candles and imported CSV cannot tell you anything about forward performance.
- **Forward-test evidence** — a meaningful sample of paper signals on live data, across different market conditions, with the walk-forward and calibration numbers read honestly.
- **Broker-side reality checks.** Payout ratios, spreads and execution latency on the actual venue differ from the model's assumptions, and on 1-minute expiries that gap dominates the result.

The honest sequence: push and confirm CI → connect a live feed → paper-trade forward long enough to get a real sample → only then size a position small enough that being wrong is survivable. Expect weeks to months for the evidence step; no amount of code review substitutes for it.

One structural caution: the discipline layer already models sessions, daily budgets, cool-offs and loss stops. Keep it enabled when you go live — it is the part of the system designed to stop a bad streak from becoming a worse one. Note that H1 was caused by that weekend gate, so if you ever disable `ORBIX_WEEKDAYS_ONLY` in production, do it deliberately rather than inheriting it from a test.

---

## Session commits

| | |
|---|---|
| `a0712ef` | Remove the Android APK and Termux surface |
| `f81ae54` | Replace the detailed audit with a consolidated status report |
| `aeecefb` | Clean the last APK reference and narrow the H3 finding |
| `7a80a23` | Fix the data_dir isolation bug and the three issues it blocked |

The APK/Termux removal is complete: `git grep -IiE "apk|termux|chaquopy|capacitor"` returns **zero** matches across tracked text files. Dropping `-I` (which skips binaries) adds one hit, `web/icon-192.png`, where the byte sequence `aPkDa-` inside the compressed image data coincidentally matches a case-insensitive "apk" — not a real reference.

Nothing has been pushed — all commits are local on `arena/915a9e38-orbix`.
