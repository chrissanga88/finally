"""Shared fixtures for portfolio and API tests: temp DB + deterministic fake market source."""

from __future__ import annotations

import pytest

import app.db.core as db_core
from app import db, tracking
from app.market import MarketDataSource, PriceCache


class FakeMarket(MarketDataSource):
    """Deterministic source: prices a ticker from `prices` (default 100.0) when added.

    Tickers mapped to None get no price (like Massive before its first poll).
    Tests change prices with `set_price`.
    """

    def __init__(self, cache: PriceCache, prices: dict[str, float | None] | None = None):
        self.cache = cache
        self.prices: dict[str, float | None] = dict(prices or {})
        self.tickers: set[str] = set()
        self.started = False
        self.stopped = False

    async def start(self, tickers: list[str]) -> None:
        self.started = True
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        if ticker in self.tickers:
            return
        self.tickers.add(ticker)
        price = self.prices.get(ticker, 100.0)
        if price is not None:
            self.cache.update(ticker, price)

    async def remove_ticker(self, ticker: str) -> None:
        self.tickers.discard(ticker)
        self.cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return sorted(self.tickers)

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price
        self.cache.update(ticker, price)


def _clear_cache() -> None:
    cache = tracking.get_price_cache()
    for t in list(cache.get_all()):
        cache.remove(t)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh seeded DB in tmp_path; path override restored afterwards."""
    monkeypatch.setattr(db_core, "_db_path_override", None)
    path = str(tmp_path / "finally.db")
    monkeypatch.setenv("DB_PATH", path)
    db.init_db(path)
    return path


@pytest.fixture
def market(fresh_db):
    """Started FakeMarket registered as the app's market source (no FastAPI app)."""
    import asyncio

    _clear_cache()
    fake = FakeMarket(tracking.get_price_cache())
    asyncio.run(tracking.start_market(fake))
    yield fake
    tracking.set_market(None)
    _clear_cache()


@pytest.fixture
def client(fresh_db, monkeypatch):
    """TestClient running the real lifespan with a FakeMarket. Yields (client, market)."""
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MOCK", "true")
    _clear_cache()
    holder: dict[str, FakeMarket] = {}

    def factory(cache: PriceCache) -> FakeMarket:
        holder["m"] = FakeMarket(cache)
        return holder["m"]

    monkeypatch.setattr(main, "create_market_data_source", factory)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.market = holder["m"]
        yield c
    _clear_cache()
