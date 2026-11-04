# Massive API — Stock Prices Reference

Massive (formerly Polygon.io) provides US stock market data over REST and WebSocket. This document covers what FinAlly needs: **current prices for multiple tickers** and **end-of-day (EOD) prices**, via REST polling and the official Python SDK.

Researched November 2026 against the live docs (`https://massive.com/docs`) and the SDK source (`github.com/massive-com/client-python`). Items marked *(inferred)* are deduced from sample responses rather than stated in the docs.

---

## 1. Basics

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (`https://api.polygon.io` still works during the transition) |
| Auth | `?apiKey=<key>` query param **or** `Authorization: Bearer <key>` header |
| Python SDK | `pip install massive` / `uv add massive` — `from massive import RESTClient` (Python ≥ 3.10) |
| SDK key env var | `MASSIVE_API_KEY` (read by `RESTClient()` as its default `api_key`) |
| Existing Polygon keys | Continue to work unchanged |

### SDK client

```python
from massive import RESTClient

client = RESTClient(
    api_key="...",          # default: os.getenv("MASSIVE_API_KEY") — read at import time
    connect_timeout=10.0,
    read_timeout=10.0,
    retries=3,              # urllib3 retry, backoff 0.1s; retries 413/429/499/500/502/503/504
    pagination=True,        # list_* methods follow next_url automatically
)
```

- The SDK is **synchronous** (urllib3). In an asyncio app, call it via `asyncio.to_thread(...)`.
- Non-200 responses raise `massive.exceptions.BadResponse` (message = response body). A missing key raises `AuthError` at construction.
- Because `api_key`'s default is captured **when `massive` is imported**, pass the key explicitly if you load `.env` after imports.

---

## 2. Plans, Limits and Data Recency

| Plan | Price | Rate limit | Stock data recency | Snapshots | History |
|---|---|---|---|---|---|
| **Stocks Basic** | $0 | **5 calls/min** (429 on excess) | **End-of-day only** | **Not included** | 2 years |
| Stocks Starter | paid | Unlimited* | 15-min delayed | Included | 5 years |
| Stocks Developer | ~$79/mo | Unlimited* | 15-min delayed | Included | 10 years |
| Stocks Advanced | $199/mo | Unlimited* | Real-time | Included | 20+ years |
| Stocks Business | ~$2,499/mo | Unlimited | Real-time | Included (+ FMV) | All |

\* Massive states paying customers get unlimited REST requests; stay under ~100 req/s as a courtesy.

**Consequences for FinAlly:**

- On the **free plan** the snapshot endpoints return **403 (NOT_AUTHORIZED)**. Only aggregate (bar) endpoints are available, and only up to the **previous trading day** — a trading day's bars become available **after midnight ET**. Free-plan "live" prices are therefore yesterday's close and don't move intraday.
- On **Starter/Developer** the snapshot gives 15-minute-delayed prices; on **Advanced+** real-time.
- `lastTrade` / `lastQuote` are present in snapshots **only if the plan includes trades/quotes**; otherwise use `min` (latest minute bar) or `day`.

---

## 3. Endpoints for Multiple Tickers

### 3.1 Full Market Snapshot — current prices, many tickers, one call (paid plans)

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA`

| Param | Notes |
|---|---|
| `tickers` | Comma-separated, **case-sensitive**. Omit for the whole market (~10k tickers). |
| `include_otc` | Default `false` |

This is the primary endpoint for FinAlly's poller: **one request returns every tracked ticker**, so a 15 s poll is 4 req/min regardless of watchlist size.

Response (abridged; one element of `tickers[]`):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [{
    "ticker": "AAPL",
    "todaysChange": 0.98,
    "todaysChangePerc": 0.82,
    "updated": 1605195918306274000,
    "day":     {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725},
    "min":     {"o": 120.435, "h": 120.468, "l": 120.37, "c": 120.4201, "v": 270796, "t": 1684428720000},
    "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49, "v": 110597265, "vw": 118.4998},
    "lastTrade": {"p": 120.47, "s": 236, "x": 10, "t": 1605195918306274000},
    "lastQuote": {"p": 120.46, "s": 8, "P": 120.47, "S": 4, "t": 1605195918507251700}
  }]
}
```

**Timestamp units differ by field** *(inferred from samples)*:

