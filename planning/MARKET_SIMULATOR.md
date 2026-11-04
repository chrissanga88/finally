# Market Simulator — Approach and Code Structure

The simulator is FinAlly's default price source (used whenever `MASSIVE_API_KEY` is empty). It produces believable, correlated, continuously moving prices with occasional dramatic jumps, so the app looks alive without any API key, and so tests and demos are free and offline.

Implemented in `backend/app/market/simulator.py` and `seed_prices.py`; plugs into the unified interface described in `MARKET_INTERFACE.md`. Items marked **CHANGE** are recommended improvements.

---

## 1. Goals

| Goal | How |
|---|---|
| Realistic-looking price paths | Geometric Brownian Motion (log-normal, never negative) |
| Different personalities per stock | Per-ticker volatility `sigma` and drift `mu` |
| Stocks move together | Correlated random shocks via Cholesky decomposition |
| Drama for demos | Rare random 2–5% jumps |
| Any ticker works | Unknown symbols get a random seed price and default parameters |
| Cheap | One numpy draw + a Python loop over ≤ ~50 tickers every 500 ms |

Non-goals: order books, bid/ask spreads, volume, market hours, mean reversion, intraday seasonality.

---

## 2. The Math

### 2.1 Geometric Brownian Motion

Each tick, every price is multiplied by a log-normal factor:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

| Symbol | Meaning | Value |
|---|---|---|
| `S` | price | starts at seed price |
| `μ` | annualized drift | 0.03–0.08 |
| `σ` | annualized volatility | 0.17 (V) – 0.50 (TSLA) |
| `dt` | tick length as a fraction of a trading year | `0.5 / (252 × 6.5 × 3600) ≈ 8.48e-8` |
| `Z` | standard normal shock, correlated across tickers | — |

The `−σ²/2` term (Itô correction) keeps the expected price growing at `μ` rather than drifting upward because of volatility. Using `exp()` guarantees prices stay positive.

**Time scale:** `dt` treats each 500 ms tick as 500 ms of *trading* time, so a stock's volatility looks like a real one's would during market hours:

| Ticker | σ | Typical move per tick | Typical move over 30 min |
|---|---|---|---|
| V ($280) | 0.17 | ~0.005% (~1.4¢) | ~0.30% |
| AAPL ($190) | 0.22 | ~0.006% (~1.2¢) | ~0.38% |
| NVDA ($800) | 0.40 | ~0.012% (~9¢) | ~0.70% |
| TSLA ($250) | 0.50 | ~0.015% (~3.6¢) | ~0.87% |

Moves of about a cent per tick mean that after rounding to 2 dp, roughly a third of AAPL ticks are "flat" — which is why PLAN.md flashes only when the rounded price changes. Drift is negligible within a session (μ = 5%/yr ≈ 0.0006% per 30 min); the random events in §2.3 provide the visible action.

### 2.2 Correlation

Real stocks share sector and market moves. We build an `n × n` correlation matrix `C` from sector rules, take its Cholesky factor `L` (`C = L·Lᵀ`), and transform independent normals:

```
z_ind  ~ N(0, I)          # n independent draws
z_corr = L · z_ind        # now Cov(z_corr) = C
```

Pairwise correlation rules (`_pairwise_correlation`):

| Pair | ρ |
|---|---|
| Either ticker is TSLA | 0.3 (TSLA "does its own thing") |
| Both in tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| Both in finance (JPM, V) | 0.5 |
| Anything else (cross-sector, unknown tickers) | 0.3 |

All values are positive and modest, so the matrix stays positive definite for any mix of tickers and Cholesky always succeeds. The matrix is rebuilt (O(n²) build + O(n³) factorization — trivial for n < 50) only when tickers are added or removed, never per tick.

### 2.3 Random events

After the GBM step, each ticker independently has probability `event_probability = 0.001` per tick of a shock:

```
S ← S · (1 ± U(0.02, 0.05))      # sign 50/50
```

With 10 tickers at 2 ticks/s that's an event roughly every 50 s somewhere on the board — enough to make the watchlist flash and the heatmap shift during a demo. Events are independent (not correlated) and are logged at DEBUG.

### 2.4 Seed prices and parameters

`seed_prices.py` holds static data only:

- `SEED_PRICES` — realistic starting prices for the 10 default tickers (AAPL 190, GOOGL 175, MSFT 420, AMZN 185, TSLA 250, NVDA 800, META 500, JPM 195, V 280, NFLX 600).
- `TICKER_PARAMS` — `{"sigma", "mu"}` per default ticker (TSLA/NVDA high vol; JPM/V low vol).
- `DEFAULT_PARAMS` — `sigma=0.25, mu=0.05` for any other ticker.
- `CORRELATION_GROUPS` and the ρ constants above.

Unknown tickers (e.g. PYPL added by the user) start at `random.uniform(50, 300)`.

