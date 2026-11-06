import uuid

import pytest

from app import db
from app.db import core


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Fresh, initialized DB per test; the path override is reset afterwards."""
    monkeypatch.setattr(core, "_db_path_override", None)
    path = str(tmp_path / "sub" / "finally.db")
    db.init_db(path)
    return path


@pytest.fixture
def insert_snapshot(db_path):
    def _insert(total_value: float, ts: float, user_id: str = "default") -> None:
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                " VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, total_value, db.to_iso(ts)),
            )
        finally:
            conn.close()

    return _insert


@pytest.fixture
def clear_snapshots(db_path):
    def _clear() -> None:
        conn = db.connect()
        try:
            conn.execute("DELETE FROM portfolio_snapshots")
        finally:
            conn.close()

    return _clear
