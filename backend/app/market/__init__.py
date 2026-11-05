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
