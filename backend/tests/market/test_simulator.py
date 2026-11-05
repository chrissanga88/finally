"""Tests for GBMSimulator (pure math, seeded RNG)."""

import numpy as np
import pytest

from app.market.seed_prices import (
    CROSS_GROUP_CORR,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)
from app.market.simulator import DEFAULT_DT, GBMSimulator


def rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


class TestInit:
    def test_default_dt_is_half_second_of_trading_year(self):
        assert DEFAULT_DT == pytest.approx(0.5 / (252 * 6.5 * 3600))

    def test_known_tickers_start_at_seed(self):
        sim = GBMSimulator(list(SEED_PRICES), rng=rng())
        for ticker, seed in SEED_PRICES.items():
            assert sim.get_price(ticker) == seed

    def test_unknown_ticker_seed_in_range(self):
        sim = GBMSimulator(["ZZZZ"], rng=rng(1))
        assert 50 <= sim.get_price("ZZZZ") <= 300

    def test_duplicate_tickers_ignored(self):
        assert GBMSimulator(["AAPL", "AAPL"]).get_tickers() == ["AAPL"]

    def test_empty(self):
        sim = GBMSimulator([])
        assert sim.step() == {}
        assert sim.get_tickers() == []

    def test_known_params_used(self):
        sim = GBMSimulator(["TSLA", "NEWCO"])
        assert sim._params["TSLA"] == TICKER_PARAMS["TSLA"]
        assert sim._params["NEWCO"] == {"sigma": 0.25, "mu": 0.05}

    def test_params_are_copies(self):
        sim = GBMSimulator(["NEWCO"])
        sim._params["NEWCO"]["sigma"] = 9.0
        assert GBMSimulator(["NEWCO2"])._params["NEWCO2"]["sigma"] == 0.25


class TestStep:
    def test_reproducible_with_seeded_rng(self):
        a = GBMSimulator(["AAPL", "TSLA", "NEWCO"], rng=rng(42))
        b = GBMSimulator(["AAPL", "TSLA", "NEWCO"], rng=rng(42))
        assert [a.step() for _ in range(200)] == [b.step() for _ in range(200)]

    def test_returns_all_tickers_rounded(self):
        sim = GBMSimulator(["AAPL", "MSFT"], rng=rng())
        out = sim.step()
        assert set(out) == {"AAPL", "MSFT"}
        assert all(round(p, 2) == p for p in out.values())

    def test_small_moves_at_default_dt(self):
        sim = GBMSimulator(["AAPL"], event_probability=0, rng=rng())
        for _ in range(100):
            prev = sim.get_price("AAPL")
            assert abs(sim.step()["AAPL"] / prev - 1) < 0.002

    def test_prices_stay_positive(self):
        sim = GBMSimulator(["TSLA"], dt=0.01, rng=rng())  # exaggerated volatility
        assert all(sim.step()["TSLA"] > 0 for _ in range(10_000))

    def test_internal_precision_not_rounded(self):
        sim = GBMSimulator(["AAPL"], event_probability=0, rng=rng())
        sim.step()
        assert sim._prices["AAPL"] != round(sim._prices["AAPL"], 2)

    def test_tech_correlation(self):
        # larger dt → bigger moves, so 2-dp rounding doesn't distort the returns
        sim = GBMSimulator(["AAPL", "MSFT"], dt=1e-4, event_probability=0, rng=rng(7))
        prices = np.array([[s["AAPL"], s["MSFT"]] for s in (sim.step() for _ in range(20_000))])
        returns = np.diff(np.log(prices), axis=0)
        assert abs(np.corrcoef(returns.T)[0, 1] - INTRA_TECH_CORR) < 0.05

    def test_cross_sector_correlation(self):
        sim = GBMSimulator(["AAPL", "JPM"], dt=1e-4, event_probability=0, rng=rng(8))
        prices = np.array([[s["AAPL"], s["JPM"]] for s in (sim.step() for _ in range(20_000))])
        returns = np.diff(np.log(prices), axis=0)
        assert abs(np.corrcoef(returns.T)[0, 1] - CROSS_GROUP_CORR) < 0.05

    def test_volatility_matches_sigma(self):
        dt = 1e-4
        sim = GBMSimulator(["TSLA"], dt=dt, event_probability=0, rng=rng(9))
        prices = np.array([sim.step()["TSLA"] for _ in range(20_000)])
        sigma_hat = np.std(np.diff(np.log(prices))) / np.sqrt(dt)
        assert sigma_hat == pytest.approx(TICKER_PARAMS["TSLA"]["sigma"], rel=0.05)


