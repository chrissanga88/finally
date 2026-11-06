import pytest

from app import db

ACTIONS = {
    "trades": [
        {"ticker": "AAPL", "side": "buy", "quantity": 10, "status": "executed", "price": 191.23},
        {
            "ticker": "TSLA",
            "side": "buy",
            "quantity": 1000,
            "status": "failed",
            "error": "Insufficient cash",
            "code": "INSUFFICIENT_CASH",
        },
    ],
    "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "executed"}],
}


def test_add_and_read(db_path):
    u = db.add_chat_message("user", "Buy 10 AAPL", None)
    a = db.add_chat_message("assistant", "Buying 10 AAPL.", ACTIONS)
    assert set(u) == {"id", "role", "content", "created_at", "actions"}
    assert u["actions"] is None
    assert a["actions"] == ACTIONS
    assert isinstance(a["created_at"], float)
    msgs = db.get_chat_messages()
    assert [m["id"] for m in msgs] == [u["id"], a["id"]]
    assert msgs[1]["actions"] == ACTIONS
    assert msgs[0]["actions"] is None


def test_empty_actions_dict_preserved(db_path):
    db.add_chat_message("assistant", "hi", {"trades": [], "watchlist_changes": []})
    assert db.get_chat_messages()[0]["actions"] == {"trades": [], "watchlist_changes": []}


def test_limit_returns_most_recent_oldest_first(db_path):
    for i in range(60):
        db.add_chat_message("user" if i % 2 == 0 else "assistant", f"m{i}", None)
    msgs = db.get_chat_messages()
    assert len(msgs) == 50
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(10, 60)]
    last20 = db.get_chat_messages(limit=20)
    assert [m["content"] for m in last20] == [f"m{i}" for i in range(40, 60)]


def test_invalid_role(db_path):
    with pytest.raises(ValueError):
        db.add_chat_message("system", "x", None)


def test_users_isolated(db_path):
    db.add_chat_message("user", "mine", None)
    db.add_chat_message("user", "theirs", None, user_id="other")
    assert [m["content"] for m in db.get_chat_messages()] == ["mine"]
    assert [m["content"] for m in db.get_chat_messages(user_id="other")] == ["theirs"]
