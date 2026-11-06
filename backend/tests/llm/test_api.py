"""Route tests on a minimal app with just the chat router."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.llm.models import ChatResponse


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_post_chat_mock(client, temp_db, fake_portfolio, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    r = client.post("/api/chat", json={"message": "watch something"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Mock: adding PYPL to your watchlist."
    assert body["actions"] == {
        "trades": [],
        "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "executed"}],
    }
    assert isinstance(body["id"], str) and isinstance(body["created_at"], float)


def test_history_roundtrip(client, temp_db, fake_portfolio, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    client.post("/api/chat", json={"message": "buy"})
    r = client.get("/api/chat/history")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"]["trades"][0]["ticker"] == "AAPL"


def test_history_empty(client, temp_db):
    assert client.get("/api/chat/history").json() == {"messages": []}


def test_503_when_unavailable(client, temp_db, fake_portfolio, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert r.json()["code"] == "LLM_UNAVAILABLE" and "error" in r.json()


def test_502_on_llm_error(client, temp_db, fake_portfolio, fake_anthropic):
    fake_anthropic(parsed=ChatResponse(message="x"), stop_reason="refusal")
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert r.json()["code"] == "LLM_ERROR"
    assert client.get("/api/chat/history").json() == {"messages": []}


def test_blank_message_400(client, temp_db, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    r = client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400
    assert r.json() == {"error": "Message must not be empty", "code": "VALIDATION_ERROR"}
