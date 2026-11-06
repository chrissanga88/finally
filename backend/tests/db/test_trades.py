import threading

import pytest

from app import db


def buy(ticker, qty, price):
    """Compute values like app.portfolio would, then persist."""
    cash = db.get_cash()
    pos = db.get_position(ticker)
    old_q = pos["quantity"] if pos else 0.0
    old_avg = pos["avg_cost"] if pos else 0.0
    cost = round(qty * price, 2)
    new_q = round(old_q + qty, 4)
    new_avg = (old_q * old_avg + qty * price) / new_q
    return db.apply_trade(ticker, "buy", qty, price, round(cash - cost, 2), new_q, new_avg, 0.0)


def sell(ticker, qty, price):
    cash = db.get_cash()
    pos = db.get_position(ticker)
    old_q = pos["quantity"] if pos else 0.0
    old_avg = pos["avg_cost"] if pos else 0.0
    cost = round(qty * price, 2)
    return db.apply_trade(
        ticker, "sell", qty, price, round(cash + cost, 2), round(old_q - qty, 4), old_avg, 0.0
    )


def test_buy_updates_everything(db_path):
    t = db.apply_trade("AAPL", "buy", 10, 191.23, 8087.70, 10, 191.23, 10012.5)
    assert set(t) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert t["ticker"] == "AAPL" and t["side"] == "buy"
    assert t["quantity"] == 10 and t["price"] == 191.23
    assert isinstance(t["executed_at"], float)
    assert db.get_cash() == 8087.70
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 10 and pos["avg_cost"] == 191.23
    assert isinstance(pos["updated_at"], float)
    trades = db.get_trades()
    assert len(trades) == 1 and trades[0]["id"] == t["id"]
    hist = db.get_snapshot_history()
    assert [h["total_value"] for h in hist] == [10000.0, 10012.5]


def test_weighted_avg_and_upsert(db_path):
    buy("AAPL", 10, 100.0)
    buy("AAPL", 10, 200.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 20
    assert pos["avg_cost"] == pytest.approx(150.0)
    assert len(db.get_positions()) == 1
    assert db.get_cash() == 7000.0


def test_sell_partial_keeps_avg(db_path):
    buy("AAPL", 10, 100.0)
    sell("AAPL", 4, 90.0)  # sell at a loss
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 6
    assert pos["avg_cost"] == 100.0
    assert db.get_cash() == 9360.0


def test_sell_all_deletes_position(db_path):
    buy("AAPL", 10, 100.0)
    sell("AAPL", 10, 110.0)
    assert db.get_position("AAPL") is None
    assert db.get_positions() == []
    assert db.get_cash() == 10100.0
    assert [t["side"] for t in db.get_trades()] == ["buy", "sell"]


def test_remainder_at_threshold_kept(db_path):
    buy("AAPL", 1.0001, 10.0)
    assert db.get_position("AAPL")["quantity"] == 1.0001
    db.apply_trade("AAPL", "sell", 1.0, 10.0, round(db.get_cash() + 10.0, 2), 0.0001, 10.0, 0)
    assert db.get_position("AAPL")["quantity"] == 0.0001  # exactly 0.0001 is kept


def test_new_quantity_below_threshold_deletes(db_path):
    db.apply_trade("AAPL", "buy", 1.0, 10.0, 9990.0, 1.0, 10.0, 0)
    conn = db.connect()
    try:  # simulate float drift: position slightly above the sold quantity
        conn.execute("UPDATE positions SET quantity = 1.00004 WHERE ticker='AAPL'")
    finally:
        conn.close()
    db.apply_trade("AAPL", "sell", 1.0, 10.0, 10000.0, 0.00004, 10.0, 0)
    assert db.get_position("AAPL") is None


def test_rounding_on_write(db_path):
    t = db.apply_trade("AAPL", "buy", 1.23456, 10.0, 9987.654321, 1.23456, 10.0, 10000.123)
    assert t["quantity"] == 1.2346
    assert db.get_position("AAPL")["quantity"] == 1.2346
    assert db.get_cash() == 9987.65
    assert db.get_snapshot_history()[-1]["total_value"] == 10000.12


def test_conflict_when_cash_changed(db_path):
    with pytest.raises(db.TradeConflict):
        db.apply_trade("AAPL", "buy", 10, 100.0, 5000.0, 10, 100.0, 0)  # wrong new_cash
    _assert_untouched()


def test_conflict_insufficient_cash(db_path):
    with pytest.raises(db.TradeConflict):
        db.apply_trade("AAPL", "buy", 1000, 100.0, -90000.0, 1000, 100.0, 0)
    _assert_untouched()


def test_conflict_insufficient_shares(db_path):
    with pytest.raises(db.TradeConflict):
        db.apply_trade("AAPL", "sell", 1, 100.0, 10100.0, -1, 0.0, 0)
    _assert_untouched()


def test_conflict_quantity_mismatch(db_path):
    buy("AAPL", 10, 100.0)
    with pytest.raises(db.TradeConflict):
        db.apply_trade("AAPL", "buy", 1, 100.0, 8900.0, 1, 100.0, 0)  # should be 11


def test_invalid_side(db_path):
    with pytest.raises(ValueError):
        db.apply_trade("AAPL", "short", 1, 100.0, 9900.0, 1, 100.0, 0)


def test_concurrent_stale_trades_one_conflicts(db_path):
    """Two callers computed from the same state; only one may commit."""
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        try:
            db.apply_trade("AAPL", "buy", 50, 100.0, 5000.0, 50, 100.0, 0)
            results.append("ok")
        except db.TradeConflict:
            results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]
    assert db.get_cash() == 5000.0
    assert db.get_position("AAPL")["quantity"] == 50


def _assert_untouched():
    assert db.get_cash() == 10000.0
    assert db.get_positions() == []
    assert db.get_trades() == []
    assert len(db.get_snapshot_history()) == 1
