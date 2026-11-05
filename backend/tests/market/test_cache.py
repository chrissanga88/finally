"""Tests for PriceCache."""

import threading
import time

import pytest

from app.market.cache import PriceCache


class TestUpdate:
    def test_first_update_is_flat_and_sets_session_open(self):
        u = PriceCache().update("AAPL", 190.004)
        assert (u.price, u.previous_price, u.session_open, u.direction) == (
            190.0,
            190.0,
            190.0,
            "flat",
        )

    def test_previous_price_tracks_last_update(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        u = c.update("AAPL", 191.5)
        assert (u.previous_price, u.direction) == (190.0, "up")
        u = c.update("AAPL", 191.0)
        assert (u.previous_price, u.direction) == (191.5, "down")

    def test_price_rounded_to_cents(self):
        assert PriceCache().update("AAPL", 190.12678).price == 190.13

    def test_default_timestamp_is_now(self):
        before = time.time()
        ts = PriceCache().update("AAPL", 1.0).timestamp
        assert before <= ts <= time.time()

    def test_explicit_timestamp(self):
        assert PriceCache().update("AAPL", 1.0, timestamp=123.5).timestamp == 123.5

    def test_zero_timestamp_is_respected(self):
        assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0

    def test_returns_what_is_stored(self):
        c = PriceCache()
        u = c.update("AAPL", 1.0)
        assert c.get("AAPL") is u

    def test_tickers_are_independent(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        u = c.update("MSFT", 420.0)
        assert (u.previous_price, u.session_open) == (420.0, 420.0)


class TestValidation:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
    def test_invalid_price_rejected_without_side_effects(self, bad):
        c = PriceCache()
        c.update("AAPL", 190.0)
        v = c.version
        with pytest.raises(ValueError, match="AAPL"):
            c.update("AAPL", bad)
        assert c.version == v
        assert c.get_price("AAPL") == 190.0

    def test_invalid_first_price_leaves_no_session(self):
        c = PriceCache()
        with pytest.raises(ValueError):
            c.update("AAPL", float("nan"))
        assert "AAPL" not in c
        assert c.update("AAPL", 190.0).session_open == 190.0

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_session_open_ignored(self, bad):
        assert PriceCache().update("AAPL", 190.0, session_open=bad).session_open == 190.0


class TestSessionOpen:
    def test_first_price_sticks(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        u = c.update("AAPL", 192.0)
        assert u.session_open == 190.0
        assert u.session_change_percent == 1.0526

    def test_explicit_override(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        assert c.update("AAPL", 192.0, session_open=189.5).session_open == 189.5

    def test_explicit_override_persists(self):
        c = PriceCache()
        c.update("AAPL", 190.0, session_open=189.5)
        assert c.update("AAPL", 191.0).session_open == 189.5

    def test_explicit_on_first_update(self):
        assert PriceCache().update("AAPL", 190.0, session_open=180.0).session_open == 180.0

    def test_non_positive_override_ignored(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        assert c.update("AAPL", 191.0, session_open=0).session_open == 190.0
        assert c.update("AAPL", 191.0, session_open=-5).session_open == 190.0

    def test_override_rounded(self):
        assert PriceCache().update("AAPL", 190.0, session_open=189.499).session_open == 189.5


class TestRemove:
    def test_remove_bumps_version_and_forgets(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        v = c.version
        c.remove("AAPL")
        assert c.version == v + 1
        assert "AAPL" not in c
        assert c.get("AAPL") is None

    def test_remove_absent_is_noop(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        v = c.version
        c.remove("NOPE")
        assert c.version == v

    def test_readded_ticker_starts_fresh_session(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.update("AAPL", 195.0)
        c.remove("AAPL")
        u = c.update("AAPL", 200.0)
        assert (u.previous_price, u.session_open, u.direction) == (200.0, 200.0, "flat")


class TestReads:
    def test_get_price(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        assert c.get_price("AAPL") == 190.0
        assert c.get_price("NOPE") is None

    def test_get_all_is_a_copy(self):
        c = PriceCache()
        c.update("AAPL", 1.0)
        snapshot = c.get_all()
        c.update("MSFT", 2.0)
        assert set(snapshot) == {"AAPL"}
        assert set(c.get_all()) == {"AAPL", "MSFT"}

    def test_len_and_contains(self):
        c = PriceCache()
        assert len(c) == 0
        c.update("AAPL", 1.0)
        c.update("MSFT", 1.0)
        assert len(c) == 2
        assert "AAPL" in c
        assert "TSLA" not in c

    def test_version_increments_on_every_update(self):
        c = PriceCache()
        assert c.version == 0
        c.update("AAPL", 1.0)
        c.update("AAPL", 1.0)
        assert c.version == 2


def test_thread_safety():
    c = PriceCache()
    n_threads, n_updates = 8, 500

    def writer(i: int) -> None:
        for k in range(n_updates):
            c.update(f"T{i}", 100.0 + k)
            c.get_all()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.version == n_threads * n_updates
    assert len(c) == n_threads
    assert all(c.get_price(f"T{i}") == 100.0 + n_updates - 1 for i in range(n_threads))