| Field | Units |
|---|---|
| `updated`, `lastTrade.t`, `lastQuote.t` | **nanoseconds** (19 digits) |
| `min.t`, aggregate `t` | **milliseconds** (13 digits) |

SDK mapping (`TickerSnapshot`):

| JSON | SDK attribute |
|---|---|
| `lastTrade` | `snap.last_trade` → `LastTrade` with `.price` (`p`), **`.sip_timestamp`** (`t`, ns), `.size`, `.exchange` |
| `lastQuote` | `snap.last_quote` |
| `min` | `snap.min` → `MinuteSnapshot` with `.close`, `.timestamp` (ms) |
| `day` / `prevDay` | `snap.day` / `snap.prev_day` → `Agg` with `.open/.high/.low/.close/.volume/.vwap` |
| `todaysChange` / `todaysChangePerc` | `snap.todays_change` / `snap.todays_change_percent` (percent units) |
| `updated` | `snap.updated` (ns) |

> ⚠️ `LastTrade` has **no `timestamp` attribute** — use `sip_timestamp` (nanoseconds). Any field may be `None` (e.g. `last_trade` on plans without trades, `min` before the open).

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=key)
snaps = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,   # or "stocks"
    tickers=["AAPL", "MSFT", "TSLA"],        # list or comma-separated string
)

for s in snaps:
    if s.last_trade and s.last_trade.price:
        price, ts = s.last_trade.price, s.last_trade.sip_timestamp / 1e9
    elif s.min and s.min.close:
        price, ts = s.min.close, s.min.timestamp / 1e3
    elif s.day and s.day.close:
        price, ts = s.day.close, s.updated / 1e9 if s.updated else None
    else:
        continue  # no trades yet
    prev_close = s.prev_day.close if s.prev_day else None
    print(s.ticker, price, ts, prev_close, s.todays_change_percent)
```

### 3.2 Unified Snapshot (v3) — alternative, up to 250 tickers (paid plans)

`GET /v3/snapshot?ticker.any_of=AAPL,MSFT&limit=250`

Newer, cross-asset format with snake_case keys and a `session` object (`price`, `change`, `change_percent`, `open`, `high`, `low`, `close`, `previous_close`, `volume`, `last_updated`). Unknown tickers come back as `{"ticker": "...", "error": "NOT_FOUND", "message": "Ticker not found."}` rather than being silently dropped. Same plan availability as 3.1. Max 250 tickers per call; default `limit` is **10**, so always pass `limit`.

```python
for s in client.list_universal_snapshots(ticker_any_of=["AAPL", "MSFT"], limit=250):
    if s.error:
        print(s.ticker, "error:", s.message)
        continue
    sess = s.session
    price = (s.last_trade.price if s.last_trade else None) or (sess.price if sess else None) \
        or (sess.close if sess else None)
    print(s.ticker, price, sess.previous_close if sess else None)
```

FinAlly uses 3.1 (one flat list, simpler model); 3.2 is a drop-in alternative if per-ticker errors are wanted.

### 3.3 Daily Market Summary (Grouped Daily) — EOD for **all** tickers, one call (all plans incl. free)

`GET /v2/aggs/grouped/locale/us/market/stocks/{YYYY-MM-DD}?adjusted=true`

Returns one OHLCV bar per US stock for the date. **The only efficient multi-ticker endpoint on the free plan**: one call covers every ticker; filter client-side.

```json
{"status": "OK", "resultsCount": 10234, "results": [
  {"T": "AAPL", "o": 189.1, "h": 191.0, "l": 188.4, "c": 190.6, "v": 51234567, "vw": 190.1, "n": 612345, "t": 1730505600000}
]}
```

`t` = Unix **milliseconds**. On non-trading days `results` is empty (or absent); on the free plan, requesting today (before midnight ET) returns **403**.

```python
from datetime import date, timedelta

def last_eod_closes(client, tickers: set[str], max_lookback: int = 7) -> dict[str, float]:
    """Most recent available daily close for each ticker (walks back over weekends/holidays)."""
    d = date.today() - timedelta(days=1)
    for _ in range(max_lookback):
        bars = client.get_grouped_daily_aggs(date=d.isoformat(), adjusted=True)
        if bars:
            return {b.ticker: b.close for b in bars if b.ticker in tickers}
        d -= timedelta(days=1)
    return {}
