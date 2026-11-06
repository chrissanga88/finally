"""Trade execution rules (PLAN §9) and tracked-ticker policy (PLAN §6)."""

from __future__ import annotations

import math

import pytest

from app import db
from app.portfolio import TradeError, execute_trade, get_portfolio, normalize_ticker


async def _code(coro) -> str:
    with pytest.raises(TradeError) as exc:
        await coro
    return exc.value.code


# --- ticker / quantity validation -----------------------------------------


@pytest.mark.parametrize("raw,expected", [(" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("V", "V")])
def test_normalize_ticker_ok(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "1ABC", ".A", "TOOLONGX", "AB-C", "A B", None, 5])
def test_normalize_ticker_invalid(raw):
    with pytest.raises(TradeError) as exc:
        normalize_ticker(raw)
    assert exc.value.code == "INVALID_TICKER"
    assert exc.value.status == 400


@pytest.mark.parametrize("qty", [0, -1, 0.00001, math.nan, math.inf, "10", True])
async def test_invalid_quantity(market, qty):
    assert await _code(execute_trade("AAPL", "buy", qty)) == "INVALID_QUANTITY"


async def test_invalid_side(market):
    assert await _code(execute_trade("AAPL", "short", 1)) == "VALIDATION_ERROR"


async def test_invalid_ticker(market):
    assert await _code(execute_trade("123", "buy", 1)) == "INVALID_TICKER"


# --- buys --------------------------------------------------------------------


async def test_buy_updates_cash_position_trade_and_snapshot(market):
    market.set_price("AAPL", 191.23)
    before = len(db.get_snapshot_history())
    result = await execute_trade("aapl", "buy", 10)
    assert result["cash_balance"] == 8087.70
    trade = result["trade"]
    assert trade["ticker"] == "AAPL" and trade["side"] == "buy"
    assert trade["quantity"] == 10 and trade["price"] == 191.23
    assert isinstance(trade["executed_at"], float)
    assert db.get_cash() == 8087.70
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 10 and pos["avg_cost"] == pytest.approx(191.23)
    snaps = db.get_snapshot_history()
    assert len(snaps) == before + 1
    assert snaps[-1]["total_value"] == 10000.0


async def test_buy_weighted_average_cost(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 10)
    market.set_price("AAPL", 130.0)
    await execute_trade("AAPL", "buy", 5)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 15
    assert pos["avg_cost"] == pytest.approx((10 * 100 + 5 * 130) / 15)


async def test_buy_quantity_rounded_to_4dp(market):
    market.set_price("AAPL", 100.0)
    result = await execute_trade("AAPL", "buy", 1.23456)
    assert result["trade"]["quantity"] == 1.2346
    assert result["cash_balance"] == round(10000 - round(1.2346 * 100, 2), 2)


async def test_buy_insufficient_cash(market):
    market.set_price("AAPL", 191.23)
    with pytest.raises(TradeError) as exc:
        await execute_trade("AAPL", "buy", 1000)
    assert exc.value.code == "INSUFFICIENT_CASH"
    assert "need $191,230.00, have $10,000.00" in exc.value.message
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None


async def test_buy_exact_cash_allowed(market):
    market.set_price("AAPL", 100.0)
    result = await execute_trade("AAPL", "buy", 100)
    assert result["cash_balance"] == 0.0


async def test_buy_untracked_ticker_adds_to_watchlist_and_market(market):
    assert not db.is_on_watchlist("PYPL")
    market.prices["PYPL"] = 60.0
    result = await execute_trade("pypl", "buy", 2)
    assert result["trade"]["price"] == 60.0
    assert db.is_on_watchlist("PYPL")
    assert "PYPL" in market.get_tickers()


async def test_buy_no_price_keeps_watchlist_add(market):
    market.prices["NEWCO"] = None  # e.g. Massive before first poll
    assert await _code(execute_trade("NEWCO", "buy", 1)) == "NO_PRICE"
    assert db.is_on_watchlist("NEWCO")  # no rollback
    assert "NEWCO" in market.get_tickers()
    assert db.get_cash() == 10000.0


async def test_buy_failure_after_auto_add_keeps_watchlist(market):
    market.prices["PYPL"] = 60.0
    assert await _code(execute_trade("PYPL", "buy", 100000)) == "INSUFFICIENT_CASH"
    assert db.is_on_watchlist("PYPL")


# --- sells -------------------------------------------------------------------


async def test_sell_without_position(market):
    assert await _code(execute_trade("AAPL", "sell", 1)) == "INSUFFICIENT_SHARES"


async def test_sell_untracked_ticker_does_not_add_to_watchlist(market):
    assert await _code(execute_trade("PYPL", "sell", 1)) == "INSUFFICIENT_SHARES"
    assert not db.is_on_watchlist("PYPL")


async def test_sell_more_than_owned(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 5)
    assert await _code(execute_trade("AAPL", "sell", 5.0001)) == "INSUFFICIENT_SHARES"
    assert db.get_position("AAPL")["quantity"] == 5


async def test_partial_sell_keeps_avg_cost(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 10)
    market.set_price("AAPL", 120.0)
    result = await execute_trade("AAPL", "sell", 4)
    assert result["cash_balance"] == round(9000 + 480, 2)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 6 and pos["avg_cost"] == pytest.approx(100.0)


async def test_sell_at_a_loss(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 10)
    market.set_price("AAPL", 80.0)
    result = await execute_trade("AAPL", "sell", 10)
    assert result["cash_balance"] == 9800.0
    assert db.get_position("AAPL") is None


async def test_full_sell_deletes_position(market):
    market.set_price("AAPL", 100.0)
    await execute_trade("AAPL", "buy", 1.5)
    await execute_trade("AAPL", "sell", 1.5)
    assert db.get_position("AAPL") is None
    assert get_portfolio()["positions"] == []


# --- tracked tickers ---------------------------------------------------------


async def test_closing_position_not_on_watchlist_stops_pricing(market):
    market.prices["PYPL"] = 60.0
    await execute_trade("PYPL", "buy", 1)
    db.remove_watchlist("PYPL")
    assert "PYPL" in market.get_tickers()
    await execute_trade("PYPL", "sell", 1)
    assert "PYPL" not in market.get_tickers()
    assert market.cache.get_price("PYPL") is None


async def test_closing_position_on_watchlist_keeps_pricing(market):
    await execute_trade("AAPL", "buy", 1)
    await execute_trade("AAPL", "sell", 1)
    assert "AAPL" in market.get_tickers()


async def test_conflict_is_retried(market, monkeypatch):
    calls = {"n": 0}
    real = db.apply_trade

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise db.TradeConflict("changed")
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "apply_trade", flaky)
    result = await execute_trade("AAPL", "buy", 1)
    assert calls["n"] == 2
    assert result["trade"]["ticker"] == "AAPL"


async def test_persistent_conflict_is_internal_error(market, monkeypatch):
    def always(*args, **kwargs):
        raise db.TradeConflict("changed")

    monkeypatch.setattr(db, "apply_trade", always)
    from app.errors import ApiError

    with pytest.raises(ApiError) as exc:
        await execute_trade("AAPL", "buy", 1)
    assert exc.value.code == "INTERNAL_ERROR" and exc.value.status == 500
