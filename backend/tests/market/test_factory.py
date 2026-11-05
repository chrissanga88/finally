"""Tests for create_market_data_source."""

import pytest

from app.market.cache import PriceCache
from app.market.factory import MAX_POLL_INTERVAL, create_market_data_source
from app.market.massive_client import DEFAULT_POLL_INTERVAL, MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL", raising=False)


def test_no_key_uses_simulator():
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_key_uses_simulator(monkeypatch, value):
    monkeypatch.setenv("MASSIVE_API_KEY", value)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_key_uses_massive_with_trimmed_key_and_default_interval(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "  abc  ")
    src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource)
    assert src._api_key == "abc"
    assert src._base_interval == DEFAULT_POLL_INTERVAL


@pytest.mark.parametrize(("raw", "expected"), [("5", 5.0), ("2.5", 2.5), ("0.2", 1.0)])
def test_poll_interval_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", raw)
    assert create_market_data_source(PriceCache())._base_interval == expected


@pytest.mark.parametrize("raw", ["fast", "", "  ", "nan", "NaN", "inf", "-inf"])
def test_bad_blank_or_non_finite_interval_falls_back(monkeypatch, raw):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", raw)
    assert create_market_data_source(PriceCache())._base_interval == DEFAULT_POLL_INTERVAL


@pytest.mark.parametrize(("raw", "expected"), [("300", 300.0), ("15000", MAX_POLL_INTERVAL)])
def test_interval_capped(monkeypatch, raw, expected):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", raw)
    assert create_market_data_source(PriceCache())._base_interval == expected


def test_source_uses_given_cache():
    cache = PriceCache()
    assert create_market_data_source(cache)._cache is cache
