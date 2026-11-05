# Market Data Backend — Detailed Design

This is the implementation blueprint for FinAlly's market data subsystem: one unified Python API, two price sources (a GBM simulator and the Massive/Polygon.io REST API), a shared in-memory price cache, and the SSE stream. An agent can build `backend/app/market/` from this document alone. The code snippets are meant to be used as written, not just as illustrations.

It consolidates and supersedes the open **CHANGE** items in the reference docs:

| Doc | What it covers | Status after this design |
|---|---|---|
| `PLAN.md` §6, §8, §9, §13 | Product contract (SSE shape, tracked tickers, trade rules) | Authoritative; this design implements it |
| `MARKET_INTERFACE.md` | Unified API, Massive integration notes | All **CHANGE** items folded in below |
| `MARKET_SIMULATOR.md` | GBM math and rationale | Math unchanged; injectable RNG folded in |
| `MASSIVE_API.md` | Massive endpoints, plans, SDK field names | Reference for §8 |
| `REVIEW.md` | PLAN review (S4: `remove()` bumps version; N8: poll interval env var) | Folded in |

> **Note:** `backend/` is currently empty in the working tree. An earlier implementation exists in git history (`git show HEAD:backend/app/market/<file>.py`). This design keeps its proven structure and fixes every known gap (see §13).

---

## Contents