class TestEvents:
    def test_probability_one_always_jumps(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=rng(3))
        for _ in range(50):
            prev = sim._prices["AAPL"]
            sim.step()
            move = abs(sim._prices["AAPL"] / prev - 1)
            assert 0.019 < move < 0.051

    def test_jumps_go_both_ways(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=rng(4))
        signs = set()
        for _ in range(50):
            prev = sim._prices["AAPL"]
            sim.step()
            signs.add(sim._prices["AAPL"] > prev)
        assert signs == {True, False}

    def test_probability_zero_never_jumps(self):
        sim = GBMSimulator(["AAPL"], event_probability=0.0, rng=rng(5))
        for _ in range(1_000):
            prev = sim._prices["AAPL"]
            sim.step()
            assert abs(sim._prices["AAPL"] / prev - 1) < 0.01


class TestAddRemove:
    def test_add_and_remove(self):
        sim = GBMSimulator(["AAPL"], rng=rng())
        sim.add_ticker("PYPL")
        assert sim.get_tickers() == ["AAPL", "PYPL"]
        assert set(sim.step()) == {"AAPL", "PYPL"}
        sim.remove_ticker("AAPL")
        assert sim.get_tickers() == ["PYPL"]
        assert sim.get_price("AAPL") is None
        assert set(sim.step()) == {"PYPL"}

    def test_idempotent(self):
        sim = GBMSimulator(["AAPL"])
        before = sim.get_price("AAPL")
        sim.add_ticker("AAPL")
        sim.remove_ticker("NOPE")
        assert sim.get_tickers() == ["AAPL"]
        assert sim.get_price("AAPL") == before

    def test_cholesky_rebuilt(self):
        sim = GBMSimulator(["AAPL"])
        assert sim._cholesky is None
        sim.add_ticker("MSFT")
        assert sim._cholesky.shape == (2, 2)
        sim.add_ticker("JPM")
        assert sim._cholesky.shape == (3, 3)
        sim.remove_ticker("MSFT")
        sim.remove_ticker("JPM")
        assert sim._cholesky is None

    def test_cholesky_holds_for_many_mixed_tickers(self):
        tickers = list(SEED_PRICES) + [f"X{i}" for i in range(40)]
        sim = GBMSimulator(tickers, rng=rng())
        assert len(sim.step()) == 50


class TestCorrelationRules:
    @pytest.mark.parametrize(
        ("a", "b", "rho"),
        [
            ("AAPL", "MSFT", INTRA_TECH_CORR),
            ("NVDA", "NFLX", INTRA_TECH_CORR),
            ("JPM", "V", INTRA_FINANCE_CORR),
            ("AAPL", "JPM", CROSS_GROUP_CORR),
            ("TSLA", "AAPL", TSLA_CORR),
            ("JPM", "TSLA", TSLA_CORR),
            ("PYPL", "AAPL", CROSS_GROUP_CORR),
            ("PYPL", "SQ", CROSS_GROUP_CORR),
        ],
    )
    def test_pairwise(self, a, b, rho):
        assert GBMSimulator._pairwise_correlation(a, b) == rho
        assert GBMSimulator._pairwise_correlation(b, a) == rho
