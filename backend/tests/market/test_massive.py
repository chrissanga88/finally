"""Tests for MassiveDataSource.

Snapshots and bars are real SDK model objects built with `from_dict` from API-shaped
JSON, and the client is a minimal fake exposing only the methods the source should
call — so a misspelled SDK attribute (e.g. `last_trade.timestamp`) fails loudly,
which a MagicMock would hide.
"""

import asyncio
import time
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from massive.exceptions import BadResponse
from massive.rest.models.aggs import GroupedDailyAgg
from massive.rest.models.snapshot import TickerSnapshot

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import (
    EOD_RETRY_SECONDS,
    EOD_STALE_RETRY_SECONDS,
    MAX_BACKOFF_INTERVAL,
    MassiveDataSource,
    _et_today,
    _expected_eod_date,
    _extract_price,
    _is_not_authorized,
    _is_rate_limited,
)

NOT_AUTHORIZED = BadResponse(
    '{"status":"NOT_AUTHORIZED","request_id":"x",'
    '"message":"You are not entitled to this data. Please upgrade your plan."}'
)
T_MS = 1_730_563_200_000  # 2024-11-02 16:00 UTC in milliseconds
MONDAY = date(2026, 11, 2)


def snap(ticker: str, price: float, prev_close: float | None = None) -> TickerSnapshot:
    d: dict = {
        "ticker": ticker,
        "lastTrade": {"p": price, "s": 100, "x": 10, "t": T_MS * 1_000_000},  # ns
        "updated": T_MS * 1_000_000,
    }
    if prev_close is not None:
        d["prevDay"] = {"c": prev_close}
    return TickerSnapshot.from_dict(d)


def bar(ticker: str, close: float, t_ms: int = T_MS) -> GroupedDailyAgg:
    return GroupedDailyAgg.from_dict({"T": ticker, "c": close, "t": t_ms})


class FakeClient:
    def __init__(self, snapshots=(), snapshot_error=None, grouped=None, grouped_error=None):
        self.snapshots = list(snapshots)
        self.snapshot_error = snapshot_error
        self.grouped = grouped or {}
        self.grouped_error = grouped_error
        self.snapshot_calls: list[list[str]] = []
        self.grouped_calls: list[str] = []

    def get_snapshot_all(self, market_type, tickers):
        self.snapshot_calls.append(list(tickers))
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshots

    def get_grouped_daily_aggs(self, date, adjusted=True):
        self.grouped_calls.append(date)
        if self.grouped_error:
            raise self.grouped_error
        return self.grouped.get(date, [])


async def started(client, tickers=("AAPL",), poll_interval=3600.0):
    cache = PriceCache()
    src = MassiveDataSource("key", cache, poll_interval=poll_interval, client=client)
    await src.start(list(tickers))
    return src, cache


def eod_client(**grouped) -> FakeClient:
    return FakeClient(snapshot_error=NOT_AUTHORIZED, grouped=grouped)


# --- helpers ----------------------------------------------------------------


class TestExtractPrice:
    def test_last_trade_nanoseconds(self):
        assert _extract_price(snap("A", 10.0)) == (10.0, T_MS / 1e3)

    def test_minute_bar_milliseconds(self):
        s = TickerSnapshot.from_dict({"ticker": "A", "min": {"c": 11.0, "t": T_MS}})
        assert _extract_price(s) == (11.0, T_MS / 1e3)

    def test_day_bar_uses_updated(self):
        s = TickerSnapshot.from_dict(
            {"ticker": "A", "day": {"c": 12.0}, "updated": T_MS * 1_000_000}
        )
        assert _extract_price(s) == (12.0, T_MS / 1e3)

    def test_prev_day_fallback_has_no_timestamp(self):
        s = TickerSnapshot.from_dict({"ticker": "A", "prevDay": {"c": 9.0}})
        assert _extract_price(s) == (9.0, None)

    def test_priority_last_trade_over_bars(self):
        s = TickerSnapshot.from_dict(
            {
                "ticker": "A",
                "lastTrade": {"p": 10.0, "t": T_MS * 1_000_000},
                "min": {"c": 11.0, "t": T_MS},
                "day": {"c": 12.0},
                "prevDay": {"c": 9.0},
            }
        )
        assert _extract_price(s)[0] == 10.0

    def test_zero_prices_skipped(self):
        s = TickerSnapshot.from_dict(
            {"ticker": "A", "lastTrade": {"p": 0}, "min": {"c": 0}, "day": {"c": 11.5}}
        )
        assert _extract_price(s)[0] == 11.5

    def test_missing_timestamp_is_none(self):
        s = TickerSnapshot.from_dict({"ticker": "A", "lastTrade": {"p": 10.0}})
        assert _extract_price(s) == (10.0, None)

    def test_nothing_available(self):
        assert _extract_price(TickerSnapshot.from_dict({"ticker": "A"})) == (None, None)


