"""Select the market data source from environment variables."""

from __future__ import annotations

import logging
import math
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import DEFAULT_POLL_INTERVAL, MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

MIN_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 300.0


def _poll_interval_from_env() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_POLL_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):  # nan/inf would make asyncio.sleep never return
        logger.warning("Invalid MASSIVE_POLL_INTERVAL=%r; using %.0fs", raw, DEFAULT_POLL_INTERVAL)
        return DEFAULT_POLL_INTERVAL
    if value > MAX_POLL_INTERVAL:
        logger.warning("MASSIVE_POLL_INTERVAL=%r too large; using %.0fs", raw, MAX_POLL_INTERVAL)
        return MAX_POLL_INTERVAL
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
