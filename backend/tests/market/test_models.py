"""Tests for PriceUpdate."""

import dataclasses

import pytest

from app.market.models import PriceUpdate


def make(price=191.23, prev=191.10, open_=190.00, ts=1730563200.5):
    return PriceUpdate("AAPL", price, prev, ts, open_)


class TestDirection:
    def test_up(self):
        assert make().direction == "up"

    def test_down(self):
        assert make(price=191.0).direction == "down"

    def test_flat(self):
        assert make(price=191.10).direction == "flat"


class TestChange:
    def test_change_is_rounded_difference(self):
        assert make().change == 0.13

    def test_negative_change(self):
        assert make(price=190.10, prev=191.10).change == -1.0

    def test_change_percent(self):
        assert make().change_percent == 0.068

    def test_change_percent_zero_previous(self):
        assert make(prev=0.0).change_percent == 0.0


class TestSessionChange:
    def test_session_change_percent(self):
        assert make().session_change_percent == 0.6474

    def test_negative_session_change(self):
        assert make(price=180.5, open_=190.0).session_change_percent == -5.0

    def test_zero_session_open(self):
        assert make(open_=0.0).session_change_percent == 0.0

    def test_unchanged_since_open(self):
        assert make(price=190.0, open_=190.0).session_change_percent == 0.0


class TestToDict:
    def test_exact_plan_keys(self):
        assert set(make().to_dict()) == {
            "ticker",
            "price",
            "previous_price",
            "timestamp",
            "change",
            "change_percent",
            "direction",
            "session_open",
            "session_change_percent",
        }

    def test_values(self):
        assert make().to_dict() == {
            "ticker": "AAPL",
            "price": 191.23,
            "previous_price": 191.10,
            "timestamp": 1730563200.5,
            "change": 0.13,
            "change_percent": 0.068,
            "direction": "up",
            "session_open": 190.0,
            "session_change_percent": 0.6474,
        }


def test_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make().price = 1.0  # type: ignore[misc]


def test_slots():
    assert not hasattr(make(), "__dict__")