```

Each lookback step costs one call — budget against the 5/min free limit (a Monday morning fetch walks Sun → Sat → Fri = 3 calls). Use `GET /v1/marketstatus/upcoming` or a simple weekday skip to avoid wasted calls.

### 3.4 Previous Day Bar — EOD for one ticker (all plans)

`GET /v2/aggs/ticker/{ticker}/prev?adjusted=true` → `results[0]` with `T, o, h, l, c, v, vw, t(ms)`.

```python
prev = client.get_previous_close_agg("AAPL")   # SDK returns list[PreviousCloseAgg] or object depending on version
bar = prev[0] if isinstance(prev, list) else prev
print(bar.close, bar.timestamp)
```

One call per ticker — fine for paid plans or a single newly added ticker; avoid for whole watchlists on free (10 tickers = 2 minutes of quota). The SDK README shows a `ThreadPoolExecutor` pattern for fanning these out on paid plans.

### 3.5 Daily Ticker Summary (Open/Close) — one ticker, one date

`GET /v1/open-close/{ticker}/{date}` → `open, high, low, close, volume, preMarket, afterHours`. SDK: `client.get_daily_open_close_agg("AAPL", "2026-10-30")`. Useful when after-hours prices matter; otherwise 3.3 is cheaper.

### 3.6 Historical bars — one ticker, a range

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` (timespan: `minute|hour|day|week|...`).

```python
for bar in client.list_aggs("AAPL", 1, "day", "2026-10-01", "2026-10-31", limit=50000):
    print(bar.timestamp, bar.close)          # timestamp in ms
```

Not needed by FinAlly today (charts accumulate from the live stream), but this is the endpoint for seeding a chart with history later.

### 3.7 Single-ticker last trade / snapshot

`GET /v2/last/trade/{ticker}` (`client.get_last_trade("AAPL")`) and `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` (`client.get_snapshot_ticker("stocks", "AAPL")`). Paid plans only; one call per ticker — prefer 3.1.

### 3.8 Market status

`GET /v1/marketstatus/now` (`client.get_market_status()`) → `market: "open" | "closed" | "extended-hours"` plus per-exchange status. Lets a poller slow down outside market hours, when prices don't change.

---

## 4. Raw HTTP (no SDK)

```python
import httpx

async def snapshot(tickers: list[str], key: str) -> list[dict]:
    async with httpx.AsyncClient(base_url="https://api.massive.com", timeout=10) as http:
        r = await http.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers)},
            headers={"Authorization": f"Bearer {key}"},
        )
        r.raise_for_status()
        return r.json().get("tickers", [])
```

An async HTTP client avoids `to_thread`; the SDK is preferred in FinAlly because it's already a dependency and handles retries.

---

## 5. Errors

| Status | Meaning | Handling |
|---|---|---|
| 401 | Missing/invalid key | Log once loudly; keep retrying slowly (key won't fix itself, but don't crash) |
| 403 `NOT_AUTHORIZED` | Endpoint or date not in your plan (e.g. snapshot on free; today's bar on free) | Switch strategy (see MARKET_INTERFACE.md §5) |
| 429 | Rate limit (free: 5/min) | SDK retries with short backoff; poller should also back off |
| 5xx | Server issue | Retry next interval |

Unknown tickers in the v2 snapshot are simply **absent** from `tickers[]` (no error). In v3 they appear with `error: "NOT_FOUND"`.

---

## 6. Practical Notes

- Tickers are **case-sensitive** — always uppercase.
- Snapshot data is cleared ~3:30 AM ET and repopulates from ~4:00 AM ET (pre-market). Between those times prices/`day` may be empty — fall back to `prev_day.close`.
- Outside market hours the snapshot keeps returning the last trade; prices simply stop changing.
- `todaysChangePerc` is already a percent (0.82 = 0.82%), measured against `prevDay.c`.
- Prices can have more than 2 decimals (`120.4229`); round for display.

## Sources

- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot.md) · [Single Ticker Snapshot](https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot.md) · [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot.md)
- [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary.md) · [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar.md) · [Docs index](https://massive.com/docs/llms.txt)
- [Pricing](https://massive.com/pricing) · [KB: REST request limits](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis) · [KB: Basic plan previous-day data](https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data)
- [massive-com/client-python](https://github.com/massive-com/client-python) — `rest/__init__.py`, `rest/base.py`, `rest/snapshot.py`, `rest/aggs.py`, `rest/models/{snapshot,trades,aggs}.py`
