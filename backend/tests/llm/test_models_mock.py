import pytest
from pydantic import ValidationError

from app.llm.mock import mock_response
from app.llm.models import ChatResponse


def test_chat_response_parses_full_payload():
    r = ChatResponse.model_validate({
        "message": "Buying 10 AAPL.",
        "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
        "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
    })
    assert r.trades[0].side == "buy" and r.trades[0].quantity == 10.0
    assert r.watchlist_changes[0].action == "add"


def test_chat_response_defaults_empty_actions():
    r = ChatResponse.model_validate({"message": "hi"})
    assert r.trades == [] and r.watchlist_changes == []


@pytest.mark.parametrize("bad", [
    {"message": "x", "trades": [{"ticker": "AAPL", "side": "short", "quantity": 1}]},
    {"message": "x", "watchlist_changes": [{"ticker": "AAPL", "action": "delete"}]},
    {"trades": []},
])
def test_chat_response_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(bad)


def test_mock_buy():
    r = mock_response("Please BUY something")
    assert r.message == "Mock: buying 1 share of AAPL."
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("AAPL", "buy", 1.0)]
    assert r.watchlist_changes == []


def test_mock_sell():
    r = mock_response("sell it")
    assert r.message == "Mock: selling 1 share of AAPL."
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("AAPL", "sell", 1.0)]


def test_mock_watch():
    r = mock_response("Watch PYPL")
    assert r.message == "Mock: adding PYPL to your watchlist."
    assert r.trades == []
    assert [(w.ticker, w.action) for w in r.watchlist_changes] == [("PYPL", "add")]


def test_mock_first_match_wins():
    r = mock_response("buy and watch TSLA")
    assert r.message == "Mock: buying 1 share of AAPL."
    assert r.watchlist_changes == []
    # "sell" checked before "watch"
    assert mock_response("sell then watch").message == "Mock: selling 1 share of AAPL."


def test_mock_default():
    r = mock_response("how am I doing?")
    assert r.message == "Mock: I'm FinAlly, your AI trading assistant."
    assert r.trades == [] and r.watchlist_changes == []
