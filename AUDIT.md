# Orbix — Status Report

**Date:** 2026-10-25 · **Branch:** `arena/915a9e38-orbix` · **Commit:** `a0712ef`
**Target:** manual trading — Orbix produces signals, you place the trades yourself.

---

## Present condition

**The app works. The test suite does not, and that is blocking deployment.**

| | |
|---|---|
| Tracked files | 128 (was 193) |
| Python LOC | 16,721 |
| Tests | 161 total — **19 failing** |
| App imports | ✅ FastAPI loads, 50 routes |
| Mobile PWA builds | ✅ Vite, 10 modules, no errors |
| CI workflows | 2 (`deploy-azure.yml`, `pages.yml`) |
| Azure deploys | ❌ **blocked** by the failing tests |
| Secrets in repo | ✅ none |
| Broker orders sent | ✅ never — by design |

The 19 failures come from **two bugs**, not nineteen. Fix those two and the suite is green.

---

## What we did this session

### 1. Full audit
Reviewed all 193 files. Found 3 critical, 4 high, 9 medium issues. Notably, **no injection, no XSS, no hardcoded credentials** — the auth layer (PBKDF2-260k, CSRF, rate limiting, CSP, output escaping) is genuinely well built and needs no changes.

### 2. Removed the entire APK and Termux surface
65 files deleted, 33 modified — **−12,992 / +874 lines**.

**Deleted:** `mobile/android-chaquopy/` (Java, Gradle, manifests) · `mobile/dist/` · `scripts/build_apk.sh` · `scripts/configure_root_apk.py` · `scripts/termux_*.sh` · `build-apk.yml` · `deploy-pwa-pages.yml` · `database/apk_config.json` · `database/root_manifest.json` · all `root_config.{json,js}` · `mobile/capacitor.config.json` · `TERMUX.md` · `DATABASE_ROOT_PERMANENT.md` · `DATABASE_LIVE.md` · `GITHUB_PAGES.md` · `docs/MOBILE.md` · `mobile/{README,BUILD_SUCCESS}.md`

**Fixed rather than deleted:**
- `mobile/src/js/db.js` → IndexedDB-only (the Capacitor SQLite branch imported packages that no longer exist, which would have broken the build)
- `mobile/package.json` → all `@capacitor/*` deps and `apk:*` scripts dropped; lockfile regenerated
- `orbix/api.py` → removed `/api/database/apk-config` and `/api/database/root-manifest`
- `database/{config,connection}.py` → removed the `ORBIX_APK` backend switch
- `scripts/build_mobile.sh` → reduced from a 250-line APK config generator to a real Vite build
- README / DATABASE.md / database/README.md / .env.example → reworded

**Verified after removal:** Python compiles · app imports with 50 routes · all JS passes `node --check` · Vite build succeeds · **tests still exactly 19 failures, so the removal caused zero regressions.**

### 3. Four audit findings closed by the removal

| Finding | Status |
|---|---|
| **C1 — Supabase `service_role` key written into git-tracked, publicly-published files** | ✅ **Eliminated.** The generator, the config files, and the publishing workflow are gone. `live_db.js` now accepts only the anon key and actively purges any legacy `SUPABASE_SERVICE_KEY` from localStorage. |
| **H2 — Two Pages workflows racing on one environment** | ✅ Resolved. `deploy-pwa-pages.yml` deleted; `pages.yml` is the single publisher. |
| **H4 — Stale build artifacts tracked despite `.gitignore`** | ✅ Resolved. `mobile/dist/` deleted and now genuinely ignored. |
| **M3 — 62 error-masking `\|\| true` operators in CI** | ✅ Resolved. Both offending workflows were deleted; remaining two have **zero** masks. |

C1 was the most serious issue in the repo, and it is now structurally impossible rather than merely unused.

---

## What's remaining

### Blocking (must fix before trusting anything)

**C2 — `Runtime(data_dir=...)` silently ignores its argument.**
`DatabaseConfig.__post_init__` derives `duckdb_path`/`sqlite_path` from `data_dir` once. `runtime.py` reassigns `data_dir` *afterwards*, so the paths stay stale and every Runtime opens the repo-root database. Proven:

```
AFTER cfg.data_dir = /tmp/tmpeumoe5qk:
  duckdb_path = /home/user/orbix/data/candles.duckdb   <-- STALE
```

Every test using a temp dir is really reading/writing one shared database. **This causes 18 of the 19 failures.** It also means two Runtime instances in one deployment corrupt each other.

**C3 — Red tests block Azure deployment.**
`deploy-azure.yml` runs the suite in its `build` job; `deploy` declares `needs: build`. Failing tests → no deploy. Azure has been dead as long as these tests have failed.

