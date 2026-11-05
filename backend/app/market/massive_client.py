"""Massive (Polygon.io) REST poller.

Snapshot mode (paid plans): one GET /v2/snapshot/locale/us/markets/stocks/tickers call per
interval returns every tracked ticker.

EOD mode (free Basic plan): the snapshot endpoint returns 403 on Basic, so on the first
refusal the source switches to end-of-day closes from the grouped daily endpoint (one call
returns every US stock), re-fetched only when the New York date changes. Prices are real
but static during the day.
"""

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

DEFAULT_POLL_INTERVAL = 15.0  # free tier: 5 calls/min → 4/min leaves headroom
MAX_BACKOFF_INTERVAL = 60.0
EOD_MAX_LOOKBACK_DAYS = 7
EOD_RETRY_SECONDS = 60.0  # nothing loaded at all: retry soon
EOD_STALE_RETRY_SECONDS = 1800.0  # loaded, but older than the expected bar date
FIRST_POLL_TIMEOUT = 5.0  # bound on how long start() may delay app startup

_ET = ZoneInfo("America/New_York")

Mode = Literal["snapshot", "eod"]


def _et_today() -> date:
    """Current date in New York (patched in tests)."""
    return datetime.now(_ET).date()


def _extract_price(snap: Any) -> tuple[float | None, float | None]:
    """Best (price, unix_seconds) from a TickerSnapshot; (None, None) if no price.

    Fallback order: last trade → latest minute bar → today's bar → previous close.
    Units: last_trade.sip_timestamp and snap.updated are NANOseconds; min.timestamp is
    MILLIseconds. LastTrade has no `.timestamp` attribute. None timestamp means "now".
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

    prev = getattr(snap, "prev_day", None)  # overnight reset / pre-market
    if prev is not None and getattr(prev, "close", None):
        return prev.close, None

    return None, None


def _prev_close(snap: Any) -> float | None:
    prev = getattr(snap, "prev_day", None)
    close = getattr(prev, "close", None) if prev is not None else None
    return close or None


def _is_not_authorized(exc: BaseException) -> bool:
    """403: the plan lacks access. BadResponse carries only the response body."""
    text = str(exc)
    return "NOT_AUTHORIZED" in text or "not entitled" in text.lower()


# Phrases that identify a 429, as surfaced by BadResponse (body text) or urllib3's
# MaxRetryError ("too many 429 error responses"). Inferred; verify against the live API.
# A bare "429" substring is NOT used: error bodies carry random hex request_ids.
_RATE_LIMIT_MARKERS = (
    "too many requests",
    "too many 429",
    "exceeded the maximum requests",
    '"status":"too_many_requests"',
)


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _expected_eod_date(today: date) -> date:
    """Most recent weekday before `today`: the newest daily bar we can hope for.

    Market holidays are not known here; on the day after one, the source keeps
    retrying (at most every EOD_STALE_RETRY_SECONDS) until the date moves on.
    """
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive REST API."""

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        client: Any | None = None,  # injectable for tests (anything RESTClient-shaped)
        first_poll_timeout: float = FIRST_POLL_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._base_interval = poll_interval
        self._interval = poll_interval  # current; grows under 429 backoff
        self._client = client
        self._first_poll_timeout = first_poll_timeout
        self._tickers: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._started = False

        self._mode: Mode = "snapshot"
        self._eod_closes: dict[str, tuple[float, float]] = {}  # ticker → (close, ts_seconds)
        self._eod_bar_date: date | None = None  # trading day the loaded closes belong to
        self._eod_retry_at = 0.0  # time.monotonic()
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
        # Populate the cache before the app serves, but never hold up startup (and the
        # health check) for long: on timeout the loop below simply polls again.
        try:
            await asyncio.wait_for(self._poll_once(), timeout=self._first_poll_timeout)
        except TimeoutError:
            logger.warning(
                "Massive first poll exceeded %.0fs; continuing startup, prices will follow",
                self._first_poll_timeout,
            )
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._base_interval,
        )

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
            self._write_eod(ticker)  # in memory: no API call
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
        """'snapshot' (intraday prices) or 'eod' (end-of-day, free plan)."""
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
        tickers = list(self._tickers)  # copy: add/remove may run while the thread works
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except BadResponse as e:
            if not _is_not_authorized(e):
                raise
            logger.warning(
                "Massive key has no snapshot access (free plan); "
                "using end-of-day prices, which do not change intraday"
            )
            self._mode = "eod"
            await self._poll_eod()
            return

        tracked = set(self._tickers)  # skip tickers removed during the request
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
        """Keep EOD closes at the newest available trading day; price tracked tickers.

        Fetches only while the loaded bar date is older than the expected one (the most
        recent weekday before today, New York time) — so at most once per day in the
        steady state, and retried every EOD_STALE_RETRY_SECONDS when a fetch made just
        after midnight could only find an older day.
        """
        today = _et_today()
        expected = _expected_eod_date(today)
        stale = self._eod_bar_date is None or self._eod_bar_date < expected
        refreshed = False
        if stale and time.monotonic() >= self._eod_retry_at:
            bar_date, closes = await asyncio.to_thread(self._fetch_eod_closes, today)
            if closes and (self._eod_bar_date is None or bar_date > self._eod_bar_date):
                self._eod_closes, self._eod_bar_date, refreshed = closes, bar_date, True
                logger.info("Massive EOD: loaded %d closes for %s", len(closes), bar_date)
            if self._eod_bar_date is None:
                self._eod_retry_at = time.monotonic() + EOD_RETRY_SECONDS
                logger.warning(
                    "Massive EOD: no daily bars found; retrying in %.0fs", EOD_RETRY_SECONDS
                )
            elif self._eod_bar_date < expected:
                self._eod_retry_at = time.monotonic() + EOD_STALE_RETRY_SECONDS
                logger.info(
                    "Massive EOD: newest bars are %s (expected %s); retrying in %.0fs",
                    self._eod_bar_date,
                    expected,
                    EOD_STALE_RETRY_SECONDS,
                )

        for ticker in list(self._tickers):
            if ticker in self._eod_closes and (refreshed or ticker not in self._cache):
                self._write_eod(ticker)

    def _write_eod(self, ticker: str) -> None:
        close, ts = self._eod_closes[ticker]
        try:
            self._cache.update(ticker, close, ts, session_open=close)
        except ValueError as e:  # invalid upstream value: skip this ticker only
            logger.warning("Skipping EOD close for %s: %s", ticker, e)

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
        return self._client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=tickers)

    def _fetch_eod_closes(self, today: date) -> tuple[date | None, dict[str, tuple[float, float]]]:
        """(bar_date, {ticker: (close, ts_seconds)}) for the newest trading day before `today`.

        A day's bars publish after midnight ET, so start at yesterday. Weekends are
        skipped without a call; holidays and not-yet-published days cost one call each.
        Returns (None, {}) if nothing is found within EOD_MAX_LOOKBACK_DAYS.
        """
        d = today - timedelta(days=1)
        for _ in range(EOD_MAX_LOOKBACK_DAYS):
            if d.weekday() < 5:
                try:
                    bars = self._client.get_grouped_daily_aggs(date=d.isoformat(), adjusted=True)
                except BadResponse as e:
                    if not _is_not_authorized(e):
                        raise
                    bars = []  # date not available on this plan yet
                closes = {
                    b.ticker: (b.close, b.timestamp / 1e3 if b.timestamp else time.time())
                    for b in bars or []
                    if b.ticker and b.close
                }
                if closes:
                    return d, closes
            d -= timedelta(days=1)
        return None, {}
