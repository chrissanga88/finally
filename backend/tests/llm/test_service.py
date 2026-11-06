import json

import pytest

from app import db
from app.llm import service
from app.llm.models import ChatResponse, TradeRequest, WatchlistChange


@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


async def test_execute_actions_watchlist_first_then_trades_in_order(fake_portfolio):
    resp = ChatResponse(
        message="x",
        trades=[TradeRequest(ticker="PYPL", side="buy", quantity=5),
                TradeRequest(ticker="AAPL", side="buy", quantity=1)],
        watchlist_changes=[WatchlistChange(ticker="PYPL", action="add"),
                           WatchlistChange(ticker="AAPL", action="remove")],
    )
    actions = await service.execute_actions(resp)
    assert [c[0] for c in fake_portfolio.calls] == [
        "watch_add", "watch_remove", "trade", "trade"]
    assert [c[1] for c in fake_portfolio.calls] == ["PYPL", "AAPL", "PYPL", "AAPL"]
    assert actions["watchlist_changes"] == [
        {"ticker": "PYPL", "action": "add", "status": "executed"},
        {"ticker": "AAPL", "action": "remove", "status": "executed"},
    ]
    assert actions["trades"][0] == {
        "ticker": "PYPL", "side": "buy", "quantity": 5, "status": "executed", "price": 60.0}


async def test_mixed_success_and_failure_does_not_stop(fake_portfolio):
    resp = ChatResponse(
        message="x",
        trades=[TradeRequest(ticker="TSLA", side="buy", quantity=1000),
                TradeRequest(ticker="aapl", side="buy", quantity=1),
                TradeRequest(ticker="ZZZ", side="buy", quantity=1)],
        watchlist_changes=[WatchlistChange(ticker="NOPE", action="remove"),
                           WatchlistChange(ticker="BRK1", action="add"),
                           WatchlistChange(ticker="PYPL", action="add")],
    )
    actions = await service.execute_actions(resp)
    t = actions["trades"]
    assert t[0] == {"ticker": "TSLA", "side": "buy", "quantity": 1000, "status": "failed",
                    "error": "Insufficient cash", "code": "INSUFFICIENT_CASH"}
    assert t[1]["status"] == "executed" and t[1]["ticker"] == "AAPL" and t[1]["price"] == 190.0
    assert t[2]["status"] == "failed" and t[2]["code"] == "NO_PRICE"
    w = actions["watchlist_changes"]
    assert w[0]["status"] == "failed" and w[0]["code"] == "NOT_FOUND"
    assert w[1]["status"] == "failed" and w[1]["code"] == "INVALID_TICKER"
    assert w[2] == {"ticker": "PYPL", "action": "add", "status": "executed"}


async def test_unexpected_exception_captured_as_internal_error(fake_portfolio):
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    fake_portfolio.execute_trade = boom
    actions = await service.execute_actions(
        ChatResponse(message="x", trades=[TradeRequest(ticker="AAPL", side="buy", quantity=1)]))
    assert actions["trades"][0]["status"] == "failed"
    assert actions["trades"][0]["code"] == "INTERNAL_ERROR"


async def test_handle_chat_mock_buy_persists_both_messages(temp_db, fake_portfolio, mock_mode):
    result = await service.handle_chat("buy please")
    assert result["role"] == "assistant"
    assert result["content"] == "Mock: buying 1 share of AAPL."
    assert result["actions"]["trades"][0]["status"] == "executed"
    assert set(result) == {"id", "role", "content", "created_at", "actions"}
    assert isinstance(result["created_at"], float)
    msgs = db.get_chat_messages()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "buy please"), ("assistant", "Mock: buying 1 share of AAPL.")]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"] == result["actions"]


async def test_handle_chat_mock_no_key_needed(temp_db, fake_portfolio, mock_mode):
    result = await service.handle_chat("hello")
    assert result["actions"] == {"trades": [], "watchlist_changes": []}


async def test_unavailable_without_key(temp_db, fake_portfolio, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(service.ChatError) as ei:
        await service.handle_chat("hi")
    assert ei.value.code == "LLM_UNAVAILABLE" and ei.value.status == 503
    assert db.get_chat_messages() == []


@pytest.mark.parametrize("kind", ["refusal", "max_tokens", "exception"])
async def test_llm_failure_stores_nothing(temp_db, fake_portfolio, fake_anthropic, kind):
    if kind == "exception":
        fake_anthropic(exc=RuntimeError("network"))
    else:
        fake_anthropic(parsed=ChatResponse(message="x", trades=[
            TradeRequest(ticker="AAPL", side="buy", quantity=1)]), stop_reason=kind)
    with pytest.raises(service.ChatError) as ei:
        await service.handle_chat("buy aapl")
    assert ei.value.code == "LLM_ERROR" and ei.value.status == 502
    assert db.get_chat_messages() == []
    assert fake_portfolio.calls == []


async def test_real_path_sends_context_and_history(temp_db, fake_portfolio, fake_anthropic):
    db.add_chat_message("user", "earlier", None)
    db.add_chat_message("assistant", "Buying.", {"trades": [], "watchlist_changes": []})
    fake = fake_anthropic(parsed=ChatResponse(
        message="Watching PYPL and buying 2.",
        trades=[TradeRequest(ticker="PYPL", side="buy", quantity=2)],
        watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")]))
    result = await service.handle_chat("watch and buy pypl")

    kwargs = fake.calls[0]
    assert "FinAlly" in kwargs["system"]
    assert '"cash_balance": 10000.0' in kwargs["system"]
    assert '"watchlist"' in kwargs["system"]
    assert kwargs["messages"][0] == {"role": "user", "content": "earlier"}
    assert kwargs["messages"][1]["content"].startswith("Buying.\n\n[Executed actions: ")
    assert kwargs["messages"][-1] == {"role": "user", "content": "watch and buy pypl"}

    assert result["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert result["actions"]["trades"][0]["status"] == "executed"
    stored = db.get_chat_messages()
    assert len(stored) == 4
    assert json.dumps(stored[-1]["actions"]) == json.dumps(result["actions"])
