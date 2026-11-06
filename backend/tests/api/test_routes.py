"""HTTP routes: status codes, response shapes and the error shape (PLAN §8)."""

from __future__ import annotations

import pytest


def _assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error", "code"}
    assert body["code"] == code
    assert isinstance(body["error"], str) and body["error"]


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_lifespan_starts_market_with_watchlist_and_positions(client):
    m = client.market
    assert m.started
    assert set(m.get_tickers()) == {
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "NVDA",
        "META",
        "JPM",
        "V",
        "NFLX",
    }


def test_portfolio_initial(client):
    body = client.get("/api/portfolio").json()
    assert body["cash_balance"] == 10000.0
    assert body["total_value"] == 10000.0
    assert body["positions"] == []


def test_trade_buy_then_portfolio(client):
    client.market.set_price("AAPL", 191.23)
    r = client.post("/api/portfolio/trade", json={"ticker": "aapl", "side": "buy", "quantity": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["cash_balance"] == 8087.70
    assert set(body["trade"]) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert body["trade"]["ticker"] == "AAPL"

    p = client.get("/api/portfolio").json()
    assert p["positions"][0]["ticker"] == "AAPL"
    assert set(p["positions"][0]) == {
        "ticker",
        "quantity",
        "avg_cost",
        "current_price",
        "market_value",
        "unrealized_pnl",
        "unrealized_pnl_percent",
        "weight",
    }


def test_trade_sell_closes_position(client):
    client.post("/api/portfolio/trade", json={"ticker": "MSFT", "side": "buy", "quantity": 2})
    r = client.post("/api/portfolio/trade", json={"ticker": "MSFT", "side": "sell", "quantity": 2})
    assert r.status_code == 200
    assert r.json()["cash_balance"] == 10000.0
    assert client.get("/api/portfolio").json()["positions"] == []


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"ticker": "AAPL", "quantity": 1}, "VALIDATION_ERROR"),  # missing side
        ({"ticker": "AAPL", "side": "short", "quantity": 1}, "VALIDATION_ERROR"),
        ({"ticker": "AAPL", "side": "buy", "quantity": "ten"}, "VALIDATION_ERROR"),
        ({"ticker": "AAPL", "side": "buy", "quantity": "10"}, "VALIDATION_ERROR"),
        ({"ticker": "AAPL", "side": "buy"}, "VALIDATION_ERROR"),
        ({"ticker": "AAPL", "side": "buy", "quantity": 0}, "INVALID_QUANTITY"),
        ({"ticker": "AAPL", "side": "buy", "quantity": -3}, "INVALID_QUANTITY"),
        ({"ticker": "AAPL", "side": "buy", "quantity": 0.00001}, "INVALID_QUANTITY"),
        ({"ticker": "12AB", "side": "buy", "quantity": 1}, "INVALID_TICKER"),
        ({"ticker": "AAPL", "side": "buy", "quantity": 1_000_000}, "INSUFFICIENT_CASH"),
        ({"ticker": "AAPL", "side": "sell", "quantity": 1}, "INSUFFICIENT_SHARES"),
    ],
)
def test_trade_errors(client, payload, code):
    _assert_error(client.post("/api/portfolio/trade", json=payload), 400, code)


def test_trade_malformed_json(client):
    r = client.post(
        "/api/portfolio/trade", content=b"{not json", headers={"content-type": "application/json"}
    )
    _assert_error(r, 400, "VALIDATION_ERROR")


def test_trade_no_price(client):
    client.market.prices["NEWCO"] = None
    r = client.post("/api/portfolio/trade", json={"ticker": "NEWCO", "side": "buy", "quantity": 1})
    _assert_error(r, 400, "NO_PRICE")
    tickers = [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]
    assert "NEWCO" in tickers


def test_history(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 1})
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snaps) >= 2
    assert all(set(s) == {"timestamp", "total_value"} for s in snaps)
    assert snaps == sorted(snaps, key=lambda s: s["timestamp"])


def test_watchlist_get(client):
    tickers = client.get("/api/watchlist").json()["tickers"]
    assert len(tickers) == 10
    assert set(tickers[0]) == {"ticker", "price", "session_change_percent", "added_at"}
    assert tickers[0]["price"] == 100.0


def test_watchlist_add_201_then_200(client):
    r = client.post("/api/watchlist", json={"ticker": "pypl"})
    assert r.status_code == 201
    assert r.json()["ticker"] == "PYPL"
    assert r.json()["price"] == 100.0
    r = client.post("/api/watchlist", json={"ticker": "PYPL"})
    assert r.status_code == 200
    assert "PYPL" in client.market.get_tickers()


def test_watchlist_add_errors(client):
    r = client.post("/api/watchlist", json={"ticker": "bad ticker"})
    _assert_error(r, 400, "INVALID_TICKER")
    _assert_error(client.post("/api/watchlist", json={}), 400, "VALIDATION_ERROR")


def test_watchlist_delete(client):
    r = client.delete("/api/watchlist/googl")
    assert r.status_code == 204
    assert r.content == b""
    assert "GOOGL" not in client.market.get_tickers()
    _assert_error(client.delete("/api/watchlist/GOOGL"), 404, "NOT_FOUND")


def test_watchlist_delete_held_keeps_pricing(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 1})
    assert client.delete("/api/watchlist/AAPL").status_code == 204
    assert "AAPL" in client.market.get_tickers()
    tickers = [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]
    assert "AAPL" not in tickers


def test_unknown_api_route_is_404_error_shape(client):
    _assert_error(client.get("/api/does-not-exist"), 404, "NOT_FOUND")


def test_unexpected_exception_is_500_error_shape(client, monkeypatch):
    from app import portfolio

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(portfolio, "get_portfolio", boom)
    _assert_error(client.get("/api/portfolio"), 500, "INTERNAL_ERROR")


def test_chat_mock_buy_executes_trade(client):
    r = client.post("/api/chat", json={"message": "please buy"})
    assert r.status_code == 200
    trades = r.json()["actions"]["trades"]
    assert trades[0]["status"] == "executed"
    assert client.get("/api/portfolio").json()["positions"][0]["ticker"] == "AAPL"


def test_sse_route_registered(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/stream/prices", "/api/portfolio", "/api/chat", "/api/chat/history"):
        assert path in paths
