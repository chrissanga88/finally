# Market Data Backend — Summary

**Status:** Complete, tested and reviewed (161 tests, 100% coverage; review: `MARKET_DATA_REVIEW.md`). Built from `MARKET_DATA_DESIGN.md`, which has the full design, code walkthrough and rationale.

## Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  correlated GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Massive/Polygon.io REST poller (when MASSIVE_API_KEY is set)
        │  snapshot mode on paid plans; end-of-day closes on the free plan (403 fallback)
        ▼
   PriceCache (thread-safe, in-memory, version counter)
        │
        ├──→ SSE stream  GET /api/stream/prices
        ├──→ Portfolio valuation
        └──→ Trade execution
```

## Modules (`backend/app/market/`)

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate`: frozen dataclass incl. `session_open`; `direction`, `change_percent`, `session_change_percent`, `to_dict()` (SSE shape) |
| `cache.py` | `PriceCache`: thread-safe store; tracks session open per ticker; `version` bumps on update and on remove |
| `interface.py` | `MarketDataSource`: `start / stop / add_ticker / remove_ticker / get_tickers` |
| `seed_prices.py` | Seed prices, per-ticker drift/volatility, correlation groups |
| `simulator.py` | `GBMSimulator` (Cholesky-correlated GBM + random 2–5% events, injectable RNG) and `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource`: snapshot polling, `prev_day.close` as session open, EOD fallback, 429 backoff |
| `factory.py` | `create_market_data_source()`: picks a source from `MASSIVE_API_KEY`; reads `MASSIVE_POLL_INTERVAL` |
| `stream.py` | `create_stream_router()`: SSE with `retry: 1000`, version-based change detection, `{}` on empty, keepalives |

PLAN §6 follow-ups are done: `session_open` / `session_change_percent` are in the SSE payload, and `PriceCache.remove()` bumps `version`.

## Usage

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

cache = PriceCache()
source = create_market_data_source(cache)          # reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT"])      # watchlist ∪ positions
app.include_router(create_stream_router(cache))

cache.get_price("AAPL")                            # float | None
cache.get("AAPL").session_change_percent           # for the watchlist
await source.add_ticker("PYPL")
await source.remove_ticker("GOOGL")
await source.stop()
```

## Still to build (outside the market package)

- App wiring: `app/main.py` lifespan, `sync_tracking()` helper and route dependencies (design §10)
- `.env.example` entry for `MASSIVE_POLL_INTERVAL`
- Massive behaviour against a live key is unverified; tests use real SDK models with a fake client
