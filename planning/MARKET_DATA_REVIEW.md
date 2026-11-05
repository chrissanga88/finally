# Market Data Backend — Code Review

> **Update (2026-11-04, same day): findings resolved.** All code findings (M1–M3, L1–L3, L5), test gaps T1–T4 and H3 are fixed; see [§8 Resolution](#8-resolution). Now **161 tests, 100% coverage**. The review text below is kept as written for the record.

**Date:** 2026-11-04
**Scope:** `backend/app/market/` (8 modules, 504 statements), `backend/tests/market/` (7 files, 138 tests), `backend/pyproject.toml`, checked against `PLAN.md` §6/§8/§9/§13, `MARKET_DATA_DESIGN.md`, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md` and `REVIEW.md`.

> **Note on independence:** the same agent session wrote the design, the code and this review. To make up for that, every suspected defect below was **reproduced with a probe script** rather than argued from reading alone (see §7). Even so, a second reviewer is worth having before the Massive path is used with a live key.

---

## 1. Verdict

**Approved with follow-ups.** The subsystem is well structured, matches the PLAN contract and the design, and is thoroughly tested. There are no high-severity defects.

- **Three medium issues.** All are in the Massive path or its configuration. None affects the default simulator path that the course demo and E2E tests use.
- **Fix before relying on a real Massive key:** M1 (stale EOD prices after an early-morning fetch) and M3 (a bad poll interval silently stops polling).

| Severity | Count | Items |
|---|---|---|
| High | 0 | — |
| Medium | 3 | M1 EOD stale after midnight; M2 startup blocked by first Massive poll; M3 NaN/inf poll interval stops polling |
| Low | 5 | L1–L5 |
| Test gaps | 5 | T1–T5 |
| Hygiene / integration | 5 | H1–H5 |

---

## 2. Test results

| Check | Result |
|---|---|
| `pytest` | **138 passed** in ~2 s |
| Repeat runs (flakiness) | 5/5 runs green |
| Coverage | **99%** (504 statements, 2 missed: `massive_client.py:42` `_et_today` body, `:279` re-raise of a non-403 error during the EOD walk) |
| `ruff check` | clean |
| `ruff format --check` | clean (19 files) |
| Live SSE smoke test (uvicorn + httpx, simulator) | `200 text/event-stream`, `retry: 1000`, then full maps about every 0.5 s; a ticker added at runtime was priced immediately |
| Live Massive API | **Not run.** No `MASSIVE_API_KEY` in `.env` |

Per-file coverage: `cache`, `factory`, `interface`, `models`, `seed_prices`, `simulator`, `stream`, `__init__` are all 100%. `massive_client` is 99%.

---

## 3. Conformance to the specification

| Requirement | Source | Status |
|---|---|---|
| One interface, two sources, chosen by `MASSIVE_API_KEY` (empty/whitespace → simulator) | PLAN §6, §5 | ✅ `factory.py` |
| GBM, ~500 ms ticks, correlated moves, 2–5% events, realistic seeds, unknown tickers $50–$300 | PLAN §6 | ✅ `simulator.py`, `seed_prices.py` |
| Massive REST polling (not WebSocket), union of tracked tickers in one call, 15 s default | PLAN §6 | ✅ `massive_client.py` |
| Cache holds latest, previous, timestamp, session open | PLAN §6 | ✅ `cache.py` |
| `session_open` / `session_change_percent` in `to_dict()` (TODO) | PLAN §6 | ✅ done |
| `PriceCache.remove()` bumps `version` (TODO) | PLAN §6 | ✅ done (only when something was removed) |
| Massive may use previous close as `session_open` | PLAN §6 | ✅ `prev_day.close` |
| SSE: `retry: 1000`, check about every 500 ms, one unnamed `data:` event with a map of **all** tickers, on change | PLAN §6 | ✅ `stream.py` |
| Per-ticker keys and units exactly as specified, `timestamp` in Unix seconds | PLAN §6 | ✅ asserted by `test_models.py::TestToDict` |
| `LastTrade.sip_timestamp` in ns (not `.timestamp`) | MARKET_INTERFACE §5 | ✅ with real-SDK-model tests |
| Free-plan 403 → EOD mode | MARKET_INTERFACE §5 | ✅ (see M1) |
| 429 backoff, error-log dedup | MARKET_INTERFACE §5 | ✅ (see L1) |
| Router created inside the factory | MARKET_INTERFACE §7 | ✅ |
| Injectable RNG | MARKET_SIMULATOR §5 | ✅ |
| `rich` out of core deps | REVIEW N9 | ✅ (demo not rebuilt) |
| `MASSIVE_POLL_INTERVAL` documented in PLAN §5 / `.env.example` | REVIEW N8 | ❌ see H2 |
| Track `watchlist ∪ positions` (startup, add, remove, closing sell) | PLAN §6 | ⏳ app-level, not built yet (H1) |

---

## 4. Strengths

- **Clean separation.** Sources only write and consumers only read the cache. Neither source knows anything about the watchlist or portfolio. `GBMSimulator` is pure synchronous math, and the async adapter is thin.
- **Correct concurrency model.**
  - The cache lock is held only around dict operations.
  - The Massive SDK runs in `asyncio.to_thread` with a copied ticker list.
  - Writes from an in-flight poll are filtered against the *current* tracked set, so a removed ticker can't be resurrected.
  - The simulator has no `await` inside `step()`, so it needs no lock.
- **Robust loops.** Both background loops catch `Exception` (not `BaseException`), so `stop()` cancellation works. Tests check that `stop()` is idempotent and that no writes happen after it.
- **Correct SDK usage.** Snapshot timestamps handle ns vs ms correctly, and tests use real `TickerSnapshot` / `GroupedDailyAgg` objects with a fake client that exposes only the expected methods. That catches the attribute-name bug class the previous implementation shipped with.
- **Sound numerics.** Internal simulator prices keep full precision and only outputs are rounded. The correlation matrix is positive definite for any ticker mix (proof in the design doc; a test covers 50 mixed tickers). Statistical tests check correlation (±0.05) and σ (±5%) with a seeded RNG, so they're deterministic.
- **Good SSE details.** It sends `{}` when the last ticker is removed (the previous implementation went silent), sends keepalive comments when idle, sets `X-Accel-Buffering: no`, and doesn't swallow `CancelledError`.

---

## 5. Findings

### Medium

#### M1. EOD mode can serve stale closes all day after an early-morning fetch
`massive_client.py` · `_poll_eod` / `_fetch_eod_closes`

Free-plan bars for day D appear "after midnight ET" on D+1 (`MASSIVE_API.md` §2). If the first fetch of a new ET date happens before D's bars are published (process started, or date rolled over, at 00:00–00:xx ET), the walk-back skips the empty D and loads D−1. `_eod_fetched_for` is still set to today, so the source **never refetches until the next date change**. A free-plan user then sees closes that are a day older than necessary for the whole day.

**Reproduced (§7, P3):** on Saturday 00:00 ET, Friday's bars are missing, so Thursday's close (189.0) is loaded. Friday's bars are then published, but three more polls make no API calls and the price stays 189.0.

**Fix:** record which bar date was loaded, and keep retrying hourly while it is older than the expected latest trading day.

```python
def _expected_eod_date(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

# _fetch_eod_closes returns (bar_date, closes); in _poll_eod:
stale = self._eod_bar_date is None or self._eod_bar_date < _expected_eod_date(today)
if (self._eod_fetched_for != today or stale) and time.monotonic() >= self._eod_retry_at:
    ...
    if closes:
        self._eod_bar_date = bar_date
        if bar_date < _expected_eod_date(today):        # holiday or not yet published
            self._eod_retry_at = time.monotonic() + 3600  # ≤ 1 extra call/hour
```

On a market holiday this costs one extra call per hour, which is well within the free 5/min budget. Add a regression test based on P3.

#### M2. A slow or unreachable Massive API delays application startup
`massive_client.py` · `start()`

`start()` awaits the first poll before returning, and the lifespan awaits `start()`, so uvicorn doesn't serve **any** request, including `/api/health` and the static frontend, until that poll finishes. With `connect_timeout=5`, `read_timeout=10` and `retries=2`, an unreachable or slow API can block startup for tens of seconds. A 403 → EOD walk-back adds up to 5 more calls. The Docker health check and E2E `depends_on` will time out or look flaky.

The design deliberately prefers populating the cache before serving. Keep that, but bound it:

```python
async def start(self, tickers: list[str]) -> None:
    ...
    try:
        await asyncio.wait_for(self._poll_once(), timeout=5.0)
    except TimeoutError:
        logger.warning("Massive first poll slow; continuing startup, prices will follow")
    self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
```

`wait_for` cancels the awaiting coroutine. The worker thread finishes in the background and its result is dropped, which is harmless because the loop polls again.

#### M3. `MASSIVE_POLL_INTERVAL=nan` or `inf` silently stops polling
`factory.py` · `_poll_interval_from_env`

`float("nan")` and `float("inf")` parse fine. `max(nan, 1.0)` returns `nan`, and `asyncio.sleep(nan)` / `sleep(inf)` **never return** (verified, P2). The source polls once in `start()` and then never again, with no warning. Prices freeze but look healthy.

**Fix:**

```python
import math
...
    if not math.isfinite(value):
        logger.warning("Invalid MASSIVE_POLL_INTERVAL=%r; using %.0fs", raw, DEFAULT_POLL_INTERVAL)
        return DEFAULT_POLL_INTERVAL
    return max(value, MIN_POLL_INTERVAL)
```

Consider also capping it (e.g. ≤ 300 s), since a huge typo like `15000` has the same symptom.

### Low

#### L1. Rate-limit detection can match unrelated errors
`massive_client.py` · `_is_rate_limited`

The check is a substring search for `"429"` anywhere in the exception text. Massive error bodies include a random hex `request_id`, so a 500 whose id contains `429` (e.g. `"8f2a429c"`) is treated as a rate limit (verified, P1). The effect is only an unnecessary interval doubling, which resets after the next success, so the severity is low.

**Fix:** match on the status or message rather than any digits:

```python
_RATE_LIMIT_MARKERS = ("too many requests", "too many 429", "exceeded the maximum requests", '"status":"too_many')
def _is_rate_limited(exc): return any(m in str(exc).lower() for m in _RATE_LIMIT_MARKERS)
```

Or parse `BadResponse`'s JSON body and check `status`. The exact 429 body is still unverified against the live API (as already flagged in the design).

#### L2. Simulator volatility is tied to a fixed 0.5 s `dt`, not to `update_interval`
`simulator.py` · `SimulatorDataSource.start`

`SimulatorDataSource(update_interval=X)` doesn't pass `dt` to `GBMSimulator`, so `dt` stays `0.5 / TRADING_SECONDS_PER_YEAR`. With any interval other than 0.5 s, the per-wall-second volatility is wrong (at `update_interval=0.1` prices move √5 ≈ 2.2× too fast). Production uses 0.5 s, so there's no current impact.

**Fix:** `GBMSimulator(..., dt=self._interval / TRADING_SECONDS_PER_YEAR, ...)`.

#### L3. The cache accepts non-finite and non-positive prices
`cache.py` · `update`

`update("X", nan)`, `update("Y", -1)` and `update("Z", 0)` are all stored (verified, P4). The simulator can't produce these and `_extract_price` skips zeros, but a malformed upstream value (negative, NaN) would flow through to trade fills and the frontend. NaN would also make `json.dumps` emit `NaN`, which isn't valid JSON, and the browser's `JSON.parse` would throw on every SSE event.

**Fix:** reject in the cache, which is the single choke point:

```python
if not math.isfinite(price) or price <= 0:
    raise ValueError(f"invalid price for {ticker}: {price!r}")
```

Both sources already catch per-item errors and log them. Massive's snapshot loop catches `ValueError` per ticker, and EOD's write loop should do the same.

#### L4. Free-plan startup can use more than its 5 calls/min budget
`massive_client.py`

A free key's first poll makes 1 snapshot call (403), then up to 5 grouped-daily calls on the walk-back (5 only if several weekdays are missing, i.e. holidays or unpublished data). The SDK's urllib3 retries also re-send 429s up to 2 more times. In the worst case this hits 429, which is handled (backoff), so impact is low. With M1's hourly retry the steady state stays at ≤ 1 call/hour.

**Optional:** in EOD mode, pass `retries=0` to the client, or cap the walk-back at 4 calls per poll.

#### L5. `SimulatorDataSource.add_ticker` before `start()` is silently ignored
`simulator.py`

If app code ever calls `add_ticker` before the lifespan has started the source, the ticker is dropped with no log, and later buys fail `NO_PRICE` for no visible reason. That can't happen in the planned lifespan.

**Fix:** a `logger.warning`, or `raise RuntimeError("not started")`, consistent with the double-start guard.

### Test gaps

- **T1.** `test_simulator_source.py::test_seeded_rng_reproducible` only compares one seed price. It should compare several cache values after running the loop, or call `src._sim.step()` repeatedly on both instances.
- **T2.** Four tests depend on timing (`update_interval=0.01` plus `asyncio.sleep(0.05–0.1)`). They passed 5/5 locally but can flake on a loaded CI runner. Prefer polling with a deadline (`for _ in range(100): if cond: break; await asyncio.sleep(0.01)`) over fixed sleeps.
- **T3.** There are no regression tests for M1, M3 and L1–L3. Add them along with the fixes; the probe scripts in §7 translate directly.
- **T4.** Two uncovered lines: the real `_et_today()` (always patched) and the re-raise of a non-403 `BadResponse` inside `_fetch_eod_closes`. Add one test where `get_grouped_daily_aggs` raises a 500-style `BadResponse` and assert the error is logged and no closes are loaded.
- **T5.** Tests reach into private state (`_interval`, `_base_interval`, `_eod_retry_at`, `_sim`, `_params`, `_cache`, `_api_key`). That's acceptable for unit tests, but it couples them to internals. Consider read-only properties for the values tests need (`poll_interval`, `api_key_set`).

### Hygiene and integration

- **H1. Tracking policy and app wiring are not built yet.** PLAN §6 "Which Tickers Are Tracked" (startup union, watchlist add/remove, closing sell) lives in `sync_tracking` and the lifespan (design §10), which need `app.db`. Until those exist, nothing calls `start()` in the app. Track this as the next backend task, and include `tests/test_tracking.py` (design §12.8).
- **H2. `MASSIVE_POLL_INTERVAL` is undocumented outside the backend.** PLAN §5 doesn't list it and the repo has no `.env.example` yet (PLAN §14 checklist). Add it to both when `.env.example` is created.
- **H3. Unused dev dependency.** `httpx` is in `[project.optional-dependencies].dev`, but no test imports it. Either remove it, or keep it and use it for an HTTP-level SSE test via `httpx.AsyncClient(transport=ASGITransport(app))`. That would also cover the response headers end to end.
- **H4. Don't let compression buffer SSE.** When the app is wired, don't apply `GZipMiddleware` to `/api/stream/*`, because compression buffers event streams. Add a note to `app/main.py` when it is created.
- **H5. Stray empty directory.** `planning/.claude/` is empty and untracked. Delete it.

---

## 6. Recommended actions (in order)

1. **M3** (two lines plus a test) and **L3** (cache validation plus a test): cheap and they prevent silent failure modes.
2. **M1** with a regression test: needed before anyone uses a free Massive key.
3. **M2**: bound the first poll so startup and health checks are reliable with a Massive key.
4. **L1**, **L2**, **L5**: small, do them alongside.
5. **T1–T4** while touching the same files. Re-run `pytest --cov` and keep coverage at 99%+.
6. **H1–H4** as part of the app-wiring task. Then do a live check with a real Massive key (paid and free) to confirm the 403/429 bodies and settle the inferred parts of L1.

---

## 7. Probe evidence

Run from `backend/` with the project virtualenv (`.venv/bin/python probe.py`):

```text
P1 500-with-429-in-request_id treated as rate limit: True
P2 MASSIVE_POLL_INTERVAL='nan' -> nan
P2 MASSIVE_POLL_INTERVAL='inf' -> inf
P2 MASSIVE_POLL_INTERVAL='-5' -> 1.0
P3 price after Fri bars published: 189.0 calls: ['2026-10-30', '2026-10-29']
P4 cache accepts: nan -1 0
asyncio.sleep(nan): never returns (wait_for timed out)   asyncio.sleep(inf): never returns
```

- **P1:** `_is_rate_limited(BadResponse('{"status":"ERROR","request_id":"8f2a429c","message":"Internal error"}'))`.
- **P2:** `create_market_data_source()` with `MASSIVE_API_KEY=k` and each interval value.
- **P3:** free-plan fake client. `_et_today` is Saturday 2026-10-31. Friday's bars are absent at start and published before three further `_poll_once()` calls.
- **P4:** `PriceCache().update(...)` with `nan`, `-1`, `0`.

---

## 8. Resolution

Applied the same day. After the fixes: **161 tests passed** (was 138), **100% coverage** (540 statements, 0 missed), `ruff check` and `ruff format --check` clean, 5/5 repeat runs green, and the live uvicorn SSE smoke test still passes. The §7 probes now report P1 `False`, P2 `15.0` for `nan`/`inf`, and P4 `ValueError`. P3 is time-gated by design and is covered by a regression test that advances the retry clock.

| Item | Status | Change | Regression test(s) |
|---|---|---|---|
| **M1** EOD stale after midnight | ✅ Fixed | `_fetch_eod_closes` returns `(bar_date, closes)`. `_poll_eod` fetches while `_eod_bar_date < _expected_eod_date(today)` (newest weekday before today), retrying every `EOD_STALE_RETRY_SECONDS` (30 min) and only loading strictly newer bars. Steady state stays at ≤ 1 call/day | `test_eod_refetches_when_bars_publish_after_midnight`, `test_eod_stale_retry_does_not_rewrite_unchanged_closes`, `test_expected_eod_date_skips_weekends` |
| **M2** First poll blocks startup | ✅ Fixed | `start()` wraps the first poll in `asyncio.wait_for(..., first_poll_timeout)` (default `FIRST_POLL_TIMEOUT = 5 s`). On timeout it logs a warning, and the poll loop still starts | `test_slow_first_poll_does_not_block_startup` |
| **M3** NaN/inf interval stops polling | ✅ Fixed | Non-finite values fall back to 15 s. Values above `MAX_POLL_INTERVAL = 300 s` are capped | `test_bad_blank_or_non_finite_interval_falls_back`, `test_interval_capped` |
| **L1** `"429"` substring false positive | ✅ Fixed | `_RATE_LIMIT_MARKERS` phrases only (`too many requests`, `too many 429`, `exceeded the maximum requests`, …). Exact live 429 body still unverified | `test_request_id_containing_429_is_not_rate_limit`, `test_error_classifiers` |
| **L2** `dt` not tied to `update_interval` | ✅ Fixed | `SimulatorDataSource` passes `dt=update_interval / TRADING_SECONDS_PER_YEAR` | `test_dt_matches_update_interval` |
| **L3** Cache accepts NaN/≤0 | ✅ Fixed | `PriceCache.update` raises `ValueError` (no state change). Non-finite `session_open` is ignored. Massive snapshot and EOD writes skip just that ticker | `TestValidation` (cache), `test_invalid_upstream_prices_skipped` |
| **L4** Free-plan startup call budget | ⏸ Not changed | Worst case is still handled by 429 backoff. Optional | — |
| **L5** `add_ticker` before `start` silent | ✅ Fixed | Logs a warning | `test_add_before_start_is_ignored_with_warning` |
| **T1** Weak RNG reproducibility test | ✅ Fixed | Compares the seeded cache plus 50 steps, and checks that different seeds differ | `test_seeded_rng_reproducible` |
| **T2** Fixed-sleep timing tests | ✅ Fixed | `wait_until(cond, timeout)` polling helper. The only remaining fixed sleep asserts the *absence* of writes after `stop()` | `test_simulator_source.py`, `test_poll_loop_runs_on_interval` |
| **T3** Missing regressions | ✅ Fixed | See the column above | — |
| **T4** Uncovered lines | ✅ Fixed | Covered `_et_today` and the non-403 re-raise in the EOD walk | `test_et_today_is_new_york_date`, `test_eod_non_403_error_during_walk_is_logged_and_retried` |
| **T5** Tests use private attributes | ⏸ Accepted | Kept for unit tests | — |
| **H1** App wiring / `sync_tracking` | ⏳ Open | Next backend task (needs `app.db`) | — |
| **H2** `MASSIVE_POLL_INTERVAL` docs | ⏳ Open | Add to PLAN §5 and `.env.example` when that file is created | — |
| **H3** Unused `httpx` dev dep | ✅ Fixed | Removed and re-locked. (`anthropic` 2.5 depends on `httpx2`, so nothing else needed `httpx`) | — |
| **H4** No GZip on SSE | ⏳ Open | Note for `app/main.py` | — |
| **H5** "Stray" `planning/.claude/` | ❎ Retracted | It contains `.cc-writes/`, which Claude Code itself uses. Not a project file; leave it alone | — |
