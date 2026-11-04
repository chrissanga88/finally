# Market Data Interface — Unified Python API

How FinAlly gets stock prices: one interface, two implementations (Massive API or simulator), selected by `MASSIVE_API_KEY`. All downstream code (SSE, portfolio valuation, trade fills, LLM context) reads from a shared in-memory `PriceCache` and never knows which source is running.

This describes the design **as implemented in `backend/app/market/`**, plus the changes still required (marked **CHANGE**). API details for Massive are in `MASSIVE_API.md`; the simulator is in `MARKET_SIMULATOR.md`.

---

## 1. Architecture

```
               ┌──────────────────────────┐
  env ───────► │ create_market_data_source│  MASSIVE_API_KEY set? → Massive : Simulator
               └────────────┬─────────────┘
                            │ returns unstarted MarketDataSource
           ┌────────────────┴─────────────────┐
  SimulatorDataSource                 MassiveDataSource
  (GBM step every 0.5s)               (REST poll every 15s)
           └────────────────┬─────────────────┘
                            │ cache.update(ticker, price, ts, session_open)
                            ▼
                     ┌─────────────┐      version counter
                     │ PriceCache  │ ───────────────────────► SSE /api/stream/prices
                     └─────────────┘ ◄─── get / get_price ─── trades, portfolio, chat
```

**Push model:** sources *write* to the cache on their own schedule; consumers *read* the cache. Nobody calls a source to fetch a price on demand. This keeps request latency independent of the upstream API and means rate limits are governed by one poll loop, not by user traffic.

### Module layout

```
backend/app/market/
├── __init__.py        # public exports
├── models.py          # PriceUpdate (frozen dataclass)
├── cache.py           # PriceCache (thread-safe)
├── interface.py       # MarketDataSource (ABC)
├── factory.py         # create_market_data_source()
├── simulator.py       # GBMSimulator + SimulatorDataSource
├── seed_prices.py     # simulator seed prices / params / correlations
├── massive_client.py  # MassiveDataSource
└── stream.py          # SSE router factory
```

Public imports: `from app.market import PriceUpdate, PriceCache, MarketDataSource, create_market_data_source, create_stream_router`.

---

## 2. `PriceUpdate` — the price record

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float              # rounded to 2 dp
    previous_price: float     # price at the previous update (tick-over-tick)
    timestamp: float          # Unix seconds (float)
    session_open: float       # CHANGE: first price seen this session (see §3)

    change: float             # property: price - previous_price
    change_percent: float     # property: tick-over-tick %, NOT for display
    direction: str            # property: "up" | "down" | "flat"
    session_change_percent: float  # CHANGE property: (price - session_open) / session_open * 100

    def to_dict(self) -> dict  # SSE / API JSON; CHANGE: add session_open, session_change_percent
```

`to_dict()` output is the SSE per-ticker object in PLAN.md §6. Percent fields are in percent units (0.65 = 0.65%), rounded to 4 dp.

---

## 3. `PriceCache` — the shared store

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None,
               session_open: float | None = None) -> PriceUpdate
    def get(self, ticker: str) -> PriceUpdate | None
    def get_price(self, ticker: str) -> float | None
    def get_all(self) -> dict[str, PriceUpdate]          # shallow copy
    def remove(self, ticker: str) -> None
    @property
    def version(self) -> int                            # bumps on every write
    def __len__(self) -> int
    def __contains__(self, ticker: str) -> bool
```

- **Thread-safe** (`threading.Lock`): the Massive SDK runs in a worker thread via `asyncio.to_thread`, and FastAPI sync routes run in the threadpool. Hold the lock only for dict operations.
- **`previous_price`** is the cached price before this update; on the first update it equals `price` (`direction="flat"`).
- **CHANGE — session open.** The cache remembers `session_open` per ticker:
  - If the caller passes `session_open` (Massive: `prev_day.close`), use it — the cache's value is overwritten so it tracks the upstream reference.
  - Otherwise keep the existing value, or set it to the first price seen.
  - `remove(ticker)` forgets it, so a re-added ticker starts a fresh session.
- **CHANGE — `remove()` bumps `version`**, so SSE immediately re-emits a map without the removed ticker.
- `timestamp` defaults to `time.time()`. **Bug to fix:** `ts = timestamp or time.time()` treats `0.0` as missing; use `timestamp if timestamp is not None else time.time()`.

---

## 4. `MarketDataSource` — the interface

