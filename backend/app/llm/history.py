"""Build the Anthropic `messages` list from stored chat history (PLAN §10 step 2-3)."""

import json

HISTORY_LIMIT = 20


def format_turn(msg: dict) -> str:
    """Stored content, plus a compact actions suffix for assistant turns with actions."""
    content = msg.get("content") or ""
    actions = msg.get("actions")
    if msg.get("role") == "assistant" and actions is not None:
        compact = json.dumps(actions, separators=(",", ":"))
        content = f"{content}\n\n[Executed actions: {compact}]"
    return content


def build_messages(history: list[dict], new_message: str) -> list[dict]:
    """Return alternating user/assistant messages starting with user, ending with the new user turn.

    `history` is oldest-first stored messages (dicts with role/content/actions); only the
    last HISTORY_LIMIT are used. Leading assistant messages are dropped and consecutive
    same-role messages are merged with a blank line.
    """
    window = list(history)[-HISTORY_LIMIT:]
    turns = [
        {"role": m["role"], "content": format_turn(m)}
        for m in window
        if m.get("role") in ("user", "assistant")
    ]
    turns.append({"role": "user", "content": new_message})

    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)

    merged: list[dict] = []
    for turn in turns:
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["content"] = f"{merged[-1]['content']}\n\n{turn['content']}"
        else:
            merged.append(dict(turn))
    return merged
