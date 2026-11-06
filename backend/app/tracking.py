"""Shared market state and the tracked-ticker policy (PLAN §6).

The market data source tracks exactly ``watchlist ∪ tickers with open positions``.
Call :func:`sync_tracking` after every watchlist change and every trade.

Getters (used by app.portfolio, app.llm and routes):
    get_price_cache() -> PriceCache          # always available (module-level instance)
    get_market() -> MarketDataSource         # set in the FastAPI lifespan; RuntimeError before
"""

from __future__ import annotations

from app import db
from app.market import MarketDataSource, PriceCache

# One process-wide cache. Created at import time (no I/O) so the SSE router built
# at import time and the lifespan-created market source share the same instance.
_price_cache = PriceCache()
_market: MarketDataSource | None = None


def get_price_cache() -> PriceCache:
    return _price_cache


def get_market() -> MarketDataSource:
    if _market is None:
        raise RuntimeError("Market data source not started")
    return _market


def set_market(market: MarketDataSource | None) -> None:
    global _market
    _market = market


def is_tracked_in_db(ticker: str) -> bool:
    """True if the ticker is on the watchlist or held."""
    return db.is_on_watchlist(ticker) or db.get_position(ticker) is not None


async def sync_tracking(ticker: str) -> None:
    """Start or stop pricing `ticker` so the source matches watchlist ∪ positions."""
    market = get_market()
    wanted = is_tracked_in_db(ticker)
    tracked = ticker in market.get_tickers()
    if wanted and not tracked:
        await market.add_ticker(ticker)
    elif tracked and not wanted:
        await market.remove_ticker(ticker)


async def start_market(market: MarketDataSource) -> None:
    """Register `market` and start it with watchlist ∪ positions."""
    set_market(market)
    await market.start(sorted(db.get_tracked_tickers()))