```python
class MarketDataSource(ABC):
    async def start(self, tickers: list[str]) -> None      # call once; seeds cache, starts background task
    async def stop(self) -> None                          # idempotent; cancels task
    async def add_ticker(self, ticker: str) -> None       # idempotent
    async def remove_ticker(self, ticker: str) -> None    # idempotent; also cache.remove(ticker)
    def get_tickers(self) -> list[str]
```

Contract every implementation honours:

| Rule | Why |
|---|---|
| Never raise out of the background loop; log and continue | One bad tick/poll must not kill pricing for the process |
| `start()` populates the cache before returning when it cheaply can (simulator: always; Massive: first poll) | SSE and trades have prices immediately |
| `add_ticker` is fast and non-blocking | Called from request handlers (watchlist add, buy) |
| Tickers are normalized by the **caller** (uppercase, trimmed, validated per PLAN.md §8) | One place for ticker rules; sources may defensively `.upper()` |
| Sources only write to the cache; they hold no portfolio/watchlist knowledge | Keeps the "which tickers are tracked" policy in one place (§6) |

### Factory

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        return MassiveDataSource(api_key=key, price_cache=price_cache,
                                 poll_interval=float(os.environ.get("MASSIVE_POLL_INTERVAL", 15)))
    return SimulatorDataSource(price_cache=price_cache)
```

`MASSIVE_POLL_INTERVAL` is an optional extra (seconds; default 15 for the free-tier budget, 2–5 is sensible on paid plans). If added, list it in `.env.example`.

---

## 5. `MassiveDataSource`

### Poll cycle

```python
async def _poll_once(self) -> None:
    if not self._tickers:
        return
    try:
        snaps = await asyncio.to_thread(self._client.get_snapshot_all,
                                        market_type="stocks", tickers=list(self._tickers))
    except BadResponse as e:
        if _is_not_authorized(e):          # 403: plan lacks snapshots (free tier)
            await self._switch_to_eod_mode()
            return
        logger.warning("Massive poll failed: %s", e); return
    except Exception as e:
        logger.warning("Massive poll failed: %s", e); return

    for s in snaps:
        price, ts = _extract_price(s)
        if price is None:
            continue
        self._cache.update(s.ticker, price, ts,
                           session_open=s.prev_day.close if s.prev_day and s.prev_day.close else None)
```

Copy the ticker list (`list(self._tickers)`) before handing it to the worker thread, so a concurrent `add_ticker` can't mutate it mid-request.

### CHANGE — price extraction (fixes a live bug)

The current code reads `snap.last_trade.timestamp / 1000`. The SDK's `LastTrade` has **no `timestamp` attribute** — the trade time is `sip_timestamp` in **nanoseconds** — so against the real API every snapshot raises `AttributeError` and is skipped, leaving the cache empty. The unit tests pass only because they use `MagicMock` objects with a `.timestamp` field.

```python
def _extract_price(s) -> tuple[float | None, float | None]:
    """Best available price + Unix-seconds timestamp from a TickerSnapshot."""
    lt = s.last_trade
    if lt and lt.price:                                   # plans with trades
        return lt.price, (lt.sip_timestamp / 1e9 if lt.sip_timestamp else None)
    if s.min and s.min.close:                             # latest minute bar
        return s.min.close, (s.min.timestamp / 1e3 if s.min.timestamp else None)
    if s.day and s.day.close:                             # today's bar so far
        return s.day.close, (s.updated / 1e9 if s.updated else None)
    if s.prev_day and s.prev_day.close:                   # pre-market / overnight reset
        return s.prev_day.close, None
    return None, None
