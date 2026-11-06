"""Deterministic mock LLM responses for LLM_MOCK=true (PLAN §10 table)."""

from .models import ChatResponse, TradeRequest, WatchlistChange


def mock_response(user_message: str) -> ChatResponse:
    """Plain substring match on the lowercased message; first match wins."""
    text = user_message.lower()
    if "buy" in text:
        return ChatResponse(
            message="Mock: buying 1 share of AAPL.",
            trades=[TradeRequest(ticker="AAPL", side="buy", quantity=1)],
        )
    if "sell" in text:
        return ChatResponse(
            message="Mock: selling 1 share of AAPL.",
            trades=[TradeRequest(ticker="AAPL", side="sell", quantity=1)],
        )
    if "watch" in text:
        return ChatResponse(
            message="Mock: adding PYPL to your watchlist.",
            watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
        )
    return ChatResponse(message="Mock: I'm FinAlly, your AI trading assistant.")
