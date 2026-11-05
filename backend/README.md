# FinAlly Backend

FastAPI backend for FinAlly, managed with `uv`. This directory currently contains the
market data subsystem (`app/market/`); the rest of the backend (database, portfolio,
watchlist, chat routes) is still to be built. Design: `planning/MARKET_DATA_DESIGN.md`.

## Setup

```bash
cd backend
uv sync --extra dev
```

## Tests and lint

```bash
uv run --extra dev pytest -v
uv run --extra dev pytest --cov=app --cov-report=term-missing
uv run --extra dev ruff check app tests
```

## Market data in one minute

```python
from app.market import PriceCache, create_market_data_source

cache = PriceCache()
source = create_market_data_source(cache)   # simulator unless MASSIVE_API_KEY is set
await source.start(["AAPL", "MSFT"])
cache.get_price("AAPL")                     # latest price, or None
await source.add_ticker("PYPL")
await source.remove_ticker("MSFT")
await source.stop()
```

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Empty → GBM simulator. Set → Massive REST polling. Intraday prices need a paid Massive plan; free keys fall back to end-of-day prices |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between Massive polls (minimum 1) |