def test_error_classifiers():
    assert _is_not_authorized(NOT_AUTHORIZED)
    assert not _is_not_authorized(BadResponse('{"status":"ERROR"}'))
    assert _is_rate_limited(BadResponse("429 Too Many Requests"))
    assert _is_rate_limited(Exception("Max retries exceeded (too many 429 error responses)"))
    assert _is_rate_limited(BadResponse("You've exceeded the maximum requests per minute"))
    assert not _is_rate_limited(NOT_AUTHORIZED)


def test_request_id_containing_429_is_not_rate_limit():
    err = BadResponse('{"status":"ERROR","request_id":"8f2a429c","message":"Internal error"}')
    assert not _is_rate_limited(err)


def test_expected_eod_date_skips_weekends():
    assert _expected_eod_date(date(2026, 11, 3)) == date(2026, 11, 2)  # Tue → Mon
    assert _expected_eod_date(MONDAY) == date(2026, 10, 30)  # Mon → Fri
    assert _expected_eod_date(date(2026, 11, 1)) == date(2026, 10, 30)  # Sun → Fri
    assert _expected_eod_date(date(2026, 10, 31)) == date(2026, 10, 30)  # Sat → Fri


def test_et_today_is_new_york_date():
    assert _et_today() == datetime.now(ZoneInfo("America/New_York")).date()


# --- lifecycle ----------------------------------------------------------------


def test_is_market_data_source():
    assert isinstance(MassiveDataSource("k", PriceCache()), MarketDataSource)


async def test_start_polls_immediately():
    client = FakeClient([snap("AAPL", 190.5), snap("MSFT", 420.25)])
    src, cache = await started(client, ["aapl", "MSFT", "AAPL"])
    assert client.snapshot_calls == [["AAPL", "MSFT"]]  # normalized + deduped
    assert cache.get_price("AAPL") == 190.5
    assert cache.get("AAPL").timestamp == T_MS / 1e3
    await src.stop()


async def test_start_creates_real_client_with_explicit_key():
    src = MassiveDataSource("secret", PriceCache())
    with patch("app.market.massive_client.RESTClient") as rest:
        await src.start([])
    rest.assert_called_once()
    assert rest.call_args.kwargs["api_key"] == "secret"
    await src.stop()


async def test_double_start_raises():
    src, _ = await started(FakeClient())
    with pytest.raises(RuntimeError):
        await src.start(["AAPL"])
    await src.stop()


async def test_stop_idempotent_and_before_start():
    await MassiveDataSource("k", PriceCache()).stop()
    src, _ = await started(FakeClient())
    await src.stop()
    await src.stop()


async def test_poll_loop_runs_on_interval():
    client = FakeClient([snap("AAPL", 1.0)])
    src, _ = await started(client, poll_interval=0.01)
    deadline = asyncio.get_running_loop().time() + 2.0
    while len(client.snapshot_calls) <= 2:
        assert asyncio.get_running_loop().time() < deadline, "poll loop did not run"
        await asyncio.sleep(0.005)
    await src.stop()


class SlowClient(FakeClient):
    def get_snapshot_all(self, market_type, tickers):
        time.sleep(0.5)  # runs in the worker thread
        return super().get_snapshot_all(market_type, tickers)


async def test_slow_first_poll_does_not_block_startup(caplog):
    client = SlowClient([snap("AAPL", 190.5)])
    src = MassiveDataSource(
        "k", PriceCache(), poll_interval=3600, client=client, first_poll_timeout=0.05
    )
    t0 = time.monotonic()
    with caplog.at_level("WARNING", logger="app.market.massive_client"):
        await src.start(["AAPL"])
    assert time.monotonic() - t0 < 0.4
    assert "continuing startup" in caplog.text
    assert src._task is not None and not src._task.done()  # poll loop still running
    await src.stop()


async def test_no_poll_without_tickers():
    client = FakeClient()
    src, _ = await started(client, tickers=())
    assert client.snapshot_calls == []
    await src.stop()


# --- snapshot mode ------------------------------------------------------------


async def test_prev_close_is_session_open():
    src, cache = await started(FakeClient([snap("AAPL", 191.9, prev_close=190.0)]))
    u = cache.get("AAPL")
    assert (u.price, u.session_open, u.session_change_percent) == (191.9, 190.0, 1.0)
    await src.stop()


async def test_without_prev_close_first_price_is_session_open():
    src, cache = await started(FakeClient([snap("AAPL", 191.9)]))
    assert cache.get("AAPL").session_open == 191.9
    await src.stop()


async def test_snapshot_without_price_skipped():
    client = FakeClient([snap("AAPL", 190.5), TickerSnapshot.from_dict({"ticker": "BAD"})])
    src, cache = await started(client, ["AAPL", "BAD"])
    assert cache.get_price("AAPL") == 190.5
    assert "BAD" not in cache
    await src.stop()