**H1 — One test fails every weekend.**
`test_dataset_scan_generates_a_paper_only_read_once` uses wall-clock time and patches `ORBIX_TRADING_SESSIONS` but not `ORBIX_WEEKDAYS_ONLY`. `discipline.py:243` blocks `weekday() >= 5`. Today is Saturday → `market_closed` → 0 signals. Confusingly, its *status* assertion still passes, so the failure looks unrelated to its cause.

**H3 — `database/` is missing from the Docker image.**
The Dockerfile copies only `orbix`, `web`, and metadata. `runtime.py`'s `from database import ...` fails, gets swallowed by a bare `except Exception`, and prints to stdout. **Production runs a different storage code path than dev.** `pyproject.toml` has the same gap (`include = ["orbix*"]`), so `pip install .` degrades identically.

### Non-blocking

| | |
|---|---|
| **M1** | `docs/app.js` is a byte-identical 2,410-line copy of `web/app.js` — will drift |
| **M2** | `supabase` missing from `pyproject.toml`; `psycopg2` imported but declared nowhere; no dev/test extra |
| **M5** | `DatabaseConfig.__post_init__` creates directories on construction — importing config writes to disk |
| **M6** | `MAX_IMPORT_BYTES = 1 GiB` on `/api/import`, no quota or free-disk check |
| **M7** | `docs/api-data/storage.json` publishes a server path (`/tmp/orbix-preview/...`) to GitHub Pages |
| **M8** | 9 env vars read in code but absent from `.env.example` |
| **M9** | 2 naive `datetime.utcnow()` calls left in `orbix/api.py` (deprecated in 3.12; rest of codebase is tz-aware) |
| — | `pages.yml` still triggers on two dead `arena/*` branches |

---

## Next steps

In this order. Items 1–3 are small and unblock everything else.

1. **Fix C2** — recompute derived paths when `data_dir` changes (or construct the config with the directory up front). Key the store caches on resolved *file paths*, not `data_dir`. Add a regression test asserting two Runtimes with different dirs see disjoint ledgers. → *clears 18 failures*
2. **Fix H1** — add `"ORBIX_WEEKDAYS_ONLY": "false"` to the existing `mock.patch.dict`. → *one line, clears the last failure*
3. **Add a standalone test workflow** on every push/PR with **no `paths:` filter**. Today tests only run inside the Azure deploy job, gated to `orbix/**`, `web/**`, `tests/**` — so changes to `database/**` or `scripts/**` are never tested. That is exactly how C2 went unnoticed. → *unblocks Azure deploys*
4. **Fix H3** — `COPY database ./database` in the Dockerfile, add `"database*"` to `pyproject.toml`, and make the import fallback fail loudly instead of printing to stdout.
5. **Then** M2 (dependencies), M1 (duplicate `app.js`), M7 (redact paths from the public snapshot), and the rest.

---

## When will it be ready for actual trading?

For **manual** trading, the broker layer is already correct and needs no work. `orbix/brokers.py` is deliberately handoff-only:

```python
"official_api": False, "order_execution": False,
"credentials_required": False, "manual_handoff": True, "orders_sent": False
```

Pocket Option has no official public API, so the app formats a copyable CALL/PUT ticket and you enter the trade in the broker's own UI. That is the right design and it is finished.

Readiness is therefore two separate questions:

**Software-ready — roughly 1–2 days of work.**
Steps 1–4 above. After that: green CI, working deploys, dev and prod on the same code path, and a ledger you can trust. This is small, well-understood work — the bugs are already diagnosed to the line.

**Money-ready — weeks to months, and not a code problem.**
Nothing in the repo yet demonstrates a profitable edge, and the code says so itself: *"Paper outcome saved and scored before the online model update; this does not prove future accuracy."* Before risking real money you need:

- A **live data feed** (Twelve Data key). Today the app runs on synthetic demo candles or imported CSV — neither is tradeable.
- **Forward-test evidence**: a meaningful sample of paper signals on live data, across different market conditions, with the walk-forward and calibration numbers reviewed honestly.
- **Broker-side reality checks**: payout ratios, spreads, and execution slippage on the actual venue differ from the model's assumptions, and on 1-minute expiries that gap dominates the result.

The software can be trustworthy in days. The *edge* has to be earned with data, and no amount of code review substitutes for that. The honest sequence is: fix 1–4 → connect a live feed → paper-trade forward for long enough to get a real sample → only then size a position small enough that being wrong is survivable.

One structural caution worth stating plainly: the discipline layer already models sessions, daily budgets, cool-offs and loss stops. Keep it enabled when you go live — it is the part of the system designed to stop a bad streak from becoming a worse one.

---

## Note on the audit trail

The detailed 16-finding audit from earlier in this session was replaced by this report at your request. Findings closed by the APK/Termux removal are summarised above; the four still-open blocking items (C2, C3, H1, H3) are described in enough detail to action directly. No repository files were modified during the audit itself — the only changes committed are the removal in `a0712ef` and this report.
