"""Tests for SimulatorDataSource (async lifecycle)."""

import asyncio
from collections.abc import Callable

import numpy as np
import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.simulator import TRADING_SECONDS_PER_YEAR, SimulatorDataSource


async def wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `cond` until true; fail after `timeout`. Avoids flaky fixed sleeps."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def test_is_market_data_source():
    assert isinstance(SimulatorDataSource(PriceCache()), MarketDataSource)


async def test_start_seeds_cache_before_loop_runs():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10)
    await src.start(["AAPL", "MSFT"])
    assert cache.get_price("AAPL") == 190.0
    assert cache.get_price("MSFT") == 420.0
    assert cache.get("AAPL").session_open == 190.0
    await src.stop()


async def test_start_normalizes_and_dedupes():
    src = SimulatorDataSource(PriceCache(), update_interval=10)
    await src.start([" aapl", "AAPL", "msft "])
    assert src.get_tickers() == ["AAPL", "MSFT"]
    await src.stop()


async def test_loop_updates_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL", "MSFT"])
    v = cache.version
    await wait_until(lambda: cache.version >= v + 10)  # several ticks of 2 tickers
    assert cache.get("AAPL").session_open == 190.0  # session open stays at the seed
    await src.stop()


async def test_dt_matches_update_interval():
    src = SimulatorDataSource(PriceCache(), update_interval=0.1)
    await src.start(["AAPL"])
    assert src._sim._dt == pytest.approx(0.1 / TRADING_SECONDS_PER_YEAR)
    await src.stop()


async def test_stop_is_idempotent_and_stops_writes():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL"])
    await wait_until(lambda: cache.version > 1)
    await src.stop()
    await src.stop()
    assert src._task is None
    v = cache.version
    await asyncio.sleep(0.05)  # several would-be ticks
    assert cache.version == v


async def test_stop_before_start_is_safe():
    await SimulatorDataSource(PriceCache()).stop()


async def test_double_start_raises():
    src = SimulatorDataSource(PriceCache(), update_interval=10)
    await src.start(["AAPL"])
    with pytest.raises(RuntimeError):
        await src.start(["AAPL"])
    await src.stop()


async def test_add_ticker_priced_immediately():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10)
    await src.start(["AAPL"])
    await src.add_ticker(" pypl ")
    assert "PYPL" in src.get_tickers()
    assert cache.get_price("PYPL") is not None
    await src.stop()


async def test_add_existing_ticker_does_not_rewrite_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10)
    await src.start(["AAPL"])
    v = cache.version
    await src.add_ticker("AAPL")
    assert cache.version == v
    await src.stop()


async def test_add_before_start_is_ignored_with_warning(caplog):
    cache = PriceCache()
    src = SimulatorDataSource(cache)
    with caplog.at_level("WARNING", logger="app.market.simulator"):
        await src.add_ticker("AAPL")
    assert src.get_tickers() == []
    assert "AAPL" not in cache
    assert "before start" in caplog.text


async def test_remove_ticker_clears_sim_and_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL", "MSFT"])
    await src.remove_ticker("msft")
    assert src.get_tickers() == ["AAPL"]
    assert "MSFT" not in cache
    v = cache.version
    await wait_until(lambda: cache.version >= v + 3)  # the loop keeps ticking...
    assert "MSFT" not in cache  # ...without bringing MSFT back
    await src.stop()


async def test_remove_unknown_is_safe():
    src = SimulatorDataSource(PriceCache(), update_interval=10)
    await src.start(["AAPL"])
    await src.remove_ticker("NOPE")
    assert src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_seeded_rng_reproducible():
    async def run(seed: int) -> list[dict[str, float]]:
        cache = PriceCache()
        src = SimulatorDataSource(cache, update_interval=10, rng=np.random.default_rng(seed))
        await src.start(["AAPL", "NEWCO"])
        await src.add_ticker("PYPL")
        path = [{t: u.price for t, u in cache.get_all().items()}]
        path += [src._sim.step() for _ in range(50)]
        await src.stop()
        return path

    assert await run(1) == await run(1)
    assert await run(1) != await run(2)


async def test_loop_survives_step_errors(monkeypatch):
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL"])
    calls = {"n": 0}
    real_step = src._sim.step

    def flaky_step():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        return real_step()

    monkeypatch.setattr(src._sim, "step", flaky_step)
    v = cache.version
    await wait_until(lambda: calls["n"] > 1 and cache.version > v)
    assert not src._task.done()
    await src.stop()
