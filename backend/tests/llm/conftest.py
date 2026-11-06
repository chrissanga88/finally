"""Fixtures for LLM tests: temp DB, fake app.portfolio, fake Anthropic client."""

import sys
import types
from types import SimpleNamespace

import pytest

import app
import app.db.core as db_core
from app import db
from app.llm import client as llm_client


class FakeTradeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_core, "_db_path_override", None)
    db.init_db(str(tmp_path / "test.db"))
    yield
    # monkeypatch restores _db_path_override


@pytest.fixture
def fake_portfolio(monkeypatch):
    """Replace app.portfolio with a scriptable fake. Records calls in order."""
    mod = types.ModuleType("app.portfolio")
    mod.calls = []
    mod.prices = {"AAPL": 190.0, "PYPL": 60.0, "TSLA": 250.0}
    mod.cash = 10000.0
    mod.watchlist = ["AAPL"]
    mod.TradeError = FakeTradeError

    def compute_portfolio():
        return {
            "cash_balance": mod.cash,
            "total_value": mod.cash,
            "positions_value": 0.0,
            "unrealized_pnl": 0.0,
            "positions": [],
        }

    def get_watchlist_items():
        return [
            {"ticker": t, "price": mod.prices.get(t), "session_change_percent": 0.0,
             "added_at": 0.0}
            for t in mod.watchlist
        ]

    async def execute_trade(ticker, side, quantity):
        mod.calls.append(("trade", ticker, side, quantity))
        t = ticker.strip().upper()
        if t not in mod.prices:
            raise FakeTradeError("NO_PRICE", f"No price for {t}")
        cost = round(quantity * mod.prices[t], 2)
        if side == "buy" and cost > mod.cash:
            raise FakeTradeError("INSUFFICIENT_CASH", "Insufficient cash")
        mod.cash = round(mod.cash - cost if side == "buy" else mod.cash + cost, 2)
        return {
            "trade": {"id": "x", "ticker": t, "side": side, "quantity": quantity,
                      "price": mod.prices[t], "executed_at": 0.0},
            "cash_balance": mod.cash,
        }

    async def add_to_watchlist(ticker):
        mod.calls.append(("watch_add", ticker))
        t = ticker.strip().upper()
        if not t.isalpha():
            raise FakeTradeError("INVALID_TICKER", f"Invalid ticker: {ticker}")
        created = t not in mod.watchlist
        if created:
            mod.watchlist.append(t)
        return {"ticker": t, "price": None, "session_change_percent": None,
                "added_at": 0.0}, created

    async def remove_from_watchlist(ticker):
        mod.calls.append(("watch_remove", ticker))
        t = ticker.strip().upper()
        if t not in mod.watchlist:
            raise FakeTradeError("NOT_FOUND", f"{t} is not on the watchlist", 404)
        mod.watchlist.remove(t)

    mod.compute_portfolio = compute_portfolio
    mod.get_watchlist_items = get_watchlist_items
    mod.execute_trade = execute_trade
    mod.add_to_watchlist = add_to_watchlist
    mod.remove_from_watchlist = remove_from_watchlist

    monkeypatch.setitem(sys.modules, "app.portfolio", mod)
    monkeypatch.setattr(app, "portfolio", mod, raising=False)
    return mod


class FakeMessages:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Install a fake AsyncAnthropic. Call with (parsed_output, stop_reason) or exc=..."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MOCK", "false")

    def install(parsed=None, stop_reason="end_turn", exc=None):
        messages = FakeMessages(
            result=SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason), exc=exc
        )
        llm_client.set_client(SimpleNamespace(messages=messages))
        return messages

    yield install
    llm_client.set_client(None)
