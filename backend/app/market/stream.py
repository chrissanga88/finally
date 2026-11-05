"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

SSE_CHECK_INTERVAL = 0.5
SSE_KEEPALIVE_SECONDS = 15.0


def create_stream_router(
    price_cache: PriceCache, interval: float = SSE_CHECK_INTERVAL
) -> APIRouter:
    """Build a new router per call, so calling twice never registers the route twice."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable proxy buffering
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = SSE_CHECK_INTERVAL,
    keepalive: float = SSE_KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Emit the full ticker map whenever the cache version changes.

    - First line: `retry: 1000` (browser reconnects 1 s after a drop).
    - An empty map `{}` is sent when the last ticker is removed, so clients drop it.
    - A `: keepalive` comment after `keepalive` seconds of silence keeps proxies
      from closing an idle connection (Massive / EOD mode).
    """
    yield "retry: 1000\n\n"
    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                payload = {t: u.to_dict() for t, u in price_cache.get_all().items()}
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= keepalive:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(interval)
    finally:
        logger.info("SSE client disconnected: %s", client)
