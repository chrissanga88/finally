# Review of planning/PLAN.md

Reviewer pass over the current (uncommitted) PLAN.md, cross-checked against the existing market data code in `backend/app/market/`, `backend/pyproject.toml`, the `claude-haiku` skill and `README.md`. PLAN.md was not modified.

Overall the plan is in good shape: the contracts (API shapes, error shape, trade rules, tracked-ticker set, mock mode) are much tighter than typical specs. The findings below are mostly the places where an implementing agent would ship something broken or have to guess.

---

## Critical

### C1. `backend/db/` will collide with the `/app/db` volume mount in Docker (§4, §12)
§4 puts schema/seed code in `backend/db/`. §12 copies `backend/` into the image (implicitly to `/app`) and mounts the named volume at `/app/db`. So the Python schema code lands at `/app/db/` and is then shadowed by the volume. On first run Docker copies the image contents into the empty named volume, so it appears to work; after that, every rebuild's schema/seed changes are invisible because the volume keeps the old copy. It also confuses the "db" name between code and data.
Also, `pyproject.toml` declares `packages = ["app"]`, so a top-level `backend/db/` package isn't part of the project package.
**Fix:** Move schema/seed code to `backend/app/db/` (imported as `app.db`), and say in §4 that `backend/` is copied to `/app` with `WORKDIR /app`, leaving `/app/db/` for the SQLite file only.

### C2. Chat history can break the user/assistant alternation the API requires (§10 steps 2, 3, 5)
Step 5 says on LLM failure it "stores nothing but the user message". The next turn then loads two `user` messages in a row, which goes against the alternation rule step 3 quotes. Also, a 20-message window can begin with an `assistant` message.
**Fix:** Pick one and state it:
- (a) On failure, store nothing (the frontend keeps the failed message locally with a retry affordance), or
- (b) When building `messages`, merge consecutive same-role messages and drop leading assistant messages.
Recommend (b) as defensive code regardless. Also state **how** stored `actions` get into an assistant turn's `content`, since `content` must be a string. For example, append `\n\n[Executed actions: <compact JSON>]`, or re-serialize the turn as the `ChatResponse` JSON plus results.

### C3. A synchronous LLM call will freeze SSE for every client (§10)
The skill's snippet uses the sync `Anthropic()` client. If it is called from an `async def` route, the event loop blocks for the length of the LLM call (often 1 to 5 s), and all SSE streams and the simulator loop stall. The same applies to sync `sqlite3` calls inside `async def` routes, though they are short.
**Fix:** In §10, require either `AsyncAnthropic` with `await client.messages.parse(...)`, or a plain `def` route (FastAPI runs it in the threadpool), or `asyncio.to_thread`. In §7, say that DB access goes through one module with one connection guarded by a lock (or a connection per call), with `check_same_thread=False` if it is shared.

### C4. `StaticFiles(directory="static")` crashes local backend dev (§4, §12)
Starlette's `StaticFiles` raises `RuntimeError: Directory 'static' does not exist` at startup when the directory is missing. That is the case in the local-dev flow `cd backend && uv run uvicorn app.main:app`. The relative path also depends on the current working directory.
**Fix:** Resolve the path relative to the `app` package (for example `Path(__file__).parent.parent / "static"`) and mount it only if it exists, or pass `check_dir=False`. Add `backend/static/` to `.gitignore`.

### C5. Dockerfile CMD will not find `uvicorn`, and dev deps question (§12)
`uv sync --frozen` installs into `/app/.venv`, which is not on `PATH`, so `CMD uvicorn ...` fails.
**Fix:** Use `uv sync --frozen --no-dev` (dev extras are already optional, but say so) and either `ENV PATH="/app/.venv/bin:$PATH"` or `CMD ["uv", "run", "--no-sync", "uvicorn", ...]`. Copy `pyproject.toml` and `uv.lock` before the source for layer caching.

---

## Should fix

### S1. Stale section cross-references after renumbering
- §4 "the single `docker run` command (Section 11)" should be **Section 12**.
- §5 "deterministic mock LLM responses (Section 9)" should be **Section 10**.
- §7 "The frontend refetches `/api/portfolio` every 30 seconds while open (Section 10)" should be **Section 11**.
- §6 and §8 "the executed-actions object defined in Section 8" is correct; check the rest.

### S2. `LLM_ERROR` status code is missing from the error-shape list (§8 vs §10)
§8 lists `400 / 404 / 503`, but §10 step 5 returns `502` for `LLM_ERROR`. Add `502` to §8. Also, FastAPI's default `422` for malformed bodies (missing `side`, `quantity` as a string, `side: "short"`) doesn't match the error shape.
**Fix:** Require a `RequestValidationError` handler that returns `400` in the standard shape, and add a generic code (`VALIDATION_ERROR`, or `INVALID_SIDE`) to the code list. Also define the shape for unexpected `500`s (`INTERNAL_ERROR`).

