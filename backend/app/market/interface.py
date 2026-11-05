"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """A producer that pushes prices into a PriceCache on its own schedule.

    Downstream code never asks a source for a price; it reads the cache.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "MSFT"])   # once, in the FastAPI lifespan
        await source.add_ticker("PYPL")        # from request handlers
        await source.remove_ticker("MSFT")
        await source.stop()                    # on shutdown

    Contract:
        - The background loop never raises; it logs and continues.
        - add_ticker / remove_ticker are idempotent and fast (no network I/O).
        - remove_ticker also removes the ticker from the cache.
        - Sources know nothing about the watchlist or positions.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin tracking `tickers` and start the background task.

        Populates the cache before returning when it cheaply can.
        Calling start() twice raises RuntimeError.
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
