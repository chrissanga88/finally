"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# EOD mode: how many calendar days to walk back looking for the last trading day,
# and how long to wait before retrying after a failed EOD fetch.
_EOD_MAX_LOOKBACK_DAYS = 7
_EOD_RETRY_SECONDS = 60.0


def _extract_price(snap: Any) -> tuple[float | None, float | None]:
    """Best available (price, unix_seconds_timestamp) from a TickerSnapshot.

    Fallback order: last trade → latest minute bar → today's bar → previous day's
    close. Plans without trade data have no `last_trade`; `min`/`day` are empty
    before the open and during the overnight reset. A timestamp of None means
    "use now".

    Units: last_trade.sip_timestamp and snapshot.updated are nanoseconds;
    minute-bar timestamps are milliseconds.
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

    prev = getattr(snap, "prev_day", None)
    if prev is not None and getattr(prev, "close", None):
        return prev.close, None

    return None, None


def _is_not_authorized(exc: BadResponse) -> bool:
    """True if the API refused the request because the plan lacks access (HTTP 403).

    BadResponse carries only the response body, e.g.
    {"status":"NOT_AUTHORIZED","message":"You are not entitled to this data..."}.
    """
    text = str(exc)
    return "NOT_AUTHORIZED" in text or "not entitled" in text.lower()


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Snapshot mode (paid plans): polls GET /v2/snapshot/locale/us/markets/stocks/tickers
    for all tracked tickers in one call per interval.

    EOD mode (free Basic plan): the snapshot endpoint returns 403 on Basic, so on the
    first refusal the source switches to end-of-day prices from the grouped daily
    endpoint (one call returns every US stock), re-fetched only when the ET date
    changes. Prices are real but static during the day.

    Rate limits:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: unlimited → poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None

        # "snapshot" until the API refuses snapshots, then "eod"
        self._mode: str = "snapshot"
        # EOD mode state
        self._eod_closes: dict[str, tuple[float, float]] = {}  # ticker -> (close, ts seconds)
        self._eod_fetched_for: date | None = None  # ET date the closes were fetched on
        self._eod_retry_at: float = 0.0  # monotonic time before which not to retry

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            if self._mode == "eod" and ticker in self._eod_closes:
                # Already have the close in memory — price it now, no API call
                close, ts = self._eod_closes[ticker]
                self._cache.update(ticker=ticker, price=close, timestamp=ts)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    @property
    def mode(self) -> str:
        """'snapshot' (intraday prices) or 'eod' (end-of-day, free plan)."""
        return self._mode

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle. Never raises."""
        if not self._tickers or not self._client:
            return
        try:
            if self._mode == "eod":
                await self._poll_eod()
            else:
                await self._poll_snapshot()
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            # Don't re-raise — the loop will retry on the next interval.
            # Common failures: 401 (bad key), 429 (rate limit), network errors.

    async def _poll_snapshot(self) -> None:
        tickers = list(self._tickers)  # copy: add/remove may run while the thread works
        try:
            # The Massive RESTClient is synchronous — run in a thread to
            # avoid blocking the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except BadResponse as e:
            if not _is_not_authorized(e):
                raise
            logger.warning(
                "Massive API key has no snapshot access (free plan); "
                "falling back to end-of-day prices, which do not change intraday"
            )
            self._mode = "eod"
            await self._poll_eod()
            return

        processed = 0
        for snap in snapshots:
            try:
                price, timestamp = _extract_price(snap)
                if price is None:
                    continue
                self._cache.update(ticker=snap.ticker, price=price, timestamp=timestamp)
                processed += 1
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning(
                    "Skipping snapshot for %s: %s",
                    getattr(snap, "ticker", "???"),
                    e,
                )
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(tickers))

    async def _poll_eod(self) -> None:
        """Ensure EOD closes are current for today's ET date, then price any tracked
        ticker not yet in the cache. Fetches at most once per ET day on success."""
        today = datetime.now(_ET).date()
        refreshed = False
        if self._eod_fetched_for != today and time.monotonic() >= self._eod_retry_at:
            closes = await asyncio.to_thread(self._fetch_eod_closes, today)
            if closes:
                self._eod_closes = closes
                self._eod_fetched_for = today
                refreshed = True
                logger.info("Massive EOD: loaded closes for %d tickers", len(closes))
            else:
                self._eod_retry_at = time.monotonic() + _EOD_RETRY_SECONDS
                logger.warning("Massive EOD: no daily bars found; retrying later")

        for ticker in list(self._tickers):
            if ticker in self._eod_closes and (refreshed or ticker not in self._cache):
                close, ts = self._eod_closes[ticker]
                self._cache.update(ticker=ticker, price=close, timestamp=ts)

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )

    def _fetch_eod_closes(self, today: date) -> dict[str, tuple[float, float]]:
        """Synchronous. Return {ticker: (close, ts_seconds)} for the most recent
        trading day before `today` (ET). One grouped-daily call per day tried;
        weekends are skipped without a call. A day's bars are published after
        midnight ET, so the walk starts at yesterday.
        """
        d = today - timedelta(days=1)
        for _ in range(_EOD_MAX_LOOKBACK_DAYS):
            if d.weekday() < 5:  # Mon-Fri
                try:
                    bars = self._client.get_grouped_daily_aggs(date=d.isoformat(), adjusted=True)
                except BadResponse as e:
                    if not _is_not_authorized(e):
                        raise
                    bars = []  # date not available on this plan yet
                if bars:
                    return {
                        b.ticker: (b.close, b.timestamp / 1e3 if b.timestamp else time.time())
                        for b in bars
                        if b.ticker and b.close
                    }
            d -= timedelta(days=1)
        return {}