### S3. The 30 s snapshot threshold races the 30 s refetch (§7, §11)
The frontend refetches every 30 s and the backend snapshots only when the latest one is "more than 30 seconds old". Timer jitter means a refetch often finds a 29.9 s-old snapshot and skips it, so points actually arrive about every 60 s. Each refetch also calls both `/api/portfolio` and `/api/portfolio/history` together, so two concurrent requests can both see "stale" and write duplicate snapshots. GETs with write side effects are also surprising.
**Fix (either):**
- Keep the policy but use a threshold of about 25 s, and serialize the check-and-insert under the same lock or transaction as trades, or
- **Simpler:** run a 30 s snapshot background task in the lifespan (the app already runs one background task, so this is about 10 lines), and drop snapshot-on-read. Growth is about 2,880 rows per day, which the 24 h window plus downsampling already handles.

### S4. The frontend needs to know which tickers to show in the watchlist (§6, §11)
The SSE map contains `watchlist ∪ positions`, so held-but-unwatched tickers appear in the stream. Also, `PriceCache.remove()` doesn't bump `version`, so after a remove the stream doesn't re-emit until the next tick (up to 15 s in Massive mode), and the client keeps the stale entry.
**Fix:** In §11, state that watchlist rows come from `/api/watchlist` and SSE is used only as a price lookup. Optionally make `PriceCache.remove()` bump `version` (one line, add it to the §6 TODO list).

### S5. "Changed" should be defined by `direction`/price, not only `timestamp` (§6, §11)
The simulator rewrites every ticker every 500 ms with a new `timestamp`. Prices are rounded to 2 dp, so many ticks are `"flat"`. With timestamp-only detection every ticker "changes" every event, and flat ticks would flash.
**Fix:** Append a chart point when `timestamp` changed. Flash only when `price !== lastPrice`, green if up and red if down.

### S6. Lightweight Charts time handling (§11 Buffers / Technical Notes)
Lightweight Charts requires strictly ascending `time` values and is designed around whole-second `UTCTimestamp`s. At 2 ticks per second, two points fall in the same second.
**Fix:** Floor timestamps to seconds and use `series.update()`, which replaces the last bar when the time is equal. With 1 point per second effective, 3,600 points is about 60 min, not 30; update the comment. Also note the library touches `window`, so chart components must be client-only (`"use client"` plus `dynamic(..., { ssr: false })` or a `useEffect` init), or `next build` prerender fails.

### S7. Buffer ownership is ambiguous (§11 Buffers)
Is the 3,600-point main-chart buffer kept for the selected ticker only, or for every ticker? If only for the selected one, clicking a different ticker shows an empty chart.
**Fix:** Keep one per-ticker buffer (cap 3,600) for all tracked tickers, with sparklines rendering the last 300. Memory is trivial (about 10 to 20 tickers × 3,600 points). State that the main chart starts empty on page load (there is no history endpoint).

### S8. Order of LLM action execution and buy side effects (§9, §10 step 6)
"Auto-executes any trades and watchlist changes in order": trades first, or watchlist first? Also, §9 says a buy of an untracked ticker adds it to the watchlist **first**. If the buy then fails (`NO_PRICE`, `INSUFFICIENT_CASH`), does the ticker stay on the watchlist?
**Fix:** Specify "watchlist_changes first, then trades, each list in order" (so "watch PYPL and buy 5" works). Also specify that the watchlist add from a buy persists even if the buy fails (simpler) or is rolled back. In Massive mode a buy of a new ticker always fails `NO_PRICE` until the next poll (up to 15 s). Say that this is expected and the system prompt should tell the model to buy only tickers that already have a price.

### S9. Mock-mode keyword precedence (§10 LLM Mock Mode)
"buy and watch TSLA" matches both `buy` and `watch`. The table order suggests first match wins, but say it. Also "sell" against an empty portfolio returns `INSUFFICIENT_SHARES`. That is fine, but the E2E test should buy first.
**Fix:** "Rules are checked top to bottom; first match wins; matching is substring on the lowercased message."

### S10. Units are inconsistent across percent fields (§8)
`unrealized_pnl_percent: 0.65` and `session_change_percent: 0.647` are percent units, but `weight: 0.192` is a fraction. That's defensible, but state it explicitly, and state that `weight = market_value / total_value` (including cash) rather than `/ positions_value`. The heatmap sizes by weight either way.

