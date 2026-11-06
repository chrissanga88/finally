"""Portfolio valuation, snapshots and watchlist operations."""

from __future__ import annotations

import time

import pytest

from app import db
from app.db import core
from app.portfolio import (
    TradeError,
    add_to_watchlist,
    compute_portfolio,
    execute_trade,
    get_portfolio,
    get_portfolio_history,
    get_watchlist_items,
    remove_from_watchlist,
)


def _snapshot_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
    finally:
        conn.close()


def _age_snapshots(seconds: float) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE portfolio_snapshots SET recorded_at = ?", (core.to_iso(time.time() - seconds),)
        )
    finally:
        conn.close()


# --- valuation -----------------------------------------------------------------


def test_empty_portfolio(market):
    p = compute_portfolio()
    assert p == {
        "cash_balance": 10000.0,
        "total_value": 10000.0,
        "positions_value": 0.0,
        "unrealized_pnl": 0.0,
        "positions": [],
    }


async def test_portfolio_shape_and_math(market):
    market.set_price("AAPL", 191.23)
    await execute_trade("AAPL", "buy", 10)
    market.set_price("AAPL", 192.48)
    p = compute_portfolio()
    assert p["cash_balance"] == 8087.70
    assert p["positions_value"] == 1924.80
    assert p["total_value"] == 10012.50
    assert p["unrealized_pnl"] == 12.50
    (pos,) = p["positions"]
    assert pos == {
        "ticker": "AAPL",
        "quantity": 10,
        "avg_cost": 191.23,
        "current_price": 192.48,
        "market_value": 1924.80,
        "unrealized_pnl": 12.50,
        "unrealized_pnl_percent": 0.65,
        "weight": round(1924.80 / 10012.50, 4),
    }


async def test_weights_sum_below_one(market):
    await execute_trade("AAPL", "buy", 10)
    await execute_trade("MSFT", "buy", 20)
    p = compute_portfolio()
    total_weight = sum(x["weight"] for x in p["positions"])
    assert 0 < total_weight < 1
    assert total_weight == pytest.approx(p["positions_value"] / p["total_value"], abs=1e-3)


async def test_missing_price_falls_back_to_avg_cost(market):
    market.set_price("AAPL", 150.0)
    await execute_trade("AAPL", "buy", 2)
    market.cache.remove("AAPL")
    (pos,) = compute_portfolio()["positions"]
    assert pos["current_price"] == 150.0
    assert pos["unrealized_pnl"] == 0.0


# --- snapshots -------------------------------------------------------------------


def test_get_portfolio_records_snapshot_only_when_stale(market):
    n = _snapshot_count()  # seed snapshot is fresh
    get_portfolio()
    assert _snapshot_count() == n
    _age_snapshots(26)
    get_portfolio()
    assert _snapshot_count() == n + 1
    get_portfolio()
    assert _snapshot_count() == n + 1


def test_compute_portfolio_has_no_side_effects(market):
    _age_snapshots(60)
    n = _snapshot_count()
    compute_portfolio()
    assert _snapshot_count() == n


def test_history_records_stale_snapshot_and_returns_oldest_first(market):
    _age_snapshots(60)
    hist = get_portfolio_history()["snapshots"]
    assert len(hist) == 2
    assert hist[0]["timestamp"] < hist[1]["timestamp"]
    assert set(hist[0]) == {"timestamp", "total_value"}


async def test_trade_records_snapshot_with_post_trade_value(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 10)
    market.set_price("MSFT", 50.0)
    n = _snapshot_count()
    await execute_trade("MSFT", "buy", 10)
    assert _snapshot_count() == n + 1
    assert db.get_snapshot_history()[-1]["total_value"] == 10000.0


# --- watchlist -------------------------------------------------------------------


def test_watchlist_items_have_prices(market):
    items = get_watchlist_items()
    assert [i["ticker"] for i in items][:3] == ["AAPL", "GOOGL", "MSFT"]
    assert len(items) == 10
    for i in items:
        assert i["price"] == 100.0
        assert i["session_change_percent"] == 0.0
        assert isinstance(i["added_at"], float)


async def test_watchlist_unpriced_is_null(market):
    market.prices["NEWCO"] = None
    item, created = await add_to_watchlist("newco")
    assert created
    assert item["ticker"] == "NEWCO"
    assert item["price"] is None and item["session_change_percent"] is None


async def test_add_watchlist_new_and_duplicate(market):
    item, created = await add_to_watchlist(" pypl ")
    assert created and item["ticker"] == "PYPL" and item["price"] == 100.0
    assert "PYPL" in market.get_tickers()
    _, created = await add_to_watchlist("PYPL")
    assert not created


async def test_add_watchlist_invalid(market):
    with pytest.raises(TradeError) as exc:
        await add_to_watchlist("not a ticker")
    assert exc.value.code == "INVALID_TICKER"


async def test_remove_watchlist_not_found(market):
    with pytest.raises(TradeError) as exc:
        await remove_from_watchlist("PYPL")
    assert exc.value.code == "NOT_FOUND" and exc.value.status == 404


async def test_remove_watchlist_stops_pricing_when_not_held(market):
    await remove_from_watchlist("googl")
    assert not db.is_on_watchlist("GOOGL")
    assert "GOOGL" not in market.get_tickers()
    assert market.cache.get("GOOGL") is None


async def test_remove_watchlist_keeps_pricing_held_ticker(market):
    await execute_trade("AAPL", "buy", 1)
    await remove_from_watchlist("AAPL")
    assert "AAPL" in market.get_tickers()
    assert market.cache.get_price("AAPL") == 100.0
