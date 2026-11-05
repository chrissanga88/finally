"""Tests for the SSE stream."""

import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class FakeRequest:
    """Reports connected for `polls` checks, then disconnected."""

    client = None

    def __init__(self, polls: int):
        self.polls = polls

    async def is_disconnected(self) -> bool:
        self.polls -= 1
        return self.polls < 0


async def collect(cache: PriceCache, polls: int, **kw) -> list[str]:
    return [e async for e in _generate_events(cache, FakeRequest(polls), interval=0, **kw)]


def data_events(events: list[str]) -> list[dict]:
    return [json.loads(e.removeprefix("data: ")) for e in events if e.startswith("data: ")]


async def test_retry_then_full_map():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("MSFT", 420.0)
    events = await collect(cache, 3)
    assert events[0] == "retry: 1000\n\n"
    assert events[1].endswith("\n\n")
    [payload] = data_events(events)  # no change after the first → no repeat
    assert set(payload) == {"AAPL", "MSFT"}
    assert payload["AAPL"] == cache.get("AAPL").to_dict()


async def test_emits_again_when_version_changes():
    cache = PriceCache()
    cache.update("AAPL", 190.0)

    class UpdatingRequest(FakeRequest):
        async def is_disconnected(self) -> bool:
            if self.polls == 2:
                cache.update("AAPL", 191.0)
            return await super().is_disconnected()

    events = [e async for e in _generate_events(cache, UpdatingRequest(3), interval=0)]
    prices = [d["AAPL"]["price"] for d in data_events(events)]
    assert prices == [190.0, 191.0]


async def test_empty_cache_sends_empty_map():
    events = await collect(PriceCache(), 1)
    assert events == ["retry: 1000\n\n", "data: {}\n\n"]


async def test_empty_map_sent_after_last_removal():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.remove("AAPL")
    assert data_events(await collect(cache, 1)) == [{}]


async def test_keepalive_when_idle():
    events = await collect(PriceCache(), 3, keepalive=0)
    assert ": keepalive\n\n" in events


async def test_no_keepalive_before_timeout():
    events = await collect(PriceCache(), 3, keepalive=60)
    assert ": keepalive\n\n" not in events


async def test_stops_immediately_when_disconnected():
    events = await collect(PriceCache(), 0)
    assert events == ["retry: 1000\n\n"]


async def test_compact_json():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    payload = (await collect(cache, 1))[1].removeprefix("data: ")
    assert ", " not in payload
    assert '": ' not in payload


def test_router_path_and_reentrancy():
    first = create_stream_router(PriceCache())
    second = create_stream_router(PriceCache())
    assert first is not second  # a module-level router would accumulate routes
    for router in (first, second):
        assert [r.path for r in router.routes] == ["/api/stream/prices"]
    app = FastAPI()
    app.include_router(first)  # mounts without error


async def test_endpoint_returns_event_stream():
    router = create_stream_router(PriceCache())
    [route] = router.routes
    response = await route.endpoint(FakeRequest(0))
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
