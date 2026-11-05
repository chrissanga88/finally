# Backend — Developer Guide

## Setup

```bash
cd backend
uv sync --extra dev        # Python 3.12 (.python-version) + all deps
```

## Tests and lint

```bash
uv run --extra dev pytest -v
uv run --extra dev pytest --cov=app --cov-report=term-missing
uv run --extra dev ruff check app tests && uv run --extra dev ruff format app tests
```

## Market data API (`app/market/`)

Full design: `planning/MARKET_DATA_DESIGN.md`. Import only from the package:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source, create_stream_router
```

- **`PriceUpdate`** — frozen dataclass: `ticker`, `price`, `previous_price`, `timestamp` (Unix s), `session_open`; properties `change`, `change_percent` (tick-over-tick, not for display), `direction` (`up`/`down`/`flat`), `session_change_percent` (for the watchlist); `to_dict()` is the SSE per-ticker object.
- **`PriceCache`** — thread-safe store:
  - `update(ticker, price, timestamp=None, session_open=None) -> PriceUpdate`
  - `get(ticker)`, `get_price(ticker)`, `get_all()` (copy), `remove(ticker)`
  - `version` — bumps on every update and every effective remove (SSE change detection)
  - First price seen is the session open unless `session_open` is passed (Massive passes the previous close).
- **`MarketDataSource`** — `start(tickers)` (once), `add_ticker`, `remove_ticker` (also clears the cache), `get_tickers`, `stop` (idempotent). Callers normalize/validate tickers.
- **`create_market_data_source(cache)`** — `MASSIVE_API_KEY` set → `MassiveDataSource` (poll interval `MASSIVE_POLL_INTERVAL`, default 15 s; free keys fall back to end-of-day closes); otherwise `SimulatorDataSource` (GBM, 0.5 s ticks).
- **`create_stream_router(cache)`** — new `APIRouter` with `GET /api/stream/prices` (SSE).

Rules for app code:
- Read prices only from the cache; never call a source for a price.
- Track exactly `watchlist ∪ positions`: after every watchlist change and trade, call one
  `sync_tracking(market, ticker)` helper (design §10.3).
- The simulator prices a newly added ticker immediately; Massive prices it on the next poll.

## Tests

`tests/market/` — 161 tests, 100% coverage. Massive tests use real SDK models
(`TickerSnapshot.from_dict`, `GroupedDailyAgg.from_dict`) and a fake client; simulator tests
use a seeded `np.random.default_rng`.
