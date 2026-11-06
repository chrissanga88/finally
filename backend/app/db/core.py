"""SQLite data access for FinAlly.

Conventions (PLAN §7):
- One short-lived connection per operation; never shared across threads.
- Timestamps stored as ISO 8601 UTC strings, returned as Unix float seconds.
- Cash / trade values rounded to 2 dp, quantities to 4 dp on write.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schema import DEFAULT_CASH, DEFAULT_USER_ID, DEFAULT_WATCHLIST, SCHEMA_SQL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "finally.db"

# Positions with a remaining quantity below this are deleted.
MIN_QUANTITY = 0.0001
# Tolerances used when re-validating caller-computed values inside apply_trade.
_CASH_TOL = 0.005
_QTY_TOL = 0.00005

_db_path_override: str | None = None


class TradeConflict(Exception):  # noqa: N818 (name fixed by TEAM.md contract)
    """Raised by apply_trade when DB state changed since the caller computed the trade."""


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="microseconds")


def from_iso(value: str) -> float:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def get_db_path() -> str:
    """Path in use: init_db() override, else DB_PATH env, else <project root>/db/finally.db."""
    if _db_path_override:
        return _db_path_override
    env = os.environ.get("DB_PATH", "").strip()
    return env or str(DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    """Open a new connection in autocommit mode (explicit BEGIN for transactions)."""
    path = get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _transaction(immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Single-connection transaction; commits on success, rolls back on any error."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create tables and seed default data if users_profile is empty.

    If db_path is given it becomes the path used by every later call in this process.
    """
    global _db_path_override
    if db_path is not None:
        _db_path_override = str(db_path)
    path = Path(get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)

    with _conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_SQL)

    with _transaction() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()
        if row[0] > 0:
            return
        now = now_iso()
        conn.execute(
            "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (DEFAULT_USER_ID, DEFAULT_CASH, now),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
            [(DEFAULT_USER_ID, t, now) for t in DEFAULT_WATCHLIST],
        )
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), DEFAULT_USER_ID, DEFAULT_CASH, now),
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _read_cash(conn: sqlite3.Connection, user_id: str) -> float:
    row = conn.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"Unknown user: {user_id}")
    return float(row["cash_balance"])


def _position_dict(row: sqlite3.Row) -> dict:
    return {
        "ticker": row["ticker"],
        "quantity": float(row["quantity"]),
        "avg_cost": float(row["avg_cost"]),
        "updated_at": from_iso(row["updated_at"]),
    }


def get_cash(user_id: str = DEFAULT_USER_ID) -> float:
    with _conn() as conn:
        return _read_cash(conn, user_id)