async def test_malformed_snapshot_skipped():
    class Broken:
        ticker = "BAD"

        @property
        def last_trade(self):
            raise TypeError("corrupt")

    client = FakeClient([Broken(), snap("AAPL", 190.5)])
    src, cache = await started(client, ["AAPL", "BAD"])
    assert cache.get_price("AAPL") == 190.5
    await src.stop()


async def test_untracked_or_removed_ticker_not_written():
    client = FakeClient([snap("AAPL", 1.0), snap("MSFT", 2.0), snap("EXTRA", 3.0)])
    src, cache = await started(client, ["AAPL", "MSFT"])
    assert "EXTRA" not in cache
    await src.remove_ticker("MSFT")
    await src._poll_once()
    assert "MSFT" not in cache
    await src.stop()


async def test_add_ticker_included_in_next_poll():
    client = FakeClient([snap("AAPL", 1.0), snap("PYPL", 60.0)])
    src, cache = await started(client)
    await src.add_ticker("pypl")
    await src.add_ticker("PYPL")
    assert src.get_tickers() == ["AAPL", "PYPL"]
    assert "PYPL" not in cache  # snapshot mode: priced on the next poll
    await src._poll_once()
    assert client.snapshot_calls[-1] == ["AAPL", "PYPL"]
    assert cache.get_price("PYPL") == 60.0
    await src.stop()


async def test_remove_ticker_clears_cache():
    src, cache = await started(FakeClient([snap("AAPL", 1.0)]))
    await src.remove_ticker("aapl")
    assert src.get_tickers() == []
    assert "AAPL" not in cache
    await src.stop()


# --- errors -------------------------------------------------------------------


async def test_generic_error_never_escapes():
    client = FakeClient(snapshot_error=ConnectionError("network down"))
    src, cache = await started(client)  # start() does not raise
    await src._poll_once()
    assert len(cache) == 0
    assert src.mode == "snapshot"
    await src.stop()


async def test_non_403_bad_response_does_not_switch_mode():
    client = FakeClient(snapshot_error=BadResponse('{"status":"ERROR","message":"oops"}'))
    src, _ = await started(client)
    assert src.mode == "snapshot"
    await src.stop()


async def test_repeated_errors_logged_once_at_error(caplog):
    client = FakeClient(snapshot_error=ConnectionError("network down"))
    with caplog.at_level("DEBUG", logger="app.market.massive_client"):
        src, _ = await started(client)
        await src._poll_once()
        await src._poll_once()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    await src.stop()


async def test_rate_limit_backs_off_and_resets():
    client = FakeClient(snapshot_error=BadResponse("429 Too Many Requests"))
    src, _ = await started(client, poll_interval=20)
    assert src._interval == 40
    await src._poll_once()
    assert src._interval == MAX_BACKOFF_INTERVAL  # 80 capped to 60
    await src._poll_once()
    assert src._interval == MAX_BACKOFF_INTERVAL
    client.snapshot_error, client.snapshots = None, [snap("AAPL", 1.0)]
    await src._poll_once()
    assert src._interval == 20
    await src.stop()


# --- EOD mode -----------------------------------------------------------------


async def test_403_switches_to_eod_and_walks_back_over_weekend():
    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6), bar("MSFT", 410.0)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
    assert src.mode == "eod"
    assert client.grouped_calls == ["2026-10-30"]  # Sun/Sat skipped without a call
    u = cache.get("AAPL")
    assert (u.price, u.session_open, u.session_change_percent) == (190.6, 190.6, 0.0)
    assert u.timestamp == T_MS / 1e3
    assert "MSFT" not in cache  # not tracked
    await src.stop()


async def test_eod_walks_back_over_holiday():
    client = eod_client(**{"2026-10-29": [bar("AAPL", 189.0)]})  # Fri 10-30 empty
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
    assert client.grouped_calls == ["2026-10-30", "2026-10-29"]
    assert cache.get_price("AAPL") == 189.0
    await src.stop()


async def test_eod_date_403_treated_as_unavailable():
    client = FakeClient(snapshot_error=NOT_AUTHORIZED, grouped_error=NOT_AUTHORIZED)
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
    assert src.mode == "eod"
    assert len(client.grouped_calls) == 5  # 7-day lookback, weekdays only
    assert len(cache) == 0
    await src.stop()


async def test_eod_fetches_once_per_day():
    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
        v = cache.version
        await src._poll_once()
        await src._poll_once()
    assert client.grouped_calls == ["2026-10-30"]
    assert len(client.snapshot_calls) == 1  # snapshot never retried after 403
    assert cache.version == v  # no rewrites while nothing changed
    await src.stop()


