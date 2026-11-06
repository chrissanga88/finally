"""Portfolio, trade and watchlist business rules shared by REST routes and the LLM."""

from .service import (
    TradeError,
    add_to_watchlist,
    compute_portfolio,
    execute_trade,
    get_portfolio,
    get_portfolio_history,
    get_watchlist_items,
    normalize_quantity,
    normalize_ticker,
    remove_from_watchlist,
)

get_watchlist = get_watchlist_items  # alias used by app.llm

__all__ = [
    "TradeError",
    "add_to_watchlist",
    "compute_portfolio",
    "execute_trade",
    "get_portfolio",
    "get_portfolio_history",
    "get_watchlist",
    "get_watchlist_items",
    "normalize_quantity",
    "normalize_ticker",
    "remove_from_watchlist",
]
