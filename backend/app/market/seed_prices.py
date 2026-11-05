"""Seed prices and per-ticker parameters for the market simulator (static data only)."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

# sigma: annualized volatility; mu: annualized drift
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # high volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # high volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Seed price range for tickers not in SEED_PRICES
UNKNOWN_SEED_RANGE: tuple[float, float] = (50.0, 300.0)

CORRELATION_GROUPS: dict[str, frozenset[str]] = {
    "tech": frozenset({"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"}),
    "finance": frozenset({"JPM", "V"}),
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3  # cross-sector and unknown tickers
TSLA_CORR = 0.3  # TSLA "does its own thing"