def get_positions(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions"
            " WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [_position_dict(r) for r in rows]


def get_position(ticker: str, user_id: str = DEFAULT_USER_ID) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions"
            " WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
    return _position_dict(row) if row else None


def get_watchlist(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()
    return [{"ticker": r["ticker"], "added_at": from_iso(r["added_at"])} for r in rows]


def get_tracked_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """watchlist ∪ positions: watchlist order first, then held-only tickers alphabetically."""
    tickers = [w["ticker"] for w in get_watchlist(user_id)]
    seen = set(tickers)
    for p in get_positions(user_id):
        if p["ticker"] not in seen:
            tickers.append(p["ticker"])
            seen.add(p["ticker"])
    return tickers


def is_on_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Watchlist writes
# ---------------------------------------------------------------------------


def add_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> tuple[dict, bool]:
    """Add ticker. Returns ({ticker, added_at}, created); existing ticker is a no-op."""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
            (user_id, ticker, now_iso()),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
    return {"ticker": row["ticker"], "added_at": from_iso(row["added_at"])}, created


def remove_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def apply_trade(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    new_cash: float,
    new_quantity: float,
    new_avg_cost: float,
    snapshot_total_value: float,
    user_id: str = DEFAULT_USER_ID,
) -> dict:
    """Persist an already-validated trade in ONE BEGIN IMMEDIATE transaction.

    Updates cash, upserts/deletes the position (deleted if new_quantity < 0.0001),
    appends to trades and records a snapshot. Business rules live in app.portfolio;
    this re-validates inside the transaction that the caller's values are consistent
    with the current state (cash, held quantity) and raises TradeConflict otherwise.

    Returns the trade row {id, ticker, side, quantity, price, executed_at (Unix s)}.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side!r}")
    quantity = round(float(quantity), 4)
    new_cash = round(float(new_cash), 2)
    new_quantity = round(float(new_quantity), 4)
    cost = round(quantity * float(price), 2)

    with _transaction() as conn:
        cash = _read_cash(conn, user_id)
        row = conn.execute(
            "SELECT quantity FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        ).fetchone()
        held = float(row["quantity"]) if row else 0.0

        if side == "buy":
            if cash + _CASH_TOL < cost:
                raise TradeConflict(f"Insufficient cash: need {cost:.2f}, have {cash:.2f}")
            expected_cash = round(cash - cost, 2)
            expected_qty = round(held + quantity, 4)
        else:
            if held + _QTY_TOL < quantity:
                raise TradeConflict(f"Insufficient shares: need {quantity}, have {held}")
            expected_cash = round(cash + cost, 2)
            expected_qty = round(held - quantity, 4)

        if abs(expected_cash - new_cash) > _CASH_TOL or abs(expected_qty - new_quantity) > _QTY_TOL:
            raise TradeConflict(
                "Portfolio state changed during trade "
                f"(expected cash {expected_cash}, qty {expected_qty}; "
                f"got cash {new_cash}, qty {new_quantity})"
            )

        now = now_iso()
        conn.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id))
        if new_quantity < MIN_QUANTITY:
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
        else:
            conn.execute(
                "INSERT INTO positions (user_id, ticker, quantity, avg_cost, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, ticker) DO UPDATE SET"
                " quantity = excluded.quantity, avg_cost = excluded.avg_cost,"
                " updated_at = excluded.updated_at",
                (user_id, ticker, new_quantity, float(new_avg_cost), now),
            )
        trade_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade_id, user_id, ticker, side, quantity, float(price), now),
        )
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, round(float(snapshot_total_value), 2), now),
        )

    return {
        "id": trade_id,
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": float(price),
        "executed_at": from_iso(now),
    }


def get_trades(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """All trades, oldest first (not part of the public contract; handy for tests)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades"
            " WHERE user_id = ? ORDER BY executed_at, rowid",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ticker": r["ticker"],
            "side": r["side"],
            "quantity": float(r["quantity"]),
            "price": float(r["price"]),
            "executed_at": from_iso(r["executed_at"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def record_snapshot_if_stale(
    total_value: float, max_age_s: float = 25.0, user_id: str = DEFAULT_USER_ID
) -> bool:
    """Insert a snapshot if the latest is older than max_age_s (or none exists).

    Check and insert run in one BEGIN IMMEDIATE transaction, so concurrent callers
    cannot both write. Returns True if a snapshot was written.
    """
    with _transaction() as conn:
        row = conn.execute(
            "SELECT MAX(recorded_at) AS latest FROM portfolio_snapshots WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        now = now_iso()
        if row["latest"] is not None and from_iso(now) - from_iso(row["latest"]) <= max_age_s:
            return False
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, round(float(total_value), 2), now),
        )
        return True


def _downsample(items: list, max_points: int) -> list:
    n = len(items)
    if max_points <= 0:
        return []
    if n <= max_points:
        return items
    if max_points == 1:
        return [items[-1]]
    # Evenly spaced indices, always including first and last.
    idx = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
    return [items[i] for i in idx]


def get_snapshot_history(
    hours: float = 24, max_points: int = 1000, user_id: str = DEFAULT_USER_ID
) -> list[dict]:
    """Snapshots from the last `hours`, oldest first, evenly downsampled to max_points."""
    cutoff = to_iso(datetime.now(UTC).timestamp() - hours * 3600)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots"
            " WHERE user_id = ? AND recorded_at >= ? ORDER BY recorded_at, rowid",
            (user_id, cutoff),
        ).fetchall()
    points = [
        {"timestamp": from_iso(r["recorded_at"]), "total_value": float(r["total_value"])}
        for r in rows
    ]
    return _downsample(points, max_points)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _chat_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": from_iso(row["created_at"]),
        "actions": json.loads(row["actions"]) if row["actions"] is not None else None,
    }


def add_chat_message(
    role: str, content: str, actions: dict | None, user_id: str = DEFAULT_USER_ID
) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError(f"Invalid role: {role!r}")
    msg_id = str(uuid.uuid4())
    now = now_iso()
    actions_json = json.dumps(actions) if actions is not None else None
    with _conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, user_id, role, content, actions_json, now),
        )
    return {
        "id": msg_id,
        "role": role,
        "content": content,
        "created_at": from_iso(now),
        "actions": json.loads(actions_json) if actions_json is not None else None,
    }


def get_chat_messages(limit: int = 50, user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """The most recent `limit` messages, oldest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages"
            " WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_chat_dict(r) for r in reversed(rows)]
