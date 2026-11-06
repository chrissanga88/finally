"""FastAPI application: API routers, SSE stream, market data lifespan, static frontend."""

from __future__ import annotations

from app.config import load_env

load_env()  # project-root .env, before anything reads os.environ

import logging  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import db  # noqa: E402
from app.api.chat import router as chat_router  # noqa: E402
from app.api.health import router as health_router  # noqa: E402
from app.api.portfolio import router as portfolio_router  # noqa: E402
from app.api.watchlist import router as watchlist_router  # noqa: E402
from app.config import DEV_FRONTEND_ORIGIN, STATIC_DIR, is_dev  # noqa: E402
from app.errors import ApiError, register_error_handlers  # noqa: E402
from app.market import create_market_data_source, create_stream_router  # noqa: E402
from app.tracking import get_price_cache, set_market, start_market  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()  # schema + seed (PLAN §7), before serving
    market = create_market_data_source(get_price_cache())
    await start_market(market)  # watchlist ∪ positions (PLAN §6)
    app.state.price_cache = get_price_cache()
    app.state.market = market
    try:
        yield
    finally:
        await market.stop()
        set_market(None)


_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def create_app(static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="FinAlly", lifespan=lifespan)
    register_error_handlers(app)

    if is_dev():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[DEV_FRONTEND_ORIGIN],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health_router)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    app.include_router(chat_router)
    app.include_router(create_stream_router(get_price_cache()))

    # Unmatched /api/* paths get the JSON 404, never the static frontend's 404.html.
    @app.api_route("/api/{path:path}", methods=_ALL_METHODS, include_in_schema=False)
    async def api_not_found(path: str) -> None:
        raise ApiError("NOT_FOUND", f"No API route: /api/{path}", 404)

    # Static frontend LAST so it never shadows /api routes; only if a build exists.
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:
        logger.info("No frontend build at %s; serving API only", static_dir)
    return app


app = create_app()
