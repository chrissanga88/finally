"""Tests for MassiveDataSource (mocked API, real SDK model objects)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from massive.exceptions import BadResponse
from massive.rest.models.aggs import GroupedDailyAgg
from massive.rest.models.snapshot import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, _extract_price

NOT_AUTHORIZED = BadResponse(
    '{"status":"NOT_AUTHORIZED","request_id":"x",'
    '"message":"You are not entitled to this data. Please upgrade your plan."}'
)


def _make_snapshot(ticker: str, price: float, timestamp_ms: int) -> TickerSnapshot:
    """Build a real TickerSnapshot from API-shaped JSON.

    The API reports lastTrade.t in nanoseconds; the SDK exposes it as
    last_trade.sip_timestamp.
    """
    return TickerSnapshot.from_dict(
        {
            "ticker": ticker,
            "lastTrade": {"p": price, "s": 100, "x": 10, "t": timestamp_ms * 1_000_000},
            "prevDay": {"c": round(price - 1, 2)},
            "updated": timestamp_ms * 1_000_000,
        }
    )


def _make_source(tickers: list[str]) -> MassiveDataSource:
    source = MassiveDataSource(api_key="test-key", price_cache=PriceCache(), poll_interval=60.0)
    source._tickers = list(tickers)
    source._client = MagicMock()  # Satisfy the _poll_once guard
    return source


@pytest.mark.asyncio
class TestMassiveDataSource:
    """Unit tests for MassiveDataSource with mocked API."""

    async def test_poll_updates_cache(self):
        """Test that polling updates the cache."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,  # Long interval so the loop doesn't auto-poll
        )
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [
            _make_snapshot("AAPL", 190.50, 1707580800000),
            _make_snapshot("GOOGL", 175.25, 1707580800000),
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_malformed_snapshot_skipped(self):
        """Test that malformed snapshots are skipped gracefully."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        good_snap = _make_snapshot("AAPL", 190.50, 1707580800000)
        bad_snap = TickerSnapshot.from_dict({"ticker": "BAD"})  # no trade/bars at all

        with patch.object(source, "_fetch_snapshots", return_value=[good_snap, bad_snap]):
            await source._poll_once()

        # Good ticker processed, bad one skipped
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_api_error_does_not_crash(self):
        """Test that API errors don't crash the poller."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None  # No update happened

    async def test_timestamp_conversion(self):
        """Test that last-trade timestamps are converted from nanoseconds to seconds."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800000)]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.timestamp == 1707580800.0  # Converted to seconds

    async def test_add_ticker(self):
        """Test adding a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_uppercase_normalization(self):
        """Test that tickers are normalized to uppercase."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("aapl")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_strips_whitespace(self):
        """Test that ticker whitespace is stripped."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("  AAPL  ")
        assert "AAPL" in source.get_tickers()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]

        tickers = source.get_tickers()
        assert tickers == ["AAPL", "GOOGL"]

    async def test_empty_tickers_skips_poll(self):
        """Test that polling is skipped when there are no tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = []

        # Should not call _fetch_snapshots
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_stop_is_idempotent(self):
        """Test that stop() can be called multiple times."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task(self):
        """Test that stop() cancels the polling task."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=10.0)

        # Mock the client and start
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        # Verify task is running
        assert source._task is not None
        assert not source._task.done()

        # Stop and verify task is cancelled
        await source.stop()
        assert source._task is None

    async def test_start_immediate_poll(self):
        """Test that start() does an immediate poll before starting the loop."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800000)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
                await source.start(["AAPL"])

        # Cache should have data immediately from the first poll
        assert cache.get_price("AAPL") == 190.50

        await source.stop()


class TestExtractPrice:
    """Price/timestamp extraction across plan-dependent snapshot shapes."""

    def test_last_trade_preferred(self):
        snap = TickerSnapshot.from_dict(
            {
                "ticker": "AAPL",
                "lastTrade": {"p": 120.47, "t": 1605195918306274000},
                "min": {"c": 120.42, "t": 1605195900000},
                "day": {"c": 120.40},
            }
        )
        price, ts = _extract_price(snap)
        assert price == 120.47
        assert ts == pytest.approx(1605195918.306274)

    def test_minute_bar_when_no_trades_on_plan(self):
        snap = TickerSnapshot.from_dict(
            {"ticker": "AAPL", "min": {"c": 120.42, "t": 1605195900000}, "day": {"c": 120.40}}
        )
        assert _extract_price(snap) == (120.42, 1605195900.0)

    def test_day_bar_uses_updated_ns(self):
        snap = TickerSnapshot.from_dict(
            {"ticker": "AAPL", "day": {"c": 120.40}, "updated": 1605195918000000000}
        )
        price, ts = _extract_price(snap)
        assert price == 120.40
        assert ts == pytest.approx(1605195918.0)

    def test_prev_day_close_overnight(self):
        snap = TickerSnapshot.from_dict({"ticker": "AAPL", "prevDay": {"c": 119.49}})
        assert _extract_price(snap) == (119.49, None)

    def test_nothing_available(self):
        assert _extract_price(TickerSnapshot.from_dict({"ticker": "AAPL"})) == (None, None)


