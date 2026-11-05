"""GBM market simulator: pure math (GBMSimulator) plus an async MarketDataSource adapter."""

from __future__ import annotations

import asyncio
import logging
import math

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
    UNKNOWN_SEED_RANGE,
)

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
DEFAULT_TICK_SECONDS = 0.5
DEFAULT_DT = DEFAULT_TICK_SECONDS / TRADING_SECONDS_PER_YEAR  # ~8.48e-8
DEFAULT_EVENT_PROBABILITY = 0.001


class GBMSimulator:
    """Correlated Geometric Brownian Motion for a dynamic set of tickers.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Z is correlated across tickers via the Cholesky factor of a sector-based
    correlation matrix. After each step a ticker has `event_probability` chance
    of a sudden 2-5% jump.

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
            z = self._cholesky @ z  # correlated shocks

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * self._sqrt_dt * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            if self._rng.random() < self._event_prob:  # rare 2-5% jump
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
        """Add without rebuilding Cholesky (for batch initialization)."""
        if ticker in self._prices:
            return
        seed = SEED_PRICES.get(ticker)
        if seed is None:  # only consume RNG for unknown tickers
            seed = float(self._rng.uniform(*UNKNOWN_SEED_RANGE))
        self._tickers.append(ticker)
        self._prices[ticker] = seed
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """O(n^2) build + O(n^3) factorization; only on add/remove, never per tick."""
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
        normalized = list(dict.fromkeys(t.upper().strip() for t in tickers))
        self._sim = GBMSimulator(
            normalized,
            dt=self._interval / TRADING_SECONDS_PER_YEAR,  # 1 tick == update_interval of trading
            event_probability=self._event_prob,
            rng=self._rng,
        )
        for ticker in self._sim.get_tickers():  # seed cache → prices available immediately
            self._write_seed(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started: %d tickers", len(normalized))

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
        if self._sim is None:
            logger.warning("Simulator: add_ticker(%s) called before start(); ignored", ticker)
            return
        if ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._write_seed(ticker)  # tradeable immediately
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
        price = self._sim.get_price(ticker) if self._sim is not None else None
        if price is not None:
            self._cache.update(ticker, price)  # first write → session_open = seed

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim is not None:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)
            except Exception:  # CancelledError is not an Exception subclass
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