async def test_eod_refetches_when_date_changes():
    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6)], "2026-11-02": [bar("AAPL", 192.0)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
    with patch("app.market.massive_client._et_today", return_value=date(2026, 11, 3)):
        await src._poll_once()
    assert client.grouped_calls == ["2026-10-30", "2026-11-02"]
    assert cache.get_price("AAPL") == 192.0
    await src.stop()


async def test_eod_empty_result_waits_before_retry():
    client = eod_client()
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, _ = await started(client)
        calls = len(client.grouped_calls)
        await src._poll_once()
        assert len(client.grouped_calls) == calls  # within retry window: no calls
        src._eod_retry_at -= EOD_RETRY_SECONDS + 1
        await src._poll_once()
        assert len(client.grouped_calls) == 2 * calls
    await src.stop()


async def test_eod_add_ticker_priced_from_memory():
    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6), bar("PYPL", 61.25)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
        calls = len(client.grouped_calls)
        await src.add_ticker("PYPL")
    assert cache.get_price("PYPL") == 61.25
    assert len(client.grouped_calls) == calls  # no API call
    await src.add_ticker("NOPE")  # unknown → no price
    assert "NOPE" not in cache
    await src.stop()


async def test_eod_reprices_ticker_missing_from_cache():
    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client)
        cache.remove("AAPL")  # e.g. removed and re-added between polls
        await src._poll_once()
    assert cache.get_price("AAPL") == 190.6
    await src.stop()


# --- review regressions (MARKET_DATA_REVIEW.md) -------------------------------


async def test_eod_refetches_when_bars_publish_after_midnight():
    """M1: a fetch just after midnight finds only an older day; the newer bars must be
    picked up once published instead of serving stale closes until the next date."""
    client = eod_client(**{"2026-10-29": [bar("AAPL", 189.0)]})  # Fri 10-30 not out yet
    saturday = date(2026, 10, 31)
    with patch("app.market.massive_client._et_today", return_value=saturday):
        src, cache = await started(client)
        assert cache.get_price("AAPL") == 189.0
        calls = len(client.grouped_calls)

        await src._poll_once()  # within the stale-retry window: no extra calls
        assert len(client.grouped_calls) == calls

        client.grouped["2026-10-30"] = [bar("AAPL", 190.6)]  # Friday's bars publish
        src._eod_retry_at -= EOD_STALE_RETRY_SECONDS + 1
        await src._poll_once()
    assert cache.get_price("AAPL") == 190.6
    assert src._eod_bar_date == date(2026, 10, 30)

    with patch("app.market.massive_client._et_today", return_value=saturday):
        calls = len(client.grouped_calls)
        src._eod_retry_at = 0.0
        await src._poll_once()  # now current: no further fetches
    assert len(client.grouped_calls) == calls
    await src.stop()


async def test_eod_stale_retry_does_not_rewrite_unchanged_closes():
    client = eod_client(**{"2026-10-29": [bar("AAPL", 189.0)]})
    with patch("app.market.massive_client._et_today", return_value=date(2026, 10, 31)):
        src, cache = await started(client)
        v = cache.version
        src._eod_retry_at = 0.0
        await src._poll_once()  # refetch finds the same (older) day again
    assert cache.version == v
    await src.stop()


async def test_eod_non_403_error_during_walk_is_logged_and_retried(caplog):
    """T4: a 5xx while walking back must not load anything or escape the loop."""
    client = FakeClient(
        snapshot_error=NOT_AUTHORIZED,
        grouped_error=BadResponse('{"status":"ERROR","message":"Internal server error"}'),
    )
    with (
        patch("app.market.massive_client._et_today", return_value=MONDAY),
        caplog.at_level("ERROR", logger="app.market.massive_client"),
    ):
        src, cache = await started(client)
        assert src.mode == "eod"
        assert len(cache) == 0
        assert "Internal server error" in caplog.text
        client.grouped_error = None
        client.grouped["2026-10-30"] = [bar("AAPL", 190.6)]
        await src._poll_once()  # no retry window was set: next poll tries again
    assert cache.get_price("AAPL") == 190.6
    await src.stop()


async def test_invalid_upstream_prices_skipped():
    """L3: the cache rejects non-positive/NaN prices; sources skip just that ticker."""
    client = FakeClient([snap("AAPL", 190.5), snap("BAD", float("nan"))])
    src, cache = await started(client, ["AAPL", "BAD"])
    assert cache.get_price("AAPL") == 190.5
    assert "BAD" not in cache
    await src.stop()

    client = eod_client(**{"2026-10-30": [bar("AAPL", 190.6), bar("NEG", -3.0)]})
    with patch("app.market.massive_client._et_today", return_value=MONDAY):
        src, cache = await started(client, ["AAPL", "NEG"])
    assert cache.get_price("AAPL") == 190.6
    assert "NEG" not in cache
    await src.stop()
