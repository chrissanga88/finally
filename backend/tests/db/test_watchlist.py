import time

from app import db


def test_add_new(db_path):
    before = time.time()
    entry, created = db.add_watchlist("PYPL")
    assert created is True
    assert entry["ticker"] == "PYPL"
    assert isinstance(entry["added_at"], float)
    assert before - 1 <= entry["added_at"] <= time.time() + 1
    assert db.get_watchlist()[-1]["ticker"] == "PYPL"
    assert db.is_on_watchlist("PYPL")


def test_add_existing_is_noop(db_path):
    first, _ = db.add_watchlist("PYPL")
    again, created = db.add_watchlist("PYPL")
    assert created is False
    assert again == first
    assert len(db.get_watchlist()) == 11


def test_remove(db_path):
    assert db.remove_watchlist("AAPL") is True
    assert not db.is_on_watchlist("AAPL")
    assert db.remove_watchlist("AAPL") is False
    assert db.remove_watchlist("NOPE") is False


def test_watchlist_ordering_by_added_at(db_path):
    db.add_watchlist("ZZZ")
    db.add_watchlist("AAA")
    tickers = [w["ticker"] for w in db.get_watchlist()]
    assert tickers[-2:] == ["ZZZ", "AAA"]


def test_users_are_isolated(db_path):
    db.add_watchlist("PYPL", user_id="other")
    assert not db.is_on_watchlist("PYPL")
    assert [w["ticker"] for w in db.get_watchlist(user_id="other")] == ["PYPL"]


def test_tracked_tickers_is_union(db_path):
    db.apply_trade("PYPL", "buy", 1, 50.0, 9950.0, 1, 50.0, 10000.0)
    db.remove_watchlist("AAPL")
    db.apply_trade("AAPL", "buy", 1, 100.0, 9850.0, 1, 100.0, 10000.0)
    tracked = db.get_tracked_tickers()
    assert tracked[:9] == [t for t in db.DEFAULT_WATCHLIST if t != "AAPL"]
    assert set(tracked[9:]) == {"AAPL", "PYPL"}
    assert len(tracked) == len(set(tracked))
    # held ticker on watchlist not duplicated
    db.apply_trade("MSFT", "buy", 1, 10.0, 9840.0, 1, 10.0, 10000.0)
    assert db.get_tracked_tickers().count("MSFT") == 1
