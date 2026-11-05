"""Thread-safe in-memory price cache — the single source of truth for live prices."""

from __future__ import annotations

import math
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
        self._version = 0  # bumped on every write and every effective remove

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
        - timestamp defaults to now; 0.0 is a valid timestamp.
        - session_open, if given and > 0, overrides the stored reference (e.g. Massive's
          previous close). Otherwise the first price seen is kept until the ticker is removed.

        Raises ValueError for a non-finite or non-positive price: such a value would
        reach trade fills, and NaN would make every SSE payload invalid JSON.
        """
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"invalid price for {ticker}: {price!r}")
        ts = timestamp if timestamp is not None else time.time()
        price = round(price, 2)
        with self._lock:
            prev = self._prices.get(ticker)
            if session_open is not None and math.isfinite(session_open) and session_open > 0:
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
        """Forget a ticker (price and session open).

        Bumps version so the SSE stream re-emits promptly without it.
        No-op (and no version bump) if the ticker is absent.
        """
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