```

Tests should build snapshots with the real model classes (`TickerSnapshot.from_dict({...})` using the JSON from `MASSIVE_API.md` §3.1) instead of `MagicMock`, so attribute-name mistakes fail.

### CHANGE — free-plan (EOD) mode

Free Basic keys get **403** from the snapshot endpoint. Rather than failing forever, the source degrades:

1. On the first 403 from the snapshot, log a single warning ("Massive key has no snapshot access; using end-of-day prices") and set `self._mode = "eod"`.
2. In EOD mode, fetch `get_grouped_daily_aggs(date)` for the most recent trading day (walk back over weekends; ≤ 1 call per day tried), write `close` for each tracked ticker with `session_open = close` (session change 0%), timestamp = bar time.
3. Re-fetch EOD data only when the date changes (check every poll, call at most hourly) — the price won't change intraday.
4. `add_ticker` in EOD mode: look the ticker up in the last grouped result held in memory and write it immediately — no API call. Unknown → no price (buys fail `NO_PRICE`, as PLAN.md §9 expects).

Free-plan users therefore see real but static prices. That is the honest outcome of the plan; the README should say "a paid Massive plan is needed for intraday prices; otherwise leave `MASSIVE_API_KEY` empty to use the simulator".

### Other behaviour

- `start()`: create `RESTClient(api_key=...)` (pass the key explicitly — the SDK's env default is read at import, before `.env` is loaded), do one immediate poll, then start the loop.
- `add_ticker`: append; price appears on the next poll (≤ `poll_interval`). Optionally trigger an early poll if the free budget allows — not required.
- `remove_ticker`: drop from the list and `cache.remove()`.
- Errors: 401 logs at `ERROR` once per distinct message; 429 doubles the interval up to 60 s, resetting after a success.
- Outside market hours (optional): check `get_market_status()` once every few minutes and poll every 60 s while `market == "closed"`.

---

## 6. `SimulatorDataSource`

Wraps `GBMSimulator` (see `MARKET_SIMULATOR.md`) in an asyncio loop: `step()` every 0.5 s → `cache.update()` for each ticker. `start()` and `add_ticker()` write the seed price to the cache immediately, so a newly added ticker is tradeable at once. `remove_ticker()` removes from both the simulator and the cache. `session_open` is not passed — the cache records the first (seed) price as the session open.

---

## 7. Integration with the app

### Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                                           # app.db — PLAN.md §7
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(sorted(db.watchlist_tickers() | db.position_tickers()))
    app.state.price_cache, app.state.market = cache, source
    yield
    await source.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(cache))       # see note below
```

`create_stream_router(cache)` needs the cache at router-creation time, but the cache is created in the lifespan. Either create the `PriceCache` at module level (it has no I/O, so this is safe) and pass it to both the router and the lifespan, or change the SSE route to read `request.app.state.price_cache`. **Recommended:** module-level cache. Also note `stream.py` declares `router` at module level and adds the route inside the factory — calling the factory twice (e.g. in tests) registers the route twice; create the `APIRouter` inside the factory.

### Tracked-ticker policy (one helper, used by every write path)

```python
async def sync_tracking(ticker: str) -> None:
    """Make the source track `ticker` iff it is on the watchlist or held."""
    wanted = db.on_watchlist(ticker) or db.has_position(ticker)
    tracked = ticker in market.get_tickers()
    if wanted and not tracked:
        await market.add_ticker(ticker)
    elif tracked and not wanted:
        await market.remove_ticker(ticker)
```

Call it after: watchlist add, watchlist remove, and every trade (buy may add a ticker; a closing sell may remove one). This implements PLAN.md §6 "Which Tickers Are Tracked" without each route re-deriving the rule.

### Readers

| Consumer | Call |
|---|---|
| Trade fill | `cache.get_price(t)` → `None` ⇒ `400 NO_PRICE` |
| Portfolio valuation | `cache.get_price(t) or position.avg_cost` |
| Watchlist API | `cache.get(t)` → `price`, `session_change_percent` or `null` |
| LLM context | `cache.get_all()` filtered to watchlist ∪ positions |
| SSE | `cache.version` change → `cache.get_all()` → `to_dict()` per ticker |

---

## 8. Testing

- **Cache:** session_open first-seen vs. explicit; `remove()` bumps version and clears session_open; `timestamp=0.0` respected.
- **Models:** `to_dict()` includes `session_open` / `session_change_percent`; zero `session_open` → 0.0.
- **Massive:** use real SDK model objects; cover each branch of `_extract_price`; nanosecond/millisecond conversion; 403 → EOD mode; EOD walk-back over a weekend; poll exceptions don't escape the loop; ticker list copied before threading.
- **Factory:** empty/whitespace key → simulator; key → Massive with explicit `api_key`.
- **Tracking helper:** watchlist removal keeps a held ticker; closing sell drops an unwatched ticker.

## 9. Summary of required changes

| # | File | Change |
|---|---|---|
| 1 | `models.py`, `cache.py` | `session_open` + `session_change_percent` (PLAN.md §6 TODO) |
| 2 | `cache.py` | `remove()` bumps `version`; `timestamp is not None` check |
| 3 | `massive_client.py` | ✅ `_extract_price` using `sip_timestamp` (ns) with fallbacks. Still to do: pass `prev_day.close` as `session_open` (needs #1) |
| 4 | `massive_client.py` | ✅ 403 → EOD mode via grouped daily aggs |
| 5 | `massive_client.py` | ✅ copy ticker list before `to_thread`. Still to do: 429 backoff |
| 6 | `stream.py` | create `APIRouter` inside the factory |
| 7 | `tests/market/test_massive.py` | ✅ real SDK models instead of `MagicMock` |
| 8 | app wiring | module-level `PriceCache`, lifespan start/stop, `sync_tracking` helper |
