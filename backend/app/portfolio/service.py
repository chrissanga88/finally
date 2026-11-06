"""Portfolio, trade and watchlist business rules (PLAN §8, §9).

One shared implementation for manual endpoints and LLM-initiated actions.
"""

from __future__ import annotations

import logging
import math
import re

from app import db
from app.errors import ApiError
from app.tracking import get_price_cache, is_tracked_in_db, sync_tracking

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z][A-Z.]{0,5}$")
MIN_QUANTITY = 0.0001
SNAPSHOT_MAX_AGE_S = 25.0
_TRADE_ATTEMPTS = 3


class TradeError(ApiError):
    """A domain error from trading / watchlist operations (default status 400)."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def normalize_ticker(raw: object) -> str:
    """Uppercase + trim, then require ^[A-Z][A-Z.]{0,5}$; else TradeError(INVALID_TICKER)."""
    ticker = raw.strip().upper() if isinstance(raw, str) else ""
    if not TICKER_RE.match(ticker):
        raise TradeError("INVALID_TICKER", f"Invalid ticker: {raw!r}")
    return ticker


def normalize_quantity(raw: object) -> float:
    """Round to 4 dp; must be a finite number > 0 after rounding."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise TradeError("INVALID_QUANTITY", f"Quantity must be a number, got {raw!r}")
    if not math.isfinite(raw):
        raise TradeError("INVALID_QUANTITY", "Quantity must be a finite number")
    qty = round(float(raw), 4)
    if qty <= 0:
        raise TradeError("INVALID_QUANTITY", "Quantity must be greater than 0")
    return qty