@pytest.mark.asyncio
class TestEodMode:
    """Free-plan fallback: snapshot 403 -> end-of-day closes."""

    async def test_snapshot_403_switches_to_eod(self):
        source = _make_source(["AAPL", "MSFT"])
        closes = {"AAPL": (190.6, 1730505600.0), "MSFT": (420.1, 1730505600.0)}

        with (
            patch.object(source, "_fetch_snapshots", side_effect=NOT_AUTHORIZED),
            patch.object(source, "_fetch_eod_closes", return_value=closes) as eod,
        ):
            await source._poll_once()

        assert source.mode == "eod"
        eod.assert_called_once()
        assert source._cache.get_price("AAPL") == 190.6
        assert source._cache.get("MSFT").timestamp == 1730505600.0

    async def test_other_bad_response_does_not_switch(self):
        source = _make_source(["AAPL"])
        with patch.object(source, "_fetch_snapshots", side_effect=BadResponse("server error")):
            await source._poll_once()  # Should not raise
        assert source.mode == "snapshot"

    async def test_eod_fetched_once_per_day(self):
        source = _make_source(["AAPL"])
        source._mode = "eod"
        closes = {"AAPL": (190.6, 1730505600.0)}

        with patch.object(source, "_fetch_eod_closes", return_value=closes) as eod:
            await source._poll_once()
            await source._poll_once()
            await source._poll_once()

        eod.assert_called_once()

    async def test_eod_failure_backs_off(self):
        source = _make_source(["AAPL"])
        source._mode = "eod"

        with patch.object(source, "_fetch_eod_closes", return_value={}) as eod:
            await source._poll_once()
            await source._poll_once()  # within retry window: no second call

        eod.assert_called_once()
        assert source._cache.get_price("AAPL") is None

    async def test_eod_add_ticker_priced_immediately(self):
        source = _make_source(["AAPL"])
        source._mode = "eod"
        closes = {"AAPL": (190.6, 1730505600.0), "PYPL": (75.2, 1730505600.0)}

        with patch.object(source, "_fetch_eod_closes", return_value=closes):
            await source._poll_once()
        await source.add_ticker("PYPL")

        assert source._cache.get_price("PYPL") == 75.2

    async def test_eod_add_unknown_ticker_has_no_price(self):
        source = _make_source(["AAPL"])
        source._mode = "eod"
        with patch.object(source, "_fetch_eod_closes", return_value={"AAPL": (190.6, 0.0)}):
            await source._poll_once()
        await source.add_ticker("ZZZZ")
        assert source._cache.get_price("ZZZZ") is None


class TestFetchEodCloses:
    """Walk back to the most recent trading day with bars."""

    @staticmethod
    def _bar(ticker: str, close: float, t_ms: int) -> GroupedDailyAgg:
        return GroupedDailyAgg.from_dict({"T": ticker, "c": close, "t": t_ms})

    def test_monday_skips_weekend_without_calls(self):
        source = _make_source(["AAPL"])
        source._client.get_grouped_daily_aggs.return_value = [
            self._bar("AAPL", 190.6, 1730318400000),
            self._bar("MSFT", 420.1, 1730318400000),
        ]

        closes = source._fetch_eod_closes(date(2026, 11, 2))  # Monday

        source._client.get_grouped_daily_aggs.assert_called_once_with(
            date="2026-10-30", adjusted=True  # Friday
        )
        assert closes["AAPL"] == (190.6, 1730318400.0)
        assert "MSFT" in closes

    def test_holiday_walks_back_further(self):
        source = _make_source(["AAPL"])
        bars = {"2026-11-26": [], "2026-11-25": [self._bar("AAPL", 191.0, 1764028800000)]}
        source._client.get_grouped_daily_aggs.side_effect = lambda date, adjusted: bars[date]

        closes = source._fetch_eod_closes(date(2026, 11, 27))  # day after Thanksgiving

        assert closes["AAPL"][0] == 191.0
        assert source._client.get_grouped_daily_aggs.call_count == 2

    def test_not_yet_published_date_is_skipped(self):
        source = _make_source(["AAPL"])

        def fake(date, adjusted):
            if date == "2026-11-03":
                raise NOT_AUTHORIZED
            return [self._bar("AAPL", 190.0, 1762128000000)]

        source._client.get_grouped_daily_aggs.side_effect = fake
        closes = source._fetch_eod_closes(date(2026, 11, 4))  # Wednesday

        assert closes["AAPL"][0] == 190.0

    def test_no_data_returns_empty(self):
        source = _make_source(["AAPL"])
        source._client.get_grouped_daily_aggs.return_value = []
        assert source._fetch_eod_closes(date(2026, 11, 4)) == {}
