import sqlite3
from pathlib import Path

from app import db
from app.db import core


def test_creates_parent_dir_and_file(db_path):
    assert Path(db_path).exists()
    assert db.get_db_path() == db_path


def test_all_tables_exist(db_path):
    conn = db.connect()
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {
        "users_profile",
        "watchlist",
        "positions",
        "trades",
        "portfolio_snapshots",
        "chat_messages",
    } <= names


def test_seed_data(db_path):
    assert db.get_cash() == 10000.0
    assert [w["ticker"] for w in db.get_watchlist()] == db.DEFAULT_WATCHLIST
    assert len(db.DEFAULT_WATCHLIST) == 10
    hist = db.get_snapshot_history()
    assert [h["total_value"] for h in hist] == [10000.0]
    assert db.get_positions() == []
    assert db.get_chat_messages() == []


def test_init_is_idempotent(db_path):
    db.add_watchlist("PYPL")
    db.init_db(db_path)
    db.init_db()
    assert len(db.get_watchlist()) == 11
    assert len(db.get_snapshot_history()) == 1
    assert db.get_cash() == 10000.0


def test_user_id_defaults_in_schema(db_path):
    conn = db.connect()
    try:
        conn.execute("INSERT INTO watchlist (ticker, added_at) VALUES ('ZZ', ?)", (db.now_iso(),))
        row = conn.execute("SELECT user_id FROM watchlist WHERE ticker='ZZ'").fetchone()
    finally:
        conn.close()
    assert row["user_id"] == "default"


def test_composite_primary_key(db_path):
    conn = db.connect()
    try:
        try:
            conn.execute(
                "INSERT INTO watchlist (user_id, ticker, added_at) VALUES ('default','AAPL',?)",
                (db.now_iso(),),
            )
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        # same ticker for another user is fine
        conn.execute(
            "INSERT INTO watchlist (user_id, ticker, added_at) VALUES ('other','AAPL',?)",
            (db.now_iso(),),
        )
    finally:
        conn.close()
    assert raised


def test_db_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_db_path_override", None)
    target = tmp_path / "envdir" / "x.db"
    monkeypatch.setenv("DB_PATH", str(target))
    assert db.get_db_path() == str(target)
    db.init_db()
    assert target.exists()


def test_db_path_default(monkeypatch):
    monkeypatch.setattr(core, "_db_path_override", None)
    monkeypatch.delenv("DB_PATH", raising=False)
    expected = Path(__file__).resolve().parents[3] / "db" / "finally.db"
    assert Path(db.get_db_path()) == expected


def test_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(core, "_db_path_override", None)
    monkeypatch.setenv("DB_PATH", "  ")
    assert db.get_db_path() == str(core.DEFAULT_DB_PATH)


def test_connect_row_factory(db_path):
    conn = db.connect()
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_timestamp_roundtrip():
    ts = 1730563200.512345
    iso = db.to_iso(ts)
    assert iso.endswith("+00:00")
    assert abs(db.from_iso(iso) - ts) < 1e-6
    # naive strings are treated as UTC
    assert db.from_iso("2024-11-02T16:00:00") == 1730563200.0
    assert isinstance(db.from_iso(db.now_iso()), float)


def test_connect_creates_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_db_path_override", None)
    target = tmp_path / "a" / "b" / "x.db"
    monkeypatch.setenv("DB_PATH", str(target))
    db.connect().close()
    assert target.parent.is_dir()