def _normalize_side(raw: object) -> str:
    side = raw.strip().lower() if isinstance(raw, str) else ""
    if side not in ("buy", "sell"):
        raise TradeError("VALIDATION_ERROR", f"Side must be 'buy' or 'sell', got {raw!r}")
    return side


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_qty(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def _price_or(ticker: str, fallback: float) -> float:
    price = get_price_cache().get_price(ticker)
    return price if price is not None else fallback


def _total_value(cash: float, positions: list[dict]) -> float:
    return round(
        cash + sum(p["quantity"] * _price_or(p["ticker"], p["avg_cost"]) for p in positions), 2
    )


def compute_portfolio() -> dict:
    """GET /api/portfolio shape, valued at live cached prices. No side effects."""
    cash = round(db.get_cash(), 2)
    rows = db.get_positions()
    items = []
    for p in rows:
        qty = p["quantity"]
        avg = p["avg_cost"]
        price = _price_or(p["ticker"], avg)
        market_value = qty * price
        cost_basis = qty * avg
        pnl = market_value - cost_basis
        items.append(
            {
                "ticker": p["ticker"],
                "quantity": qty,
                "avg_cost": round(avg, 4),
                "current_price": price,
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_percent": round(pnl / cost_basis * 100, 2) if cost_basis else 0.0,
                "_mv": market_value,
            }
        )
    positions_value = sum((i["_mv"] for i in items), 0.0)
    total = cash + positions_value
    for i in items:
        i["weight"] = round(i.pop("_mv") / total, 4) if total > 0 else 0.0
    return {
        "cash_balance": cash,
        "total_value": round(total, 2),
        "positions_value": round(positions_value, 2),
        "unrealized_pnl": round(sum((i["unrealized_pnl"] for i in items), 0.0), 2),
        "positions": items,
    }


def get_portfolio() -> dict:
    """Portfolio at live prices; records a snapshot if the latest is > 25 s old."""
    portfolio = compute_portfolio()
    db.record_snapshot_if_stale(portfolio["total_value"], max_age_s=SNAPSHOT_MAX_AGE_S)
    return portfolio


def get_portfolio_history() -> dict:
    """GET /api/portfolio/history: records a stale snapshot first, then last 24 h."""
    get_portfolio()
    return {"snapshots": db.get_snapshot_history(hours=24, max_points=1000)}


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


async def execute_trade(ticker: str, side: str, quantity: float) -> dict:
    """Execute a market order (PLAN §9). Returns {"trade": {...}, "cash_balance": float}.

    Raises TradeError with code INVALID_TICKER, INVALID_QUANTITY, VALIDATION_ERROR,
    NO_PRICE, INSUFFICIENT_CASH or INSUFFICIENT_SHARES.
    """
    ticker = normalize_ticker(ticker)
    side = _normalize_side(side)
    qty = normalize_quantity(quantity)

    if side == "buy" and not is_tracked_in_db(ticker):
        # Untracked buy: watch it first. Persists even if the buy then fails (no rollback).
        db.add_watchlist(ticker)
        await sync_tracking(ticker)

    for _attempt in range(_TRADE_ATTEMPTS):
        # No awaits between reading state and apply_trade; apply_trade re-validates
        # inside BEGIN IMMEDIATE and raises TradeConflict if another writer interleaved.
        position = db.get_position(ticker)
        if side == "sell" and position is None:
            raise TradeError("INSUFFICIENT_SHARES", f"Insufficient shares: you hold no {ticker}")
        price = get_price_cache().get_price(ticker)
        if price is None:
            raise TradeError("NO_PRICE", f"No price available for {ticker} yet; try again shortly")

        cash = round(db.get_cash(), 2)
        cost = round(qty * price, 2)
        held = position["quantity"] if position else 0.0
        old_avg = position["avg_cost"] if position else 0.0

        if side == "buy":
            if cash < cost:
                raise TradeError(
                    "INSUFFICIENT_CASH",
                    f"Insufficient cash: need {_money(cost)}, have {_money(cash)}",
                )
            new_cash = round(cash - cost, 2)
            new_qty = round(held + qty, 4)
            new_avg = (held * old_avg + qty * price) / (held + qty)
        else:
            if held < qty:
                raise TradeError(
                    "INSUFFICIENT_SHARES",
                    f"Insufficient shares: need {_fmt_qty(qty)} {ticker}, have {_fmt_qty(held)}",
                )
            new_cash = round(cash + cost, 2)
            new_qty = round(held - qty, 4)
            new_avg = old_avg

        others = [p for p in db.get_positions() if p["ticker"] != ticker]
        if new_qty >= MIN_QUANTITY:
            others.append({"ticker": ticker, "quantity": new_qty, "avg_cost": new_avg})
        snapshot_total = _total_value(new_cash, others)

        try:
            trade = db.apply_trade(
                ticker, side, qty, price, new_cash, new_qty, new_avg, snapshot_total
            )
        except db.TradeConflict:
            logger.info("Trade conflict on %s %s %s; retrying", side, qty, ticker)
            continue
        break
    else:
        raise ApiError("INTERNAL_ERROR", "Trade could not be completed; please retry", 500)

    if side == "sell" and new_qty < MIN_QUANTITY:
        await sync_tracking(ticker)  # closing a position may stop pricing it
    return {"trade": trade, "cash_balance": new_cash}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


def _watchlist_item(ticker: str, added_at: float) -> dict:
    update = get_price_cache().get(ticker)
    return {
        "ticker": ticker,
        "price": update.price if update else None,
        "session_change_percent": update.session_change_percent if update else None,
        "added_at": added_at,
    }


def get_watchlist_items() -> list[dict]:
    """GET /api/watchlist items, with live prices (null if not yet priced)."""
    return [_watchlist_item(w["ticker"], w["added_at"]) for w in db.get_watchlist()]


async def add_to_watchlist(ticker: str) -> tuple[dict, bool]:
    """Add a ticker (no-op if present). Returns (item with price, created)."""
    ticker = normalize_ticker(ticker)
    entry, created = db.add_watchlist(ticker)
    await sync_tracking(ticker)
    return _watchlist_item(entry["ticker"], entry["added_at"]), created


async def remove_from_watchlist(ticker: str) -> None:
    """Remove a ticker; TradeError(NOT_FOUND, 404) if absent. Keeps pricing held tickers."""
    ticker = normalize_ticker(ticker)
    if not db.remove_watchlist(ticker):
        raise TradeError("NOT_FOUND", f"{ticker} is not on the watchlist", 404)
    await sync_tracking(ticker)
