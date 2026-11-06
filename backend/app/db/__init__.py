"""FinAlly persistence layer (SQLite via stdlib sqlite3).

One short-lived connection per operation. Timestamps are stored as ISO 8601 UTC
strings and returned as Unix float seconds. See planning/TEAM.md for the contract.
"""

from .core import (
    DEFAULT_CASH,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    TradeConflict,
    add_chat_message,
    add_watchlist,
    apply_trade,
    connect,
    from_iso,
    get_cash,
    get_chat_messages,
    get_db_path,
    get_position,
    get_positions,
    get_snapshot_history,
    get_tracked_tickers,
    get_trades,
    get_watchlist,
    init_db,
    is_on_watchlist,
    now_iso,
    record_snapshot_if_stale,
    remove_watchlist,
    to_iso,
)

__all__ = [
    "DEFAULT_CASH",
    "DEFAULT_USER_ID",
    "DEFAULT_WATCHLIST",
    "TradeConflict",
    "add_chat_message",
    "add_watchlist",
    "apply_trade",
    "connect",
    "from_iso",
    "get_cash",
    "get_chat_messages",
    "get_db_path",
    "get_position",
    "get_positions",
    "get_snapshot_history",
    "get_tracked_tickers",
    "get_trades",
    "get_watchlist",
    "init_db",
    "is_on_watchlist",
    "now_iso",
    "record_snapshot_if_stale",
    "remove_watchlist",
    "to_iso",
]
