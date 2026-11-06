import json

from app.llm.history import HISTORY_LIMIT, build_messages
from app.llm.prompt import build_system_prompt


def _m(role, content, actions=None):
    return {"role": role, "content": content, "actions": actions}


def test_empty_history_is_just_new_message():
    assert build_messages([], "hello") == [{"role": "user", "content": "hello"}]


def test_alternating_history_preserved():
    hist = [_m("user", "a"), _m("assistant", "b")]
    assert build_messages(hist, "c") == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]


def test_assistant_actions_suffix_compact_json():
    actions = {"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1,
                           "status": "executed", "price": 190.0}],
               "watchlist_changes": []}
    out = build_messages([_m("user", "buy"), _m("assistant", "Buying.", actions)], "next")
    compact = json.dumps(actions, separators=(",", ":"))
    assert out[1]["content"] == f"Buying.\n\n[Executed actions: {compact}]"
    assert ", " not in compact and ": " not in compact


def test_no_suffix_when_actions_null():
    out = build_messages([_m("user", "hi"), _m("assistant", "hello", None)], "x")
    assert out[1]["content"] == "hello"


def test_drops_leading_assistant():
    hist = [_m("assistant", "orphan1"), _m("assistant", "orphan2"), _m("user", "u"),
            _m("assistant", "a")]
    out = build_messages(hist, "n")
    assert out[0] == {"role": "user", "content": "u"}
    assert [m["role"] for m in out] == ["user", "assistant", "user"]


def test_merges_consecutive_same_role():
    hist = [_m("user", "u1"), _m("user", "u2"), _m("assistant", "a1"), _m("assistant", "a2")]
    out = build_messages(hist, "u3")
    assert out == [
        {"role": "user", "content": "u1\n\nu2"},
        {"role": "assistant", "content": "a1\n\na2"},
        {"role": "user", "content": "u3"},
    ]


def test_trailing_user_in_history_merges_with_new_message():
    out = build_messages([_m("user", "a"), _m("assistant", "b"), _m("user", "c")], "d")
    assert out[-1] == {"role": "user", "content": "c\n\nd"}
    assert out[-1]["role"] == "user"


def test_window_limited_to_last_20():
    hist = []
    for i in range(30):
        hist.append(_m("user", f"u{i}"))
        hist.append(_m("assistant", f"a{i}"))
    out = build_messages(hist, "new")
    # last 20 stored = u20,a20..u29,a29 -> plus new
    assert len(out) == HISTORY_LIMIT + 1
    assert out[0]["content"] == "u20"
    assert out[-1]["content"] == "new"


def test_window_starting_with_assistant_after_trim():
    hist = [_m("user", "u0")] + [
        m for i in range(1, 11) for m in (_m("assistant", f"a{i}"), _m("user", f"u{i}"))
    ]
    # 21 messages; last 20 start with assistant a1 -> dropped
    out = build_messages(hist, "new")
    assert out[0]["role"] == "user" and out[0]["content"] == "u1"
    roles = [m["role"] for m in out]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))
    assert roles[-1] == "user"


def test_does_not_mutate_input():
    hist = [_m("user", "a"), _m("user", "b")]
    build_messages(hist, "c")
    assert hist == [_m("user", "a"), _m("user", "b")]


def test_system_prompt_contains_identity_and_context():
    ctx = {"portfolio": {"cash_balance": 1234.5, "positions": []},
           "watchlist": [{"ticker": "AAPL", "price": 190.0}]}
    s = build_system_prompt(ctx)
    assert "FinAlly" in s and "AI trading assistant" in s
    assert '"cash_balance": 1234.5' in s
    assert '"AAPL"' in s
