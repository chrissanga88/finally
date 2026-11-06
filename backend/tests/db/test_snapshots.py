import threading
import time

from app import db
from app.db.core import _downsample


def test_not_stale_after_seed(db_path):
    assert db.record_snapshot_if_stale(123.0) is False
    assert len(db.get_snapshot_history()) == 1


def test_records_when_stale(db_path, clear_snapshots, insert_snapshot):
    clear_snapshots()
    insert_snapshot(10000.0, time.time() - 30)
    assert db.record_snapshot_if_stale(10050.456) is True
    hist = db.get_snapshot_history()
    assert [h["total_value"] for h in hist] == [10000.0, 10050.46]
    # now fresh again
    assert db.record_snapshot_if_stale(1.0) is False


def test_records_when_none(db_path, clear_snapshots):
    clear_snapshots()
    assert db.record_snapshot_if_stale(5.0) is True


def test_custom_max_age(db_path, clear_snapshots, insert_snapshot):
    clear_snapshots()
    insert_snapshot(1.0, time.time() - 10)
    assert db.record_snapshot_if_stale(2.0, max_age_s=25) is False
    assert db.record_snapshot_if_stale(2.0, max_age_s=5) is True


def test_concurrent_only_one_writes(db_path, clear_snapshots, insert_snapshot):
    clear_snapshots()
    insert_snapshot(1.0, time.time() - 60)
    n = 8
    barrier = threading.Barrier(n)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        r = db.record_snapshot_if_stale(2.0)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert len(db.get_snapshot_history()) == 2


def test_history_window_and_order(db_path, clear_snapshots, insert_snapshot):
    clear_snapshots()
    now = time.time()
    insert_snapshot(1.0, now - 25 * 3600)  # outside 24h
    insert_snapshot(3.0, now - 100)
    insert_snapshot(2.0, now - 3600)
    hist = db.get_snapshot_history()
    assert [h["total_value"] for h in hist] == [2.0, 3.0]
    assert hist[0]["timestamp"] < hist[1]["timestamp"]
    assert abs(hist[1]["timestamp"] - (now - 100)) < 1e-3
    assert len(db.get_snapshot_history(hours=26)) == 3


def test_history_downsampled(db_path, clear_snapshots, insert_snapshot):
    clear_snapshots()
    now = time.time()
    for i in range(2500):
        insert_snapshot(float(i), now - 3600 + i)
    hist = db.get_snapshot_history()
    assert len(hist) == 1000
    values = [h["total_value"] for h in hist]
    assert values[0] == 0.0 and values[-1] == 2499.0
    assert values == sorted(values)
    gaps = {round(b - a) for a, b in zip(values, values[1:], strict=False)}
    assert gaps <= {2.0, 3.0}  # evenly spaced
    assert len(db.get_snapshot_history(max_points=10)) == 10


def test_downsample_helper():
    items = list(range(10))
    assert _downsample(items, 20) == items
    assert _downsample(items, 10) == items
    assert _downsample(items, 2) == [0, 9]
    assert _downsample(items, 1) == [9]
    assert _downsample(items, 0) == []
    assert _downsample([], 5) == []
    out = _downsample(list(range(1001)), 11)
    assert out == list(range(0, 1001, 100))


def test_users_isolated(db_path, insert_snapshot):
    insert_snapshot(5.0, time.time(), user_id="other")
    assert [h["total_value"] for h in db.get_snapshot_history(user_id="other")] == [5.0]
    assert len(db.get_snapshot_history()) == 1