Prices restart from seeds on every process start; nothing about the simulation is persisted. Positions keep their `avg_cost` from the DB, so after a restart a holding's P&L jumps to "seed price vs. what you paid". Acceptable for a simulator; see §6 for an optional fix.

---

## 3. Code Structure

```
seed_prices.py       static data (no logic)
simulator.py
├── GBMSimulator           pure, synchronous math — no asyncio, no cache
│   ├── __init__(tickers, dt=DEFAULT_DT, event_probability=0.001)
│   ├── step() -> dict[str, float]      advance all tickers one tick; returns rounded prices
│   ├── add_ticker(t) / remove_ticker(t)   update state + rebuild Cholesky
│   ├── get_price(t) -> float | None
│   ├── get_tickers() -> list[str]
│   ├── _add_ticker_internal(t)         seed price + params (no rebuild; for batch init)
│   ├── _rebuild_cholesky()
│   └── _pairwise_correlation(t1, t2)   static
└── SimulatorDataSource(MarketDataSource)   async adapter
    ├── __init__(price_cache, update_interval=0.5, event_probability=0.001)
    ├── start(tickers)      build GBMSimulator, seed cache, start _run_loop task
    ├── stop()              cancel task (idempotent)
    ├── add_ticker(t)       sim.add_ticker + write seed price to cache immediately
    ├── remove_ticker(t)    sim.remove_ticker + cache.remove
    ├── get_tickers()
    └── _run_loop()         while True: step → cache.update each → sleep(interval)
```

**Why two classes:** `GBMSimulator` is deterministic-given-randomness math that can be unit-tested synchronously (step 10,000 times, check positivity, check correlation statistics) with no event loop or cache. `SimulatorDataSource` only handles lifecycle and wiring, matching `MassiveDataSource` so the factory can swap them.

**State:** internal prices are kept at full float precision; only the values returned by `step()` (and written to the cache) are rounded to 2 dp, so rounding never compounds.

**Hot path (`step`)**: one `np.random.standard_normal(n)`, one matrix-vector product, then a Python loop of `exp` + an event check per ticker. Microseconds for 10–50 tickers; no need to vectorize further.

**Robustness:** `_run_loop` wraps each step in `try/except Exception` and logs, so a bug in one tick never stops the price feed. Cancellation (`stop()`) propagates normally because `CancelledError` isn't an `Exception` subclass.

**Concurrency:** everything runs on the event loop thread; `add_ticker`/`remove_ticker` are `async` methods called from request handlers on the same loop, so `GBMSimulator` needs no locking. Writes go through the thread-safe `PriceCache`.

---

## 4. Usage

```python
from app.market import PriceCache
from app.market.simulator import GBMSimulator, SimulatorDataSource

# Pure simulator (tests, notebooks)
sim = GBMSimulator(["AAPL", "TSLA"])
for _ in range(5):
    print(sim.step())          # {'AAPL': 190.01, 'TSLA': 249.97}

# As the app's data source
cache = PriceCache()
source = SimulatorDataSource(cache)
await source.start(["AAPL", "MSFT"])
await source.add_ticker("PYPL")      # priced immediately
print(cache.get_price("PYPL"))
await source.stop()
```

`backend/market_data_demo.py` runs a live Rich terminal dashboard of the simulator (`uv run market_data_demo.py`).

---

## 5. Testing Approach

Existing tests (`tests/market/test_simulator.py`, `test_simulator_source.py`) cover seeding, add/remove, unknown tickers, positivity, drift over many steps, Cholesky rebuilds and the async lifecycle. Guidelines:

- Test statistical properties with generous tolerances over many steps (e.g. sample correlation of AAPL/MSFT log-returns over 20k steps is within 0.6 ± 0.05), never exact paths.
- Use `event_probability=0` for tests about GBM, and `event_probability=1` to test events deterministically.
- **CHANGE — injectable RNG.** `step()` uses the global `np.random` and `random` modules, so tests can't reproduce a path without reseeding global state. Accept `rng: np.random.Generator | None = None` in `GBMSimulator.__init__` (default `np.random.default_rng()`) and use it for normals, uniform seed prices, event checks and event sizes. Tests then pass `np.random.default_rng(42)` for exact, repeatable paths. Behaviour in production is unchanged.

---

## 6. Optional Improvements (not required)

| Idea | Value | Cost |
|---|---|---|
| Injectable RNG (above) | Reproducible tests | Small — recommended |
| Warm start from last trade price in DB for held tickers | Avoids P&L jump after restart | Small: pass `{ticker: last_trade_price}` overrides into `start()` |
| Common "market" factor instead of rule table | More realistic beta structure | Medium; current rules are good enough |
| `SIM_SPEED` multiplier on `dt` | Livelier demos (e.g. ×10 ≈ 3% moves per 30 min) | Trivial; keep default 1 |
| Event correlation (sector-wide shocks) | Drama on the heatmap | Small |

Keep the simulator boring in structure: anything added must preserve the `MarketDataSource` contract and the 500 ms cadence the frontend expects.
