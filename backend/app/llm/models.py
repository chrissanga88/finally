"""Pydantic Structured Output schema for the chat assistant (PLAN §10)."""

from typing import Literal

from pydantic import BaseModel


class TradeRequest(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]


class ChatResponse(BaseModel):
    message: str  # conversational text shown to the user
    trades: list[TradeRequest] = []  # trades to auto-execute
    watchlist_changes: list[WatchlistChange] = []  # watchlist modifications