1. [Architecture](#1-architecture)
2. [Module layout and dependencies](#2-module-layout-and-dependencies)
3. [`PriceUpdate`: the price record](#3-priceupdate-the-price-record)
4. [`PriceCache`: the shared store](#4-pricecache-the-shared-store)
5. [`MarketDataSource`: the unified interface](#5-marketdatasource-the-unified-interface)
6. [Factory and configuration](#6-factory-and-configuration)
7. [Simulator](#7-simulator)
8. [Massive API source](#8-massive-api-source)
9. [SSE stream](#9-sse-stream)
10. [App integration](#10-app-integration)
11. [Behaviour walkthroughs](#11-behaviour-walkthroughs)
12. [Testing](#12-testing)
13. [Changes vs. the previous implementation](#13-changes-vs-the-previous-implementation)
14. [Build order checklist](#14-build-order-checklist)

---

## 1. Architecture

```
                 env: MASSIVE_API_KEY, MASSIVE_POLL_INTERVAL
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │ create_market_data_source()   │  key set? → Massive : Simulator
                  └───────────────┬───────────────┘
                                  │ unstarted MarketDataSource
            ┌─────────────────────┴──────────────────────┐
  SimulatorDataSource                          MassiveDataSource
  GBMSimulator.step() every 0.5 s              REST poll every 15 s (snapshot mode)
  correlated GBM + random events               or EOD closes (free plan, 403 fallback)
            └─────────────────────┬──────────────────────┘
                                  │ cache.update(ticker, price, ts, session_open?)
                                  ▼
                          ┌──────────────┐  version++ on every write/remove
                          │  PriceCache  │──────────────────────────► SSE /api/stream/prices
                          └──────┬───────┘
                                 │ get / get_price / get_all
          ┌──────────────────────┼───────────────────────┬──────────────────┐
     trade fills           portfolio valuation      watchlist API       LLM context
```

**Push model.** Sources *write* to the cache on their own schedule, and consumers only *read* the cache. No request handler ever asks a source for a price. As a result:

- Request latency does not depend on the upstream API.
- The Massive rate limit is consumed by one poll loop, not by user traffic.
- Every downstream module works identically against either source.

**One writer, many readers.** Exactly one source runs per process. The cache is thread-safe because the Massive SDK runs in a worker thread (`asyncio.to_thread`) and FastAPI sync (`def`) routes run in the threadpool.

**Tracking policy lives outside the sources.** Sources only know "the tickers I was told to track". The rule *track `watchlist ∪ positions`* (PLAN §6) is enforced by one app-level helper (`sync_tracking`, §10.3).

---

## 2. Module layout and dependencies

```
backend/
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app + lifespan (§10)
│   ├── tracking.py             # sync_tracking() helper (§10.3)
│   ├── db/                     # owned by the DB/backend agent — functions used here listed in §10.4
│   └── market/
│       ├── __init__.py         # public exports
│       ├── models.py           # PriceUpdate
│       ├── cache.py            # PriceCache
│       ├── interface.py        # MarketDataSource (ABC)
│       ├── seed_prices.py      # simulator static data
│       ├── simulator.py        # GBMSimulator + SimulatorDataSource
│       ├── massive_client.py   # MassiveDataSource
│       ├── factory.py          # create_market_data_source()
│       └── stream.py           # create_stream_router()
├── market_data_demo.py         # optional Rich terminal demo
└── tests/market/
    ├── test_models.py  test_cache.py  test_simulator.py  test_simulator_source.py
    ├── test_massive.py  test_factory.py  test_stream.py
    └── (tests/test_tracking.py)
```

### Public API (`app/market/__init__.py`)

```python
"""Market data subsystem for FinAlly.

    PriceUpdate               immutable price record
    PriceCache                thread-safe in-memory price store
    MarketDataSource          abstract interface for price sources
    create_market_data_source factory: simulator or Massive, from env
    create_stream_router      FastAPI router factory for GET /api/stream/prices
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
]
```

Downstream code imports **only** from `app.market`. Concrete classes (`SimulatorDataSource`, `MassiveDataSource`, `GBMSimulator`) are imported from their modules only by the factory, the tests and the demo.

### `pyproject.toml` (relevant parts)

```toml
[project]
name = "finally-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "numpy>=2.0.0",
    "massive>=1.0.0",
    "python-dotenv>=1.0.0",
    "anthropic>=0.40.0",      # LLM (PLAN §10), not market data
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.7.0",
    "rich>=13.0.0",           # demo only — keep out of the production image (REVIEW N9)
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

---

## 3. `PriceUpdate`: the price record

`app/market/models.py`

An immutable snapshot of one ticker at one moment. `to_dict()` is the **exact per-ticker object of the SSE payload** (PLAN §6).

```python
"""Data models for market data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Latest price for one ticker. Created only by PriceCache.update()."""

    ticker: str
    price: float           # rounded to 2 dp
    previous_price: float  # price at the previous update (tick-over-tick)
    timestamp: float       # Unix seconds (float)
    session_open: float    # reference price for session change % (see PriceCache)

    @property
    def change(self) -> float:
        """Absolute tick-over-tick change."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Tick-over-tick change in percent. NOT for display (PLAN §6)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def session_change_percent(self) -> float:
        """Change since session_open, in percent (0.65 == 0.65%). Shown in the watchlist."""
        if self.session_open == 0:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict[str, str | float]:
        """Serialize for SSE / JSON. Keys and units match PLAN §6 exactly."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "session_open": self.session_open,
            "session_change_percent": self.session_change_percent,
        }
```

Example:

```python
>>> u = PriceUpdate("AAPL", 191.23, 191.10, 1730563200.512, 190.00)
>>> u.direction, u.change, u.session_change_percent
('up', 0.13, 0.6474)
>>> u.to_dict()
{'ticker': 'AAPL', 'price': 191.23, 'previous_price': 191.1, 'timestamp': 1730563200.512,
 'change': 0.13, 'change_percent': 0.068, 'direction': 'up',
 'session_open': 190.0, 'session_change_percent': 0.6474}
```

Design notes:
- All fields are required (no defaults). Only the cache constructs these, and it always knows every value.
- `slots=True` keeps the objects small. The SSE loop serializes every tracked ticker twice a second.
- Percent fields use percent units and are rounded to 4 dp, matching the `*_percent` convention in PLAN §8.

---

## 4. `PriceCache`: the shared store

`app/market/cache.py`

```python
"""Thread-safe in-memory price cache — the single source of truth for live prices."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker.

    Writer:  the active MarketDataSource (exactly one).
    Readers: SSE stream, trade execution, portfolio valuation, watchlist API, LLM context.

    Thread-safe: the Massive SDK runs in a worker thread and FastAPI `def` routes
    run in the threadpool. The lock is held only for dict operations.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._session_open: dict[str, float] = {}
        self._lock = Lock()
        self._version = 0  # bumped on every write AND every effective remove

    # --- writes -----------------------------------------------------------

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        session_open: float | None = None,
    ) -> PriceUpdate:
        """Record a new price and return the resulting PriceUpdate.

        - previous_price is the cached price before this call (== price on first update).
        - timestamp defaults to now. 0.0 is a valid timestamp (checked with `is not None`).
        - session_open: if given (and > 0) it overrides the stored reference, e.g.
          Massive's previous close. Otherwise the first price seen is kept as the
          session open until the ticker is removed.
        """
        ts = timestamp if timestamp is not None else time.time()
        price = round(price, 2)
        with self._lock:
            prev = self._prices.get(ticker)
            if session_open is not None and session_open > 0:
                self._session_open[ticker] = round(session_open, 2)
            open_ = self._session_open.setdefault(ticker, price)

            update = PriceUpdate(
                ticker=ticker,
                price=price,
                previous_price=prev.price if prev else price,
                timestamp=ts,
                session_open=open_,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def remove(self, ticker: str) -> None:
        """Forget a ticker (price and session open). Bumps version so SSE re-emits
        promptly without it. No-op (and no version bump) if absent."""
        with self._lock:
            existed = self._prices.pop(ticker, None) is not None
            self._session_open.pop(ticker, None)
            if existed:
                self._version += 1

    # --- reads ------------------------------------------------------------

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — safe to iterate while writers keep updating."""
        with self._lock:
            return dict(self._prices)

    @property
    def version(self) -> int:
        """Monotonic change counter for cheap SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: object) -> bool:
        with self._lock:
            return ticker in self._prices
```

### Session-open rules

| Situation | `session_open` |
|---|---|
| Simulator, first tick of AAPL at 190.00 | 190.00 (first price seen) |
| Simulator, later ticks | unchanged (190.00) |
| Massive snapshot, `prev_day.close = 189.50` | 189.50 on every poll (upstream reference wins) |
| Massive EOD mode | the close itself, so session change = 0% |
| `remove("AAPL")` then re-add | fresh session; the next first price becomes the open |

### Example

```python
cache = PriceCache()
cache.update("AAPL", 190.00)                 # first: previous=190.00, flat, open=190.00
u = cache.update("AAPL", 191.90)             # previous=190.00, up
u.session_change_percent                     # 1.0
v = cache.version                            # 2
cache.remove("AAPL")
cache.version                                # 3 → SSE re-emits without AAPL
cache.remove("AAPL"); cache.version          # 3 (no-op)
```

---

## 5. `MarketDataSource`: the unified interface

`app/market/interface.py`

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """A producer that pushes prices into a PriceCache on its own schedule.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "MSFT"])   # once, in the FastAPI lifespan
        await source.add_ticker("PYPL")        # from request handlers
        await source.remove_ticker("MSFT")
        await source.stop()                    # on shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin tracking `tickers` and start the background task.

        Populates the cache before returning when it cheaply can (simulator always;
        Massive after its first poll). Calling start() twice raises RuntimeError.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent. No cache writes afterwards."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. Idempotent. Must be fast — called from request handlers."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and remove it from the cache. Idempotent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers (a copy)."""
```

### Contract every implementation honours

| Rule | Why |
|---|---|
| The background loop never raises: log and continue | One bad tick or poll must not kill pricing for the process |
| `CancelledError` is never swallowed inside the loop | `stop()` must actually stop it |
| `start()` seeds the cache when it cheaply can | SSE and trades have prices immediately after startup |
| `add_ticker` is fast and non-blocking (no network I/O) | Called inside watchlist/trade request handlers |
| The **caller** normalizes tickers (uppercase, trim, validate `^[A-Z][A-Z.]{0,5}$`) | One place for ticker rules (PLAN §8). Sources still `.upper().strip()` defensively |
| Sources hold no watchlist/portfolio knowledge | The tracking policy lives in `sync_tracking` (§10.3) |
| `remove_ticker` also calls `cache.remove()` | A removed ticker vanishes from SSE immediately |

---

## 6. Factory and configuration

`app/market/factory.py`

```python
"""Select the market data source from environment variables."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import DEFAULT_POLL_INTERVAL, MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

MIN_POLL_INTERVAL = 1.0


def _poll_interval_from_env() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_POLL_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid MASSIVE_POLL_INTERVAL=%r; using %.0fs", raw, DEFAULT_POLL_INTERVAL)
        return DEFAULT_POLL_INTERVAL
    return max(value, MIN_POLL_INTERVAL)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty → MassiveDataSource; otherwise SimulatorDataSource.

    Returns an UNSTARTED source; the caller awaits source.start(tickers).
    Reads the environment at call time (after load_dotenv), never at import time.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _poll_interval_from_env()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Empty or whitespace → simulator. Otherwise → Massive |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (min 1). 15 fits the free tier (4 req/min); 2–5 suits paid plans |

Add `MASSIVE_POLL_INTERVAL` to `.env.example` and PLAN §5 (REVIEW N8):

```bash
# Optional: seconds between Massive polls (default 15 = free-tier safe; 2-5 on paid plans)
MASSIVE_POLL_INTERVAL=
```

---

## 7. Simulator

The math and rationale are in `MARKET_SIMULATOR.md`. In summary: GBM `S ← S·exp((μ−σ²/2)dt + σ√dt·Z)`, with `dt = 0.5 s` of trading time, correlated `Z` via Cholesky, and a 0.1% chance per ticker per tick of a 2–5% jump.

### 7.1 `seed_prices.py` (static data only)

```python
"""Seed prices and per-ticker parameters for the market simulator."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00,
}

# sigma: annualized volatility; mu: annualized drift
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},   # high vol
    "NVDA": {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},    # low vol (bank)
    "V": {"sigma": 0.17, "mu": 0.04},      # low vol (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

UNKNOWN_SEED_RANGE: tuple[float, float] = (50.0, 300.0)

CORRELATION_GROUPS: dict[str, frozenset[str]] = {
    "tech": frozenset({"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"}),
    "finance": frozenset({"JPM", "V"}),
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3   # cross-sector and unknown tickers
TSLA_CORR = 0.3          # TSLA "does its own thing"
```

### 7.2 `GBMSimulator`: pure synchronous math

There's no asyncio and no cache here. The RNG is **injectable**, so tests can reproduce exact price paths.

```python
"""GBM market simulator."""

from __future__ import annotations

import asyncio
import logging
import math

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS, CROSS_GROUP_CORR, DEFAULT_PARAMS, INTRA_FINANCE_CORR,
    INTRA_TECH_CORR, SEED_PRICES, TICKER_PARAMS, TSLA_CORR, UNKNOWN_SEED_RANGE,
)

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600      # 5,896,800
DEFAULT_TICK_SECONDS = 0.5
DEFAULT_DT = DEFAULT_TICK_SECONDS / TRADING_SECONDS_PER_YEAR   # ≈ 8.48e-8
DEFAULT_EVENT_PROBABILITY = 0.001


class GBMSimulator:
    """Correlated Geometric Brownian Motion for a dynamic set of tickers.

    Internal prices keep full float precision; only step()'s return values are
    rounded to 2 dp, so rounding never compounds.
    """

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = DEFAULT_EVENT_PROBABILITY,
        rng: np.random.Generator | None = None,
    ) -> None:
        self._dt = dt
        self._sqrt_dt = math.sqrt(dt)
        self._event_prob = event_probability
        self._rng = rng if rng is not None else np.random.default_rng()

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- public -----------------------------------------------------------

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to 2 dp}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z                      # correlated shocks

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * self._sqrt_dt * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            if self._rng.random() < self._event_prob:   # rare 2–5% jump
                magnitude = self._rng.uniform(0.02, 0.05)
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                self._prices[ticker] *= 1.0 + sign * magnitude
                logger.debug("Event on %s: %+.1f%%", ticker, sign * magnitude * 100)

            result[ticker] = round(self._prices[ticker], 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price rounded to 2 dp (what the cache would show), or None."""
        price = self._prices.get(ticker)
        return round(price, 2) if price is not None else None

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals --------------------------------------------------------

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add without rebuilding Cholesky (batch init)."""
        if ticker in self._prices:
            return
        seed = SEED_PRICES.get(ticker)
        if seed is None:                                # don't consume RNG for known tickers
            seed = float(self._rng.uniform(*UNKNOWN_SEED_RANGE))
        self._tickers.append(ticker)
        self._prices[ticker] = seed
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """O(n²) build + O(n³) factorization; only on add/remove, never per tick."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        tech, finance = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

**Why Cholesky always succeeds:** for any mix of tickers the matrix splits into a sum of positive semidefinite terms plus a strictly positive diagonal:

```
C = 0.3·J  +  0.3·J_tech  +  0.2·J_fin  +  D
    J      = all-ones matrix                      (every pair 0.3)
    J_tech = ones on the tech block (TSLA excluded; it is not in the tech set)
    J_fin  = ones on the finance block
    D      = diag(0.4 tech, 0.5 finance, 0.7 other)   → diagonal sums to 1
```

Each `J` term is PSD and `D` is positive definite, so `C` is positive definite and the factorization cannot fail. A test covers 50 mixed tickers (§12).

### 7.3 `SimulatorDataSource`: the async adapter

```python
class SimulatorDataSource(MarketDataSource):
    """Runs GBMSimulator.step() every `update_interval` seconds and writes to the cache."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = DEFAULT_TICK_SECONDS,
        event_probability: float = DEFAULT_EVENT_PROBABILITY,
        rng: np.random.Generator | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._rng = rng
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._sim is not None:
            raise RuntimeError("SimulatorDataSource already started")
        tickers = [t.upper().strip() for t in tickers]
        self._sim = GBMSimulator(tickers, event_probability=self._event_prob, rng=self._rng)
        for ticker in self._sim.get_tickers():          # seed cache → prices available now
            self._write_seed(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started: %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._write_seed(ticker)                        # tradeable immediately
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if self._sim is not None:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim is not None else []

    def _write_seed(self, ticker: str) -> None:
        price = self._sim.get_price(ticker) if self._sim else None
        if price is not None:
            self._cache.update(ticker, price)           # first write → session_open = seed

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim is not None:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)
            except Exception:                           # CancelledError is not an Exception
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

Concurrency: the loop, `add_ticker` and `remove_ticker` all run on the event-loop thread, so `GBMSimulator` needs no lock. There is no `await` inside `step()`, so an `add_ticker` can never interleave with a step halfway through.

---

## 8. Massive API source

`app/market/massive_client.py`. Endpoint and SDK details are in `MASSIVE_API.md`.

### 8.1 Modes

| Mode | When | Endpoint | Price | `session_open` | Calls |
|---|---|---|---|---|---|
| `snapshot` | Default (paid plans) | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=…` via `get_snapshot_all` | last trade → minute bar → day bar → prev close | `prev_day.close` | 1 per poll, any number of tickers |
| `eod` | After the first **403 NOT_AUTHORIZED** from the snapshot (free Basic plan) | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` via `get_grouped_daily_aggs` | Last trading day's close | the close (0% change) | ≤ 1 per ET day on success (+ weekend skips for free) |

Free-plan users see real but static prices. That is the honest limit of the plan. The README should say: *"Intraday prices need a paid Massive plan; otherwise leave `MASSIVE_API_KEY` empty to use the simulator."*

### 8.2 Error handling

| Failure | Detection | Behaviour |
|---|---|---|
| 403 on snapshot | `BadResponse` body contains `NOT_AUTHORIZED` / "not entitled" | Switch to `eod` once, with one WARNING |
| 403 on a grouped-daily date | same | Treat that date as unavailable and walk back |
| 429 rate limit | `"429"` / "too many" / "maximum requests" in the exception text *(inferred: the SDK's urllib3 retries surface as `MaxRetryError` or `BadResponse`)* | Double the poll interval, up to 60 s. Reset to base after the next success |
| 401, network error, 5xx | Any other `Exception` | Log at ERROR the first time a message appears, DEBUG for repeats. Retry next interval |
| Malformed snapshot entry | `AttributeError` / `TypeError` / `ValueError` per item | Skip that ticker, keep the rest |
| Unknown ticker | Absent from the snapshot response | No price, so buys fail `NO_PRICE` (PLAN §9) |

### 8.3 Code

```python
"""Massive (Polygon.io) REST poller."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 15.0      # free tier: 5 calls/min → 4/min leaves headroom
MAX_BACKOFF_INTERVAL = 60.0
EOD_MAX_LOOKBACK_DAYS = 7
EOD_RETRY_SECONDS = 60.0

_ET = ZoneInfo("America/New_York")

Mode = Literal["snapshot", "eod"]


def _et_today() -> date:
    """Current date in New York (patched in tests)."""
    return datetime.now(_ET).date()


def _extract_price(snap: Any) -> tuple[float | None, float | None]:
    """Best (price, unix_seconds) from a TickerSnapshot; (None, None) if no price.

    Units: last_trade.sip_timestamp and snap.updated are NANOseconds;
    min.timestamp is MILLIseconds. LastTrade has no `.timestamp` attribute.
    A None timestamp means "now" (cache default).
    """
    lt = getattr(snap, "last_trade", None)
    if lt is not None and getattr(lt, "price", None):
        ns = getattr(lt, "sip_timestamp", None)
        return lt.price, (ns / 1e9 if ns else None)

    minute = getattr(snap, "min", None)
    if minute is not None and getattr(minute, "close", None):
        ms = getattr(minute, "timestamp", None)
        return minute.close, (ms / 1e3 if ms else None)

    day = getattr(snap, "day", None)
    if day is not None and getattr(day, "close", None):
        ns = getattr(snap, "updated", None)
        return day.close, (ns / 1e9 if ns else None)

    prev = getattr(snap, "prev_day", None)          # overnight reset / pre-market
    if prev is not None and getattr(prev, "close", None):
        return prev.close, None

    return None, None


def _prev_close(snap: Any) -> float | None:
    prev = getattr(snap, "prev_day", None)
    close = getattr(prev, "close", None) if prev is not None else None
    return close or None


def _is_not_authorized(exc: BaseException) -> bool:
    """403: plan lacks access. BadResponse carries only the response body."""
    text = str(exc)
    return "NOT_AUTHORIZED" in text or "not entitled" in text.lower()


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "too many" in text or "maximum requests" in text


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive REST API (see module docs / §8.1)."""

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        client: Any | None = None,          # injectable for tests (anything RESTClient-shaped)
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._base_interval = poll_interval
        self._interval = poll_interval      # current (may grow under 429 backoff)
        self._client = client
        self._tickers: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._started = False

        self._mode: Mode = "snapshot"
        self._eod_closes: dict[str, tuple[float, float]] = {}   # ticker → (close, ts_seconds)
        self._eod_fetched_for: date | None = None
        self._eod_retry_at = 0.0                                 # time.monotonic()
        self._last_error: str | None = None

    # --- MarketDataSource -------------------------------------------------

    async def start(self, tickers: list[str]) -> None:
        if self._started:
            raise RuntimeError("MassiveDataSource already started")
        self._started = True
        if self._client is None:
            # Pass the key explicitly: the SDK's default is read at import time,
            # before load_dotenv() may have run.
            self._client = RESTClient(
                api_key=self._api_key, connect_timeout=5.0, read_timeout=10.0, retries=2
            )
        self._tickers = list(dict.fromkeys(t.upper().strip() for t in tickers))
        await self._poll_once()             # cache populated before the app serves
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs interval",
                    len(self._tickers), self._base_interval)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        if self._mode == "eod" and ticker in self._eod_closes:
            close, ts = self._eod_closes[ticker]           # in memory: no API call
            self._cache.update(ticker, close, ts, session_open=close)
        logger.info("Massive: added %s (priced on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    @property
    def mode(self) -> Mode:
        return self._mode

    # --- polling ----------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """One poll cycle. Never raises (except CancelledError)."""
        if not self._tickers or self._client is None:
            return
        try:
            if self._mode == "eod":
                await self._poll_eod()
            else:
                await self._poll_snapshot()
        except Exception as e:
            self._on_poll_error(e)
        else:
            self._on_poll_success()

    async def _poll_snapshot(self) -> None:
        tickers = list(self._tickers)        # copy: add/remove may run while the thread works
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except BadResponse as e:
            if not _is_not_authorized(e):
                raise
            logger.warning("Massive key has no snapshot access (free plan); "
                           "using end-of-day prices, which do not change intraday")
            self._mode = "eod"
            await self._poll_eod()
            return

        tracked = set(self._tickers)        # skip tickers removed during the request
        updated = 0
        for snap in snapshots:
            try:
                if snap.ticker not in tracked:
                    continue
                price, ts = _extract_price(snap)
                if price is None:
                    continue
                self._cache.update(snap.ticker, price, ts, session_open=_prev_close(snap))
                updated += 1
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning("Skipping snapshot for %s: %s", getattr(snap, "ticker", "?"), e)
        logger.debug("Massive poll: %d/%d tickers updated", updated, len(tickers))

    async def _poll_eod(self) -> None:
        """Refresh EOD closes at most once per ET day; price tracked tickers."""
        today = _et_today()
        refreshed = False
        if self._eod_fetched_for != today and time.monotonic() >= self._eod_retry_at:
            closes = await asyncio.to_thread(self._fetch_eod_closes, today)
            if closes:
                self._eod_closes, self._eod_fetched_for, refreshed = closes, today, True
                logger.info("Massive EOD: loaded %d closes", len(closes))
            else:
                self._eod_retry_at = time.monotonic() + EOD_RETRY_SECONDS
                logger.warning("Massive EOD: no daily bars found; retrying in %.0fs",
                               EOD_RETRY_SECONDS)

        for ticker in list(self._tickers):
            if ticker in self._eod_closes and (refreshed or ticker not in self._cache):
                close, ts = self._eod_closes[ticker]
                self._cache.update(ticker, close, ts, session_open=close)

    # --- error bookkeeping ------------------------------------------------

    def _on_poll_error(self, e: Exception) -> None:
        if _is_rate_limited(e):
            self._interval = min(self._interval * 2, MAX_BACKOFF_INTERVAL)
            logger.warning("Massive rate limited; poll interval now %.0fs", self._interval)
            return
        msg = f"{type(e).__name__}: {e}"
        level = logging.ERROR if msg != self._last_error else logging.DEBUG
        logger.log(level, "Massive poll failed: %s", msg)
        self._last_error = msg

    def _on_poll_success(self) -> None:
        if self._interval != self._base_interval:
            logger.info("Massive poll recovered; interval back to %.0fs", self._base_interval)
            self._interval = self._base_interval
        self._last_error = None

    # --- synchronous SDK calls (run in a worker thread) -------------------

    def _fetch_snapshots(self, tickers: list[str]) -> list[Any]:
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS, tickers=tickers
        )

    def _fetch_eod_closes(self, today: date) -> dict[str, tuple[float, float]]:
        """{ticker: (close, ts_seconds)} for the most recent trading day before `today`.

        A day's bars publish after midnight ET, so start at yesterday. Weekends are
        skipped without a call; holidays cost one call each (empty result).
        """
        d = today - timedelta(days=1)
        for _ in range(EOD_MAX_LOOKBACK_DAYS):
            if d.weekday() < 5:
                try:
                    bars = self._client.get_grouped_daily_aggs(date=d.isoformat(), adjusted=True)
                except BadResponse as e:
                    if not _is_not_authorized(e):
                        raise
                    bars = []
                if bars:
                    return {
                        b.ticker: (b.close, b.timestamp / 1e3 if b.timestamp else time.time())
                        for b in bars
                        if b.ticker and b.close
                    }
            d -= timedelta(days=1)
        return {}
```

### 8.4 Request budget

| Scenario | Calls/min |
|---|---|
| Snapshot mode, 15 s interval, any watchlist size | 4 |
| Snapshot mode, 2 s interval (paid) | 30 |
| EOD mode, steady state | ~0 (1 per ET day) |
| EOD mode, Monday morning start | 1 (Sun/Sat skipped, Fri fetched) |
| EOD after a holiday Monday, on Tuesday | 2 (Mon empty → Fri) |

### 8.5 Optional extras (not required)

- **Market-hours slowdown:** every ~5 min call `get_market_status()`. While `market == "closed"`, poll every 60 s.
- **Early poll after `add_ticker`** (snapshot mode only, since paid plans have no request cap). Replace `asyncio.sleep` in `_poll_loop` with `asyncio.wait_for(self._wake.wait(), self._interval)` and set the event in `add_ticker`. Enforce a ≥1 s minimum gap between polls.

---

## 9. SSE stream

`app/market/stream.py`

```python
"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

SSE_CHECK_INTERVAL = 0.5
SSE_KEEPALIVE_SECONDS = 15.0


def create_stream_router(price_cache: PriceCache, interval: float = SSE_CHECK_INTERVAL) -> APIRouter:
    """Build a NEW router per call (calling twice must not register the route twice)."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",     # disable proxy buffering
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = SSE_CHECK_INTERVAL,
    keepalive: float = SSE_KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Emit the full ticker map whenever the cache version changes.

    - First line: `retry: 1000` (browser reconnects 1 s after a drop).
    - An empty map `{}` IS sent when the last ticker is removed, so clients drop it.
    - A `: keepalive` comment after `keepalive` seconds of silence (Massive mode,
      EOD mode) keeps proxies from closing the idle connection.
    """
    yield "retry: 1000\n\n"
    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                payload = {t: u.to_dict() for t, u in price_cache.get_all().items()}
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= keepalive:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(interval)
    finally:
        logger.info("SSE client disconnected: %s", client)
```

### Wire format example

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":191.23,"previous_price":191.1,"timestamp":1730563200.512,"change":0.13,"change_percent":0.068,"direction":"up","session_open":190.0,"session_change_percent":0.6474},"MSFT":{...}}

data: {"AAPL":{...},"MSFT":{...}}

: keepalive

```

Notes:
- One event can follow several cache writes (the simulator writes 10 tickers per tick, so the version jumps by 10). The stream sends at most one event per 0.5 s check. That's the intended coalescing.
- Reading `version` and then `get_all()` isn't atomic. If a write lands in between, this event already carries the newer data and the next check sends it again. That's harmless.
- `CancelledError` (server shutdown, client gone) propagates. The generator must not swallow it.
- Frontend obligations (append only on a `timestamp` change, flash only on a `price` change, treat the map as a price lookup only) are in PLAN §6 and §11.

---

## 10. App integration

### 10.1 Lifespan and wiring (`app/main.py`, market-relevant parts)

```python
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]        # backend/app/main.py → finally/
load_dotenv(PROJECT_ROOT / ".env")                         # before anything reads os.environ

from fastapi import FastAPI                                # noqa: E402

from app import db                                         # noqa: E402
from app.market import PriceCache, create_market_data_source, create_stream_router  # noqa: E402

# Module-level cache: no I/O, so creating it at import time is safe, and the
# SSE router (built at import time) and the lifespan share the same instance.
price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()                                           # schema + seed (PLAN §7)
    market = create_market_data_source(price_cache)
    tracked = sorted(db.get_watchlist_tickers() | db.get_position_tickers())
    await market.start(tracked)                            # watchlist ∪ positions (PLAN §6)
    app.state.price_cache = price_cache
    app.state.market = market
    try:
        yield
    finally:
        await market.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))
# app.include_router(portfolio_router); app.include_router(watchlist_router); ...
# StaticFiles mounted LAST and only if the directory exists (PLAN §12).
```

### 10.2 Dependencies for routes (`app/deps.py`)

```python
from fastapi import Request

from app.market import MarketDataSource, PriceCache


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market(request: Request) -> MarketDataSource:
    return request.app.state.market
```

Tests then replace both with `app.dependency_overrides`, or set `app.state` directly, and skip the real lifespan.

### 10.3 Tracked-ticker policy (`app/tracking.py`)

There's a single helper, called after **every** watchlist add/remove and **every** trade. Manual and LLM paths share it.

```python
"""Keep the market data source tracking exactly watchlist ∪ positions (PLAN §6)."""

from __future__ import annotations

from app import db
from app.market import MarketDataSource


async def sync_tracking(market: MarketDataSource, ticker: str) -> None:
    wanted = db.is_on_watchlist(ticker) or db.has_position(ticker)
    tracked = ticker in market.get_tickers()
    if wanted and not tracked:
        await market.add_ticker(ticker)
    elif tracked and not wanted:
        await market.remove_ticker(ticker)
```

| Event | DB change | `sync_tracking` result |
|---|---|---|
| Watchlist add PYPL | watchlist += PYPL | `add_ticker("PYPL")` |
| Watchlist remove AAPL, still held | watchlist −= AAPL | none (AAPL keeps streaming) |
| Watchlist remove GOOGL, not held | watchlist −= GOOGL | `remove_ticker("GOOGL")` |
| Buy untracked NEW | watchlist += NEW (PLAN §9) | `add_ticker("NEW")` *before* the price lookup |
| Sell closes AAPL, not on watchlist | position deleted | `remove_ticker("AAPL")` |
| Sell closes MSFT, on watchlist | position deleted | none |

### 10.4 Functions the market layer expects from `app.db`

These are owned by the DB agent. They're listed here so the contract is explicit.

```python
def init_db() -> None: ...
def get_watchlist_tickers(user_id: str = "default") -> set[str]: ...
def get_position_tickers(user_id: str = "default") -> set[str]: ...
def is_on_watchlist(ticker: str, user_id: str = "default") -> bool: ...
def has_position(ticker: str, user_id: str = "default") -> bool: ...
```

### 10.5 Consumers

| Consumer | Call | No-price behaviour |
|---|---|---|
| Trade fill (PLAN §9) | `cache.get_price(t)` | `None` → `400 NO_PRICE` |
| Portfolio valuation (PLAN §8) | `cache.get_price(t)` | fall back to `avg_cost` |
| `GET /api/watchlist` | `cache.get(t)` → `.price`, `.session_change_percent` | both `null` |
| `POST /api/watchlist` response | same as above, after `sync_tracking` | simulator: priced; Massive: `null` until next poll |
| LLM portfolio context | `cache.get_all()` | omit ticker or `null` |
| SSE | `cache.version`, `cache.get_all()` | ticker absent from map |

Trade route sketch showing the ordering:

```python
async def execute_trade(req: TradeIn, market: MarketDataSource, cache: PriceCache) -> TradeOut:
    ticker = normalize_ticker(req.ticker)                 # INVALID_TICKER (PLAN §8)
    qty = round(req.quantity, 4)
    if qty <= 0:
        raise ApiError(400, "INVALID_QUANTITY", "Quantity must be > 0")
    if req.side == "buy" and not (db.is_on_watchlist(ticker) or db.has_position(ticker)):
        db.add_to_watchlist(ticker)                       # persists even if the buy fails
        await sync_tracking(market, ticker)               # simulator seeds a price right now
    price = cache.get_price(ticker)
    if price is None:
        raise ApiError(400, "NO_PRICE", f"No price available for {ticker} yet")
    result = db.apply_trade(ticker, req.side, qty, price) # one transaction (PLAN §9)
    await sync_tracking(market, ticker)                   # closing sell may stop tracking
    return result
```

---

## 11. Behaviour walkthroughs

### 11.1 Fresh start, simulator

1. `.env` has no `MASSIVE_API_KEY`, so the factory returns `SimulatorDataSource`.
2. `init_db()` seeds 10 watchlist tickers, so `start([AAPL, AMZN, …])` creates the GBM and writes 10 seed prices. `version` is now 10.
3. A browser connects to SSE and immediately receives `retry: 1000`, then a map of 10 tickers, each `direction: "flat"` with `session_change_percent: 0.0`.
4. Every 0.5 s: a step and 10 cache writes, then one SSE event. Roughly a third of AAPL ticks are `flat` after rounding, which is why the frontend flashes only on a price change.
5. About every 50 s an event jumps one ticker by 2–5%, and its session change % visibly moves.

### 11.2 Paid Massive key

1. The factory returns `MassiveDataSource(poll_interval=15)`. `start()` performs one snapshot call before the app serves.
2. Each ticker: price from `last_trade` (or `min`/`day`), `session_open = prev_day.close`. Session change % then matches the broker's "today's change".
3. SSE sends one event per poll (the version changes every 15 s). In between it sends a keepalive comment every 15 s if nothing changed.
4. A user adds PYPL. It goes into the list but isn't in the cache yet, so `POST /api/watchlist` returns `price: null`, and the next poll (≤ 15 s) prices it. A buy of PYPL in that window fails `NO_PRICE`. That's expected (PLAN §9).

### 11.3 Free Massive key

1. First snapshot call returns 403 `NOT_AUTHORIZED`. The source logs one WARNING and switches to `mode = "eod"`.
2. `_fetch_eod_closes(today)` walks back from yesterday, skipping weekends, and fetches grouped daily bars, about 10k tickers in one call. Closes are written with `session_open = close`.
3. Later polls make no API calls until the ET date changes. `add_ticker("PYPL")` prices immediately from memory.

### 11.4 Removing a held ticker from the watchlist

1. The user holds 5 NVDA and removes NVDA from the watchlist. `sync_tracking` sees `has_position` is true, so nothing changes.
2. NVDA disappears from `/api/watchlist` but stays in the SSE map, so the positions table and heatmap keep live prices.
3. The user sells all 5. The position row is deleted, `sync_tracking` calls `remove_ticker("NVDA")`, and the cache removal bumps the version. The next SSE event (≤ 0.5 s) omits NVDA.

---

## 12. Testing

Run: `cd backend && uv run --extra dev pytest -v` (coverage: `--cov=app`).

### 12.1 `test_models.py`

```python
from app.market.models import PriceUpdate


def make(price=191.23, prev=191.10, open_=190.00):
    return PriceUpdate("AAPL", price, prev, 1730563200.5, open_)


def test_direction_and_change():
    assert make().direction == "up"
    assert make(price=191.0).direction == "down"
    assert make(price=191.10).direction == "flat"
    assert make().change == 0.13


def test_session_change_percent():
    assert make().session_change_percent == 0.6474
    assert make(open_=0.0).session_change_percent == 0.0


def test_to_dict_has_exact_plan_keys():
    assert set(make().to_dict()) == {
        "ticker", "price", "previous_price", "timestamp", "change", "change_percent",
        "direction", "session_open", "session_change_percent",
    }
```

### 12.2 `test_cache.py`

```python
from app.market.cache import PriceCache


def test_first_update_is_flat_and_sets_session_open():
    c = PriceCache()
    u = c.update("AAPL", 190.004)
    assert (u.price, u.previous_price, u.session_open, u.direction) == (190.0, 190.0, 190.0, "flat")


def test_session_open_sticks_until_overridden():
    c = PriceCache()
    c.update("AAPL", 190.0)
    assert c.update("AAPL", 192.0).session_open == 190.0
    assert c.update("AAPL", 192.0, session_open=189.5).session_open == 189.5


def test_remove_bumps_version_and_resets_session():
    c = PriceCache()
    c.update("AAPL", 190.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1 and "AAPL" not in c
    c.remove("AAPL")
    assert c.version == v + 1                       # no-op remove doesn't bump
    assert c.update("AAPL", 200.0).session_open == 200.0


def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0


def test_get_all_is_a_copy():
    c = PriceCache()
    c.update("AAPL", 1.0)
    snapshot = c.get_all()
    c.update("MSFT", 2.0)
    assert set(snapshot) == {"AAPL"}
```

### 12.3 `test_simulator.py` (pure math, seeded RNG)

```python
import numpy as np

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator


def test_reproducible_with_seeded_rng():
    a = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(42))
    b = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(42))
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_starts_at_seed_and_unknown_in_range():
    sim = GBMSimulator(["AAPL", "ZZZZ"], rng=np.random.default_rng(1))
    assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]
    assert 50 <= sim.get_price("ZZZZ") <= 300


def test_prices_stay_positive():
    sim = GBMSimulator(["TSLA"], dt=0.01, rng=np.random.default_rng(0))   # exaggerated vol
    assert all(sim.step()["TSLA"] > 0 for _ in range(10_000))


def test_tech_correlation():
    # larger dt → ~40¢ moves per step, so 2-dp rounding doesn't distort the returns
    sim = GBMSimulator(["AAPL", "MSFT"], dt=1e-4, event_probability=0,
                       rng=np.random.default_rng(7))
    prices = np.array([[s["AAPL"], s["MSFT"]] for s in (sim.step() for _ in range(20_000))])
    r = np.diff(np.log(prices), axis=0)
    assert abs(np.corrcoef(r.T)[0, 1] - 0.6) < 0.05


def test_event_probability_one_always_jumps():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=np.random.default_rng(3))
    move = abs(sim.step()["AAPL"] / 190.0 - 1)
    assert 0.019 < move < 0.051


def test_cholesky_holds_for_many_mixed_tickers():
    tickers = list(SEED_PRICES) + [f"X{i}" for i in range(40)]
    sim = GBMSimulator(tickers, rng=np.random.default_rng(0))
    assert len(sim.step()) == 50


def test_add_remove_idempotent():
    sim = GBMSimulator(["AAPL"])
    sim.add_ticker("AAPL"); sim.add_ticker("PYPL"); sim.remove_ticker("NOPE")
    assert sim.get_tickers() == ["AAPL", "PYPL"]
```

### 12.4 `test_simulator_source.py` (async lifecycle)

```python
import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


async def test_start_seeds_cache_and_loop_updates():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL", "MSFT"])
    assert cache.get_price("AAPL") == 190.0
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version > v
    await src.stop()
    await src.stop()                                     # idempotent


async def test_add_ticker_priced_immediately_and_remove_clears_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    assert cache.get_price("PYPL") is not None
    await src.remove_ticker("PYPL")
    assert "PYPL" not in cache and "PYPL" not in src.get_tickers()
    await src.stop()


async def test_no_writes_after_stop():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL"])
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v


async def test_double_start_raises():
    src = SimulatorDataSource(PriceCache())
    await src.start(["AAPL"])
    with pytest.raises(RuntimeError):
        await src.start(["AAPL"])
    await src.stop()
```

### 12.5 `test_massive.py` (real SDK models, fake client)

Use **real** `TickerSnapshot` / `GroupedDailyAgg` objects built with `from_dict`, and a small fake client that only has the two methods the source should call. A misspelled attribute such as `last_trade.timestamp` then fails loudly, which wouldn't happen with a `MagicMock`.

```python
from datetime import date
from unittest.mock import patch

from massive.exceptions import BadResponse
from massive.rest.models.aggs import GroupedDailyAgg
from massive.rest.models.snapshot import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MAX_BACKOFF_INTERVAL, MassiveDataSource, _extract_price

NOT_AUTHORIZED = BadResponse('{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."}')
T_MS = 1_730_563_200_000


def snap(ticker, price, prev_close=None, **extra):
    d = {"ticker": ticker, "lastTrade": {"p": price, "s": 100, "x": 10, "t": T_MS * 1_000_000}}
    if prev_close:
        d["prevDay"] = {"c": prev_close}
    d.update(extra)
    return TickerSnapshot.from_dict(d)


class FakeClient:
    def __init__(self, snapshots=(), snapshot_error=None, grouped=None):
        self.snapshots, self.snapshot_error = list(snapshots), snapshot_error
        self.grouped = grouped or {}
        self.snapshot_calls, self.grouped_calls = [], []

    def get_snapshot_all(self, market_type, tickers):
        self.snapshot_calls.append(list(tickers))
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshots

    def get_grouped_daily_aggs(self, date, adjusted=True):
        self.grouped_calls.append(date)
        return self.grouped.get(date, [])


async def started(client, tickers=("AAPL",)):
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=3600, client=client)
    await src.start(list(tickers))
    return src, cache


def test_extract_price_fallbacks_and_units():
    assert _extract_price(snap("A", 10.0)) == (10.0, T_MS / 1e3)                     # ns → s
    m = TickerSnapshot.from_dict({"ticker": "A", "min": {"c": 11.0, "t": T_MS}})
    assert _extract_price(m) == (11.0, T_MS / 1e3)                                   # ms → s
    p = TickerSnapshot.from_dict({"ticker": "A", "prevDay": {"c": 9.0}})
    assert _extract_price(p) == (9.0, None)
    assert _extract_price(TickerSnapshot.from_dict({"ticker": "A"})) == (None, None)


async def test_snapshot_poll_sets_price_and_prev_close_session_open():
    src, cache = await started(FakeClient([snap("AAPL", 191.9, prev_close=190.0)]))
    u = cache.get("AAPL")
    assert (u.price, u.session_open, u.session_change_percent) == (191.9, 190.0, 1.0)
    await src.stop()


async def test_403_switches_to_eod_and_walks_back_over_weekend():
    client = FakeClient(
        snapshot_error=NOT_AUTHORIZED,
        grouped={"2026-10-30": [GroupedDailyAgg.from_dict({"T": "AAPL", "c": 190.6, "t": T_MS})]},
    )
    with patch("app.market.massive_client._et_today", return_value=date(2026, 11, 2)):  # Monday
        src, cache = await started(client)
    assert src.mode == "eod"
    assert client.grouped_calls == ["2026-10-30"]             # Sun/Sat skipped without a call
    assert cache.get("AAPL").session_change_percent == 0.0
    await src.stop()


async def test_errors_never_escape_and_rate_limit_backs_off():
    client = FakeClient(snapshot_error=BadResponse("429 Too Many Requests"))
    src = MassiveDataSource("k", PriceCache(), poll_interval=20, client=client)
    await src.start(["AAPL"])                                  # did not raise
    assert src._interval == 40
    await src._poll_once()
    assert src._interval == MAX_BACKOFF_INTERVAL               # 80 capped to 60
    client.snapshot_error, client.snapshots = None, [snap("AAPL", 1.0)]
    await src._poll_once()
    assert src._interval == 20                                 # reset after success
    await src.stop()


async def test_ticker_removed_mid_poll_is_not_written():
    client = FakeClient([snap("AAPL", 1.0), snap("MSFT", 2.0)])
    src, cache = await started(client, ["AAPL", "MSFT"])
    await src.remove_ticker("MSFT")
    await src._poll_once()
    assert "MSFT" not in cache
    await src.stop()
```

The background loop sleeps for the full interval (≥ 20 s here) before its first poll, so it never interferes with these direct `_poll_once()` calls.

### 12.6 `test_factory.py`

```python
from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def test_empty_or_whitespace_key_uses_simulator(monkeypatch):
    for value in ("", "   "):
        monkeypatch.setenv("MASSIVE_API_KEY", value)
        assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_key_uses_massive_with_interval(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "0.2")
    src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource) and src._base_interval == 1.0   # clamped


def test_bad_interval_falls_back(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "fast")
    assert create_market_data_source(PriceCache())._base_interval == 15.0
```

### 12.7 `test_stream.py`

Drive the generator directly with a fake request. That avoids the problems of reading an endless stream through a test client.

```python
import json

from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class FakeRequest:
    client = None

    def __init__(self, polls_before_disconnect):
        self.n = polls_before_disconnect

    async def is_disconnected(self):
        self.n -= 1
        return self.n < 0


async def collect(cache, polls, **kw):
    return [e async for e in _generate_events(cache, FakeRequest(polls), interval=0, **kw)]


async def test_retry_then_full_map():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    events = await collect(cache, 3)
    assert events[0] == "retry: 1000\n\n"
    data = json.loads(events[1].removeprefix("data: "))
    assert data["AAPL"]["session_change_percent"] == 0.0
    assert len(events) == 2                                   # no change → no repeat


async def test_empty_map_sent_after_last_removal():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.remove("AAPL")
    events = await collect(cache, 1)
    assert events[1] == "data: {}\n\n"


async def test_keepalive_when_idle():
    events = await collect(PriceCache(), 3, keepalive=0)
    assert ": keepalive\n\n" in events


def test_router_factory_is_reentrant():
    app = FastAPI()
    app.include_router(create_stream_router(PriceCache()))
    app.include_router(create_stream_router(PriceCache()))
    paths = [r.path for r in app.routes if getattr(r, "path", "") == "/api/stream/prices"]
    assert len(paths) == 2      # one per router; a module-level router would register 3
```

### 12.8 `tests/test_tracking.py`

Monkeypatch the four `app.db` predicates and use a `SimulatorDataSource` started on a fresh cache. Assert the six rows of the §10.3 table.

---

## 13. Changes vs. the previous implementation

| # | Area | Previous (git `HEAD`) | This design |
|---|---|---|---|
| 1 | `PriceUpdate` | No session fields | `session_open` field, `session_change_percent`, both in `to_dict()` (PLAN §6 TODO) |
| 2 | `PriceCache.update` | `timestamp or time.time()` treated `0.0` as missing | `is not None` check; `session_open` param and tracking |
| 3 | `PriceCache.remove` | No version bump | Bumps `version` when something was removed (PLAN §6 TODO, REVIEW S4) |
| 4 | Simulator RNG | Global `np.random` / `random` | Injectable `np.random.Generator` (reproducible tests) |
| 5 | Simulator `start` | Could be called twice | Raises `RuntimeError` on a second call (both sources) |
| 6 | Massive `session_open` | Not passed | `prev_day.close` (snapshot) / the close (EOD) |
| 7 | Massive errors | Logged every poll; no 429 handling | Error dedup, 429 → exponential backoff to 60 s, reset on success |
| 8 | Massive client | Constructed inside `start()` only | Injectable `client=` for tests; explicit timeouts |
| 9 | Massive mid-poll removal | Removed ticker could be re-written by an in-flight poll | Writes filtered by the current tracked set |
| 10 | Poll interval | Hard-coded 15 s | `MASSIVE_POLL_INTERVAL` env var, min 1 s (REVIEW N8) |
| 11 | SSE router | Module-level `APIRouter`; calling the factory twice registered the route twice | Router built inside the factory |
| 12 | SSE empty map | Skipped when the cache was empty, so clients kept stale tickers | `{}` is sent |
| 13 | SSE keepalive | None | `: keepalive` after 15 s idle |
| 14 | SSE cancellation | Swallowed `CancelledError` | Propagates; logging in `finally` |
| 15 | App wiring | Not built | Module-level cache, lifespan start/stop, `sync_tracking`, deps |
| 16 | `rich` | Core dependency | Dev dependency (REVIEW N9) |

---

## 14. Build order checklist

Each step is independently testable. Run the module's tests before moving on.

- [ ] `models.py` + `test_models.py`
- [ ] `cache.py` + `test_cache.py`
- [ ] `interface.py`
- [ ] `seed_prices.py`, `simulator.py` + `test_simulator.py`, `test_simulator_source.py`
- [ ] `massive_client.py` + `test_massive.py`
- [ ] `factory.py` + `test_factory.py`
- [ ] `stream.py` + `test_stream.py`
- [ ] `__init__.py` exports
- [ ] `app/main.py` lifespan, `app/deps.py`, `app/tracking.py` + `test_tracking.py` (needs the `app.db` functions in §10.4)
- [ ] `.env.example` and PLAN §5: add `MASSIVE_POLL_INTERVAL`
- [ ] Update `planning/MARKET_DATA_SUMMARY.md` (test counts, session fields) and `backend/CLAUDE.md` (cache `update()` signature)
- [ ] Manual check: `uv run uvicorn app.main:app --port 8000`, then `curl -N localhost:8000/api/stream/prices` should show `retry: 1000` followed by a 10-ticker map twice a second
