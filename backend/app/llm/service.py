"""Chat orchestration: context, LLM call, action execution, persistence (PLAN §10)."""

import logging

from app import db

from . import client as llm_client
from .history import HISTORY_LIMIT, build_messages
from .mock import mock_response
from .models import ChatResponse, TradeRequest, WatchlistChange
from .prompt import build_system_prompt

logger = logging.getLogger(__name__)


class ChatError(Exception):
    """Chat failure surfaced to the API as {"error", "code"} with `status`."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _error_fields(exc: Exception) -> tuple[str, str]:
    """(message, code) for a failed action. Domain errors carry .code/.message."""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if isinstance(code, str) and code:
        return (message if isinstance(message, str) and message else str(exc)), code
    logger.exception("Unexpected error executing chat action")
    return "Unexpected error", "INTERNAL_ERROR"


def _display_ticker(raw: str) -> str:
    return (raw or "").strip().upper()


def build_context() -> dict:
    """Current portfolio + watchlist (with live prices) for the system prompt.

    Uses compute_portfolio() (no snapshot side effect) rather than get_portfolio().
    """
    from app import portfolio

    return {
        "portfolio": portfolio.compute_portfolio(),
        "watchlist": portfolio.get_watchlist_items(),
    }


async def _execute_watchlist_change(change: WatchlistChange) -> dict:
    from app import portfolio

    result = {"ticker": _display_ticker(change.ticker), "action": change.action}
    try:
        if change.action == "add":
            item, _created = await portfolio.add_to_watchlist(change.ticker)
            result["ticker"] = item.get("ticker", result["ticker"])
        else:
            await portfolio.remove_from_watchlist(change.ticker)
        result["status"] = "executed"
    except Exception as exc:
        error, code = _error_fields(exc)
        result.update(status="failed", error=error, code=code)
    return result


async def _execute_trade(trade: TradeRequest) -> dict:
    from app import portfolio

    result = {
        "ticker": _display_ticker(trade.ticker),
        "side": trade.side,
        "quantity": trade.quantity,
    }
    try:
        outcome = await portfolio.execute_trade(trade.ticker, trade.side, trade.quantity)
        executed = outcome["trade"]
        result.update(
            ticker=executed.get("ticker", result["ticker"]),
            quantity=executed.get("quantity", trade.quantity),
            status="executed",
            price=executed["price"],
        )
    except Exception as exc:
        error, code = _error_fields(exc)
        result.update(status="failed", error=error, code=code)
    return result


async def execute_actions(response: ChatResponse) -> dict:
    """Watchlist changes first, then trades, each in order; failures don't stop the rest."""
    watchlist_results = [await _execute_watchlist_change(c) for c in response.watchlist_changes]
    trade_results = [await _execute_trade(t) for t in response.trades]
    return {"trades": trade_results, "watchlist_changes": watchlist_results}


async def get_llm_response(user_message: str) -> ChatResponse:
    """Mock or real Haiku response. Raises ChatError (503/502)."""
    if llm_client.is_mock_mode():
        return mock_response(user_message)
    if not llm_client.is_available():
        raise ChatError(
            "LLM_UNAVAILABLE",
            "AI chat is unavailable: ANTHROPIC_API_KEY is not configured.",
            503,
        )
    system = build_system_prompt(build_context())
    history = db.get_chat_messages(limit=HISTORY_LIMIT)
    messages = build_messages(history, user_message)
    try:
        return await llm_client.call_llm(system, messages)
    except llm_client.LLMError as exc:
        raise ChatError("LLM_ERROR", str(exc), 502) from exc


async def handle_chat(user_message: str) -> dict:
    """Full chat turn. On LLM failure nothing is stored."""
    response = await get_llm_response(user_message)
    actions = await execute_actions(response)
    db.add_chat_message("user", user_message, None)
    return db.add_chat_message("assistant", response.message, actions)


def get_history(limit: int = 50) -> list[dict]:
    return db.get_chat_messages(limit=limit)
