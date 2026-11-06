"""Static frontend mount vs /api routing: unmatched /api/* is always the JSON 404."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.conftest import FakeMarket, _clear_cache


@pytest.fixture(params=["with_static", "without_static"])
def app_client(request, fresh_db, tmp_path, monkeypatch):
    static = tmp_path / "static"
    if request.param == "with_static":
        static.mkdir()
        (static / "index.html").write_text("<html>FinAlly app</html>")
        (static / "404.html").write_text("<html>Next 404</html>")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setattr(main, "create_market_data_source", lambda cache: FakeMarket(cache))
    _clear_cache()
    with TestClient(main.create_app(static_dir=static), raise_server_exceptions=False) as c:
        c.has_static = request.param == "with_static"
        yield c
    _clear_cache()


@pytest.mark.parametrize("method", ["get", "post", "delete"])
@pytest.mark.parametrize("path", ["/api/does-not-exist", "/api/portfolio/nope/deeper", "/api/"])
def test_unknown_api_path_is_json_404(app_client, method, path):
    r = getattr(app_client, method)(path)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert set(body) == {"error", "code"} and body["code"] == "NOT_FOUND"


def test_real_api_routes_still_win(app_client):
    assert app_client.get("/api/health").json() == {"status": "ok"}
    assert app_client.get("/api/watchlist").status_code == 200
    assert app_client.delete("/api/watchlist/GOOGL").status_code == 204
    r = app_client.delete("/api/watchlist/GOOGL")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


def test_static_frontend_served_at_root(app_client):
    r = app_client.get("/")
    if app_client.has_static:
        assert r.status_code == 200 and "FinAlly app" in r.text
    else:
        assert r.status_code == 404
