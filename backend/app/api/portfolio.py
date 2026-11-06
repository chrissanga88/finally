"""Portfolio routes: /api/portfolio, /api/portfolio/trade, /api/portfolio/history."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeIn(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    # strict: reject strings like "10"; ints are still accepted as floats
    quantity: Annotated[float, Field(strict=True, allow_inf_nan=True)]


@router.get("")
def get_portfolio() -> dict:
    return portfolio.get_portfolio()


@router.post("/trade")
async def trade(req: TradeIn) -> dict:
    return await portfolio.execute_trade(req.ticker, req.side, req.quantity)


@router.get("/history")
def get_history() -> dict:
    return portfolio.get_portfolio_history()