### S11. The example portfolio numbers don't add up (§8)
10 × 192.48 = 1924.80 and 8087.70 + 1924.80 = 10012.50, but the top level shows `positions_value: 1924.75`, `total_value: 10012.45` and `unrealized_pnl: 12.45`. Agents write tests from examples. Fix the example to `1924.80 / 10012.50 / 12.50`.

### S12. Next.js `output: 'export'` together with `rewrites()` (§4, §11)
Next warns that rewrites don't apply with `output: 'export'`, and SSE through the Next dev proxy can get buffered or compressed, so the stream arrives in bursts or not at all.
**Fix:** Make `output: 'export'` conditional on `NODE_ENV === 'production'` and define `rewrites()` only in dev. Add a note: "if SSE is buffered through the dev proxy, allow `NEXT_PUBLIC_API_BASE=http://localhost:8000` for the EventSource in dev, plus a dev-only CORS allowance on the backend." Otherwise the frontend agent will lose time debugging this.

### S13. Pin the trade arithmetic precisely (§7, §9)
Spell out: `qty = round(qty, 4)`, which must still be `> 0` after rounding (0.00001 becomes 0, so `INVALID_QUANTITY`); `cost = round(qty * price, 2)`; the cash check is `cash >= cost`; and `cash = round(cash - cost, 2)`. Without this, agents disagree at the edges and tests become flaky.

### S14. Missing `.env` handling in the start scripts (§12)
`docker run --env-file .env` fails hard if `.env` is missing. A first-time student who skipped `cp .env.example .env` gets a confusing error.
**Fix:** The scripts should create `.env` from `.env.example` (or an empty one) with a warning, since the app runs without any keys (simulator, and chat returns 503).

### S15. README contradicts PLAN on DB init
`README.md` still says "SQLite with lazy initialization", while §7 now mandates startup initialization. Update the README, or add it to the §14 checklist so an agent fixes it.

### S16. §5 labels `ANTHROPIC_API_KEY` as "Required"
This conflicts with the behavior list right below it (the app starts without it, and mock mode doesn't need it). Relabel it "Required for real AI chat".

---

## Nice to have

- **N1. Connection status "red" is almost unreachable (§11).** `EventSource` only reaches `CLOSED` on a non-200 or wrong-content-type response. When the server is down it stays in `CONNECTING` forever (yellow). Consider "yellow for more than ~5 s, then red", or say that yellow covers both cases.
- **N2. Sparklines with Lightweight Charts (§11).** Ten to twenty separate canvas chart instances for sparklines is heavy. An inline SVG `<polyline>` (not a library) is simpler and lighter. Explicitly allow it, or keep the rule and accept the cost.
- **N3. Header total before the first SSE event (§11).** Say that it falls back to `current_price` from `/api/portfolio`, mirroring the backend's `avg_cost` fallback.
- **N4. Positions table "% change" (§2, §11).** Clarify that this is `unrealized_pnl_percent` (vs avg cost), not session change.
- **N5. History window edge (§8).** After a restart days later, the 24 h window drops the seed snapshot and the chart is nearly empty. Consider always including the most recent snapshot before the window as the first point.
- **N6. Watchlist size cap.** Nothing stops the LLM from adding 200 tickers. A soft cap (for example 50, error `INVALID_TICKER` or a new `WATCHLIST_FULL`) protects Cholesky rebuild cost and UI layout.
- **N7. Chat UX guards.** Disable chat input while a request is in flight (prevents concurrent turns interleaving history). On `503 LLM_UNAVAILABLE`, show a clear "set ANTHROPIC_API_KEY or LLM_MOCK=true" hint.
- **N8. Massive poll interval** is described as "configurable" but there is no env var. Add `MASSIVE_POLL_INTERVAL` (default 15) to §5, or drop the word.
- **N9. Move `rich` to dev dependencies** in `backend/pyproject.toml`. It's only used by `market_data_demo.py`, and keeping it in core deps adds weight to the production image.
- **N10. E2E determinism (§13).** Recommend a `data-testid` convention (for example `watchlist-row-AAPL`, `cash-balance`, `position-row-AAPL`, `heatmap-cell-AAPL`). Have the test runner wait on `/api/health`. Pin the Playwright Docker image tag to the `@playwright/test` version. "Heatmap renders with correct colors" is hard to assert; assert a `data-pnl-sign="positive|negative"` attribute instead of computed colors.
- **N11. Docker `HEALTHCHECK`** using `/api/health` is free and helps the test compose file use `depends_on: condition: service_healthy`.
- **N12. Store user-message timestamps at receipt** (before the LLM call) so ordering in `chat_messages` stays correct even though both rows are written after the call (§10 step 7).
- **N13. Over-engineering check.** `user_id` columns and Massive previous-close-as-session-open are cheap and fine. The one place to simplify is snapshot-on-read (see S3). Everything else is appropriately minimal.
