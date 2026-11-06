"""Watchlist routes: GET/POST /api/watchlist, DELETE /api/watchlist/{ticker}."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import portfolio

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistIn(BaseModel):
    ticker: str


@router.get("")
def get_watchlist() -> dict:
    return {"tickers": portfolio.get_watchlist_items()}


@router.post("")
async def add_ticker(req: WatchlistIn) -> JSONResponse:
    item, created = await portfolio.add_to_watchlist(req.ticker)
    return JSONResponse(status_code=201 if created else 200, content=item)


@router.delete("/{ticker}", status_code=204)
async def remove_ticker(ticker: str) -> Response:
    await portfolio.remove_from_watchlist(ticker)
    return Response(status_code=204)
