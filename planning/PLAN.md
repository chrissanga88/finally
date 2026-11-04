# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel, open by default, ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving        │
│                      (Next.js export)           │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim       │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: Official Anthropic Python SDK calling Claude Haiku 5.5 (`claude-haiku-5-5`), with Pydantic Structured Outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |
| Anthropic SDK + Claude Haiku 5.5 | One official SDK, no proxy layer (no LiteLLM/OpenRouter); Haiku is fast and cheap enough that a loading spinner beats token streaming; native Pydantic Structured Outputs (`messages.parse`) give validated trade instructions with no hand-written JSON parsing |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── app/db/               # Schema definitions, seed data, init logic (package app.db)
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; *.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── .env                      # Environment variables (gitignored)
├── .env.example              # Committed template listing every variable
└── .gitignore                # Must include .env, db/*.db and backend/static/
```

There is deliberately **no root `docker-compose.yml`** — the start/stop scripts and the single `docker run` command (Section 12) cover running the app. The only compose file is `test/docker-compose.test.yml`.

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/app/db/`** (imported as `app.db`) contains schema SQL definitions and seed logic. The backend initializes the database **at startup** (FastAPI lifespan) — see Section 7. Schema code must **not** live in a top-level `backend/db/`: in Docker, `backend/` is copied to `/app`, and `/app/db/` is reserved for the SQLite volume mount, which would shadow any code there.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (`docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

### Local Development (without Docker)

- Backend: `cd backend && uv run uvicorn app.main:app --reload --port 8000`
- Frontend: `cd frontend && npm run dev` (port 3000). `next.config` sets `output: 'export'` **only when `NODE_ENV === 'production'`**, and defines `rewrites()` (proxying `/api/*` to `http://localhost:8000/api/*`) **only in dev**, so Next never sees both together. The browser still sees a single origin and no CORS configuration is needed.
- If the SSE stream arrives buffered or in bursts through the dev proxy, the frontend may read an optional `NEXT_PUBLIC_API_BASE` (e.g. `http://localhost:8000`) for the `EventSource` URL only, and the backend adds a CORS allowance for `http://localhost:3000` **only when running in dev**. In production `NEXT_PUBLIC_API_BASE` is unset and everything is same-origin.

---

## 5. Environment Variables

```bash
# Required for real AI chat (Claude Haiku 5.5). The app runs without it: chat returns 503, or use LLM_MOCK=true
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false

# Optional: SQLite file location. Defaults to <project root>/db/finally.db locally;
# the Dockerfile sets it to /app/db/finally.db
DB_PATH=
```

`.env.example` (committed) mirrors this block with empty/placeholder values.

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (Section 10), and `ANTHROPIC_API_KEY` is not required
- If `LLM_MOCK` is not `true` and `ANTHROPIC_API_KEY` is missing → the app still starts; `POST /api/chat` returns `503` with error code `LLM_UNAVAILABLE`
- Locally, the backend loads `.env` from the **project root** (one level above `backend/`) using `python-dotenv`. In Docker, variables are provided via `--env-file .env`.

---

## 6. Market Data

> **Status: implemented.** See `planning/MARKET_DATA_SUMMARY.md` for the module layout and usage. Items below marked **(TODO)** are small follow-up changes still required.

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface (`MarketDataSource`). The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.); unknown tickers get a random seed price ($50–$300) and default drift/volatility

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all tracked tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Which Tickers Are Tracked

The market data source tracks **`watchlist ∪ tickers with open positions`**. The watchlist is a UI concept; removing a ticker from the watchlist must not stop pricing a held position. Backend responsibilities:

- On startup: `source.start(watchlist ∪ positions)`
- On watchlist add or buy of a new ticker: `source.add_ticker(t)`
- On watchlist remove: `source.remove_ticker(t)` **only if** no position is held
- On a sell that closes a position: `source.remove_ticker(t)` **only if** the ticker is not on the watchlist

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, timestamp, and session open price for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### Session Change % (TODO)

The watchlist shows **session change %**: change since the backend first priced the ticker (since process start, or since the ticker was added). This replaces "daily change %", which has no meaning for the simulator. The existing `change_percent` field is tick-over-tick and should not be used for this display.

Required change: `PriceCache` records the first price seen per ticker as `session_open`, and `PriceUpdate.to_dict()` adds `session_open` and `session_change_percent`. For Massive, the previous close may be used as `session_open` if readily available in the response.

Also required: `PriceCache.remove()` must bump the cache `version`, so the SSE stream re-emits promptly after a ticker stops being tracked (otherwise, in Massive mode, a removed ticker lingers in the stream for up to one poll interval).

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- The server first sends `retry: 1000` (browser reconnects 1s after a drop)
- The server checks the cache every ~500ms and, **whenever the cache has changed**, sends one unnamed `data:` event containing a JSON map of **all** tracked tickers:

```json
{
  "AAPL": {
    "ticker": "AAPL",
    "price": 191.23,
    "previous_price": 191.10,
    "timestamp": 1730563200.512,
    "change": 0.13,
    "change_percent": 0.068,
    "direction": "up",
    "session_open": 190.00,
    "session_change_percent": 0.647
  },
  "GOOGL": { "...": "..." }
}
```

- `timestamp` is Unix seconds (float). `direction` is `"up" | "down" | "flat"`.
- Because each event carries every ticker, the frontend must compare each ticker's `timestamp` with the last one it holds and only append chart points for tickers whose `timestamp` changed (important in Massive mode, where most events repeat unchanged prices). **Flashing** is triggered only when `price` differs from the last price held (green if higher, red if lower); a new timestamp with an unchanged price (a `"flat"` tick, common because prices are rounded to 2 dp) does not flash.
- The map contains `watchlist ∪ positions`, so it may include held tickers that are not on the watchlist. The frontend uses it as a price lookup only; which rows appear in the watchlist comes from `/api/watchlist`.

---

## 7. Database

### SQLite, Initialized at Startup

On startup (FastAPI lifespan), the backend opens the SQLite file at `DB_PATH`, runs `CREATE TABLE IF NOT EXISTS` for every table, and seeds default data if the `users_profile` table is empty. Initializing at startup (not on first request) avoids races between concurrent first requests and gives the market data source the watchlist it needs before serving. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

All DB access goes through the `app.db` module using the standard-library `sqlite3`, opening a short-lived connection per operation (or per request). Do not share one connection across threads. Each trade's transaction runs on a single connection.

### Conventions

- Every table except `users_profile` has a `user_id` column defaulting to `"default"` (in `users_profile`, `id` *is* the user id). This is hardcoded for now (single-user) but enables future multi-user support without schema migration.
- Timestamps are stored as ISO 8601 UTC strings. **The API always returns timestamps as Unix seconds (float)**, matching the SSE stream and what charting libraries expect.
- Money is stored as REAL; cash balances and trade values are rounded to 2 decimal places (cents) when written. Quantities are rounded to 4 decimal places.

### Schema

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT

**watchlist** — Tickers the user is watching
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT
- PRIMARY KEY `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user; row deleted when quantity reaches 0)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT
- PRIMARY KEY `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL
- `price` REAL
- `executed_at` TEXT

**portfolio_snapshots** — Portfolio value over time (for P&L chart). See "Snapshot Policy" below.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — the executed-actions object defined in Section 8; null for user messages)
- `created_at` TEXT

Composite primary keys on `watchlist` and `positions` make upserts trivial (`INSERT ... ON CONFLICT(user_id, ticker) DO UPDATE`). Append-only tables keep UUID keys.

### Snapshot Policy (no background task)

A snapshot is recorded:
1. Immediately after every executed trade, and
2. Whenever `GET /api/portfolio` or `GET /api/portfolio/history` is called and the latest snapshot is more than **25 seconds** old. The threshold is deliberately below the 30-second refetch interval so timer jitter doesn't make the frontend skip every other point.

The stale-check and insert run together in a single `BEGIN IMMEDIATE` transaction (re-checking the latest snapshot inside it), so the two concurrent reads the frontend makes on each refetch can't both write a snapshot.

The frontend refetches `/api/portfolio` every 30 seconds while open (Section 11), so the P&L chart gets a point roughly every 30s while the app is in use, with no extra background task to manage. No snapshots accumulate while no browser is open.

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX
- One initial portfolio snapshot of `10000.0`

---

## 8. API Endpoints

### Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates (Section 6) |
| GET | `/api/portfolio` | Cash, positions with live P&L, total value |
| POST | `/api/portfolio/trade` | Execute a market order |
| GET | `/api/portfolio/history` | Portfolio value snapshots (P&L chart) |
| GET | `/api/watchlist` | Watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |
| POST | `/api/chat` | Send a message, receive the assistant reply and executed actions |
| GET | `/api/chat/history` | Recent chat messages (to repopulate the panel after refresh) |
| GET | `/api/health` | Health check |

### Error Shape

All errors use one shape with an appropriate status code (`400` validation, `404` not found, `500` unexpected, `502` LLM call failed, `503` LLM unavailable):

```json
{ "error": "Insufficient cash: need $1,912.30, have $500.00", "code": "INSUFFICIENT_CASH" }
```

Error codes: `INVALID_TICKER`, `INVALID_QUANTITY`, `NO_PRICE`, `INSUFFICIENT_CASH`, `INSUFFICIENT_SHARES`, `NOT_FOUND`, `VALIDATION_ERROR`, `LLM_UNAVAILABLE`, `LLM_ERROR` (`502`), `INTERNAL_ERROR` (`500`).

FastAPI's default `422` response for malformed request bodies does not match this shape. The backend registers a `RequestValidationError` handler that returns `400` with code `VALIDATION_ERROR` (e.g. missing `side`, `side: "short"`, `quantity` sent as a non-number), and a catch-all exception handler that returns `500` with code `INTERNAL_ERROR`. Domain checks (ticker format, quantity `> 0`) still return their specific codes.

### Ticker Rules

All tickers are uppercased and trimmed, then must match `^[A-Z][A-Z.]{0,5}$`, else `400 INVALID_TICKER`. In simulator mode any valid-looking symbol is accepted (it will get simulated prices). In Massive mode it is accepted and shows no price until the first successful poll.

### Response Shapes

**`GET /api/portfolio`**
```json
{
  "cash_balance": 8087.70,
  "total_value": 10012.50,
  "positions_value": 1924.80,
  "unrealized_pnl": 12.50,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10,
      "avg_cost": 191.23,
      "current_price": 192.48,
      "market_value": 1924.80,
      "unrealized_pnl": 12.50,
      "unrealized_pnl_percent": 0.65,
      "weight": 0.192
    }
  ]
}
```
If a held ticker has no cached price, `current_price` falls back to `avg_cost`.

Units: fields named `*_percent` are in percent (`0.65` means 0.65%). `weight` is a fraction (`0.192` means 19.2%) and is `market_value / total_value`, where `total_value` includes cash, so position weights sum to less than 1.

**`POST /api/portfolio/trade`** — request `{"ticker": "AAPL", "side": "buy", "quantity": 10}`
```json
{
  "trade": { "id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": 191.23, "executed_at": 1730563200.5 },
  "cash_balance": 8087.70
}
```
The frontend refetches `/api/portfolio` after a successful trade.

**`GET /api/portfolio/history`** — snapshots from the last 24 hours, at most 1,000 points (evenly downsampled if more), oldest first:
```json
{ "snapshots": [ { "timestamp": 1730563200.0, "total_value": 10000.0 } ] }
```

**`GET /api/watchlist`**
```json
{ "tickers": [ { "ticker": "AAPL", "price": 191.23, "session_change_percent": 0.65, "added_at": 1730563200.0 } ] }
```
`price` and `session_change_percent` are `null` if not yet priced.

**`POST /api/watchlist`** — request `{"ticker": "PYPL"}` → `201` with the new entry (same shape as an item above). Adding an existing ticker is a no-op returning `200`.

**`DELETE /api/watchlist/{ticker}`** → `204`. `404 NOT_FOUND` if not on the watchlist.

**`POST /api/chat`** — request `{"message": "Buy 10 AAPL"}`
```json
{
  "id": "uuid",
  "role": "assistant",
  "content": "Buying 10 shares of AAPL at market.",
  "created_at": 1730563200.0,
  "actions": {
    "trades": [
      { "ticker": "AAPL", "side": "buy", "quantity": 10, "status": "executed", "price": 191.23 },
      { "ticker": "TSLA", "side": "buy", "quantity": 1000, "status": "failed", "error": "Insufficient cash", "code": "INSUFFICIENT_CASH" }
    ],
    "watchlist_changes": [
      { "ticker": "PYPL", "action": "add", "status": "executed" }
    ]
  }
}
```
The same `actions` object is what is stored in `chat_messages.actions`.

**`GET /api/chat/history`** — the last 50 messages, oldest first: `{"messages": [ <message objects as above; user messages have actions: null> ]}`

**`GET /api/health`** → `{"status": "ok"}`

---

## 9. Portfolio & Trade Rules

These rules apply identically to manual trades and LLM-initiated trades (one shared function).

- `quantity` is first rounded: `qty = round(qty, 4)`. It must still be `> 0` after rounding (so `0.00001` becomes `0` and fails), otherwise `400 INVALID_QUANTITY`.
- Fill price is the current cached price. If the ticker has no cached price, `400 NO_PRICE`.
- Trading a ticker that is not tracked: a **buy** adds it to the watchlist (and the market data source) first; if no price is available yet, the buy fails with `NO_PRICE`. The watchlist add **persists** even when the buy then fails for any reason (no rollback). In Massive mode, a buy of a brand-new ticker therefore always fails `NO_PRICE` until the next poll (up to ~15 s); this is expected, and the user can retry. A **sell** of a ticker with no position fails with `INSUFFICIENT_SHARES`.
- Trade value: `cost = round(qty × price, 2)`. Cash is updated as `cash = round(cash − cost, 2)` for a buy and `cash = round(cash + cost, 2)` for a sell.
- **Buy**: requires `cash_balance >= cost`, else `INSUFFICIENT_CASH`. New `avg_cost` = weighted average: `(old_qty × old_avg + qty × price) / (old_qty + qty)`.
- **Sell**: requires `position.quantity >= quantity`, else `INSUFFICIENT_SHARES`. `avg_cost` is unchanged. If remaining quantity is below `0.0001`, the position row is deleted.
- Each trade is a single DB transaction: update cash, upsert/delete position, append to `trades`, record a snapshot.
- Realized P&L is **out of scope** (not tracked or displayed).

---

## 10. LLM Integration

When writing code that calls the LLM, use the **`claude-haiku` skill** (`.claude/skills/claude-haiku/SKILL.md`): the official Anthropic Python SDK, model `claude-haiku-5-5`, `output_config={"effort": "low"}`, and Structured Outputs via `client.messages.parse(..., output_format=<Pydantic model>)`. No LiteLLM, no OpenRouter.

The backend's `pyproject.toml` must include `anthropic`, `pydantic` and `python-dotenv`. `ANTHROPIC_API_KEY` must be set in the project-root `.env` (see Section 5).

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the last 20 messages from the `chat_messages` table (including each assistant message's stored `actions`, so the model sees previous successes/failures). An assistant turn's `content` sent to the API is its stored `content` followed by `\n\n[Executed actions: <compact JSON of actions>]` when `actions` is non-null.
3. Builds the request: a `system` prompt (instructions + current portfolio context as JSON) and a `messages` list of prior turns plus the new user message. Per the skill's rules, `messages` must alternate `user`/`assistant`, start with `user`, and must not end with an assistant prefill. The history builder enforces this defensively: drop leading `assistant` messages from the window, and merge consecutive same-role messages (joined with a blank line).
4. Calls `await client.messages.parse()` on an **`AsyncAnthropic`** client with the Pydantic response model below. The synchronous `Anthropic` client must not be called from an `async def` route: it would block the event loop and stall every SSE stream and the simulator for the duration of the call.
5. Uses `response.parsed_output`; if `stop_reason` is `"refusal"` or `"max_tokens"`, or the call raises, returns `LLM_ERROR` (`502`) and stores **nothing** (neither the user nor an assistant message), so history never contains an unanswered user turn. The frontend keeps the failed message visible locally with the error so the user can resend it.
6. Auto-executes **`watchlist_changes` first, then `trades`**, each list in order, via the same functions as the manual endpoints, collecting each result (executed or failed with error). Watchlist-first means "watch PYPL and buy 5" works in one turn. One failed action does not stop the rest.
7. Stores the user message and the assistant message (with the executed-actions object) in `chat_messages`
8. Returns the assistant message and actions to the frontend (shape in Section 8). No token streaming — Haiku is fast enough that a loading indicator is sufficient.

### Structured Output Schema

```python
class TradeRequest(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float

class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]

class ChatResponse(BaseModel):
    message: str                                  # conversational text shown to the user
    trades: list[TradeRequest] = []               # trades to auto-execute
    watchlist_changes: list[WatchlistChange] = [] # watchlist modifications
```

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

The LLM writes its `message` **before** trades execute, so it cannot describe failures in the same turn. Instead, failed actions are shown inline in the chat UI next to the assistant's message (e.g. "✗ Buy 1000 TSLA — Insufficient cash"), and are persisted in `actions` so the LLM sees them in history on the next turn. There is no second LLM call.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees (phrase the message as intent, e.g. "Buying 10 AAPL", since execution happens after)
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Only use tickers in valid symbol format

### LLM Mock Mode

When `LLM_MOCK=true`, the backend skips the Anthropic call and returns a deterministic `ChatResponse` based on simple keyword matching on the lowercased user message. Rules are checked top to bottom and the **first match wins**; matching is a plain substring check (so "buy and watch TSLA" hits only the `buy` rule):

| User message contains | Mock response |
|---|---|
| `buy` | message `"Mock: buying 1 share of AAPL."`, trades `[{AAPL, buy, 1}]` |
| `sell` | message `"Mock: selling 1 share of AAPL."`, trades `[{AAPL, sell, 1}]` |
| `watch` | message `"Mock: adding PYPL to your watchlist."`, watchlist_changes `[{PYPL, add}]` |
| anything else | message `"Mock: I'm FinAlly, your AI trading assistant."`, no actions |

The mock response then flows through steps 6–8 unchanged, so execution, persistence and the API shape are fully exercised. This enables fast, free, reproducible E2E tests, development without an API key, and CI.

---

## 11. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), session change % (`session_change_percent` from SSE), a sparkline mini-chart (accumulated from SSE since page load), and a remove control. An input to add a ticker.
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss, intensity scaled by `unrealized_pnl_percent`)
- **P&L chart** — line chart showing total portfolio value over time, from `/api/portfolio/history`
- **Positions table** — ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — ticker field, quantity field, buy button, sell button. Market orders, instant fill. Errors from the API are shown inline.
- **AI chat panel** — docked, collapsible sidebar, **open by default**. Message input, scrolling conversation history (loaded from `/api/chat/history` on page load), loading indicator while waiting. Each assistant message shows its executed actions inline as confirmations (✓) or failures (✗ with error).
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Data Flow

- **Prices**: one `EventSource` on `/api/stream/prices`. Append chart points only for tickers whose `timestamp` changed, and flash only when the price changed (Section 6).
- **Connection status**: `onopen` → green; `onerror` while `readyState === CONNECTING` → yellow; `readyState === CLOSED` → red.
- **Portfolio**: fetch `/api/portfolio` on load, after every trade, after any chat response containing actions, and every 30 seconds.
- **Header total value** is computed on the frontend: `cash_balance + Σ(position.quantity × latest SSE price)`, so it updates live between portfolio fetches. Positions table and heatmap use the same live prices.
- **Watchlist**: fetch `/api/watchlist` on load and after any add/remove (manual or via chat actions). The watchlist rows shown are exactly the tickers from this endpoint; SSE supplies their live prices but never adds or removes rows.
- **P&L chart**: fetch `/api/portfolio/history` on load and whenever the portfolio is refetched.

### Buffers

Keep **one price buffer per tracked ticker** (every ticker in the SSE map, not only the selected one), capped at **3,600 points**; drop the oldest points beyond that. The main chart renders the selected ticker's full buffer, so switching tickers shows history immediately; sparklines render the last **300 points** of the same buffer. Buffers start empty on page load (there is no price-history endpoint), so the main chart fills in progressively too.

Times are floored to whole seconds before being added (Lightweight Charts requires strictly ascending `UTCTimestamp` values in seconds). Use `series.update()`, which replaces the last point when its time equals the new one, so at most one point is kept per second, and 3,600 points covers ~60 minutes.

### Technical Notes

- **Charting**: use **Lightweight Charts** (TradingView, canvas-based) for the main price chart, sparklines and P&L chart. For the treemap, use **`d3-hierarchy`**'s `treemap()` layout to compute rectangles and render them as absolutely positioned `<div>`s. Do not add other chart libraries. Lightweight Charts touches `window`, so chart components must be client-only (`"use client"` and create the chart inside `useEffect`, or load with `dynamic(..., { ssr: false })`), otherwise `next build` fails during prerender.
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme
- `next.config`: `output: 'export'` (production only), `images: { unoptimized: true }` (required for static export if `next/image` is used), and the dev-only `/api` rewrite (Section 4)

---

## 12. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm ci && npm run build (produces static export in out/)

Stage 2: Python 3.12 slim
  - Install uv
  - WORKDIR /app
  - Copy backend/pyproject.toml and backend/uv.lock first (layer caching)
  - uv sync --frozen --no-dev --no-install-project
  - Copy the rest of backend/ into /app, then uv sync --frozen --no-dev
  - Copy frontend build output (out/) into /app/static/
  - ENV PATH="/app/.venv/bin:$PATH"   (so uvicorn is found)
  - ENV DB_PATH=/app/db/finally.db
  - Expose port 8000
  - CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

FastAPI registers all `/api` routers first, then mounts `StaticFiles(directory=<static dir>, html=True)` at `/` **last**, so the static catch-all never shadows API routes. The static dir is resolved relative to the package (`Path(__file__).resolve().parent.parent / "static"`, i.e. `backend/static/`), not the working directory, and is mounted **only if it exists**, since `StaticFiles` raises at startup on a missing directory and local backend dev has no frontend build. `backend/static/` is gitignored.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- If `.env` is missing, copies `.env.example` to `.env` and prints a warning (the app runs without any keys: simulator prices, and chat returns `503` unless `LLM_MOCK=true`)
- Runs the container (named `finally`) with the volume mount, port mapping, and `.env` file; if a container named `finally` already exists, removes it first
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts must be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 13. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)** — the main investment:
- Market data: already covered (73 tests, see `MARKET_DATA_SUMMARY.md`); add tests for `session_open` / `session_change_percent`
- Portfolio: trade execution, weighted avg cost, position deletion at zero, edge cases (selling more than owned, insufficient cash, no price, invalid quantity, selling at a loss)
- Tracked tickers: watchlist removal keeps pricing a held ticker; closing a position stops pricing a ticker not on the watchlist
- Snapshots: recorded after trades and when stale on portfolio reads; history downsampling
- LLM: Pydantic parsing of valid responses, handling of refusal / max_tokens / exceptions, mixed success/failure action results, mock mode keyword mapping
- API routes: status codes, response shapes and error shape from Section 8

**Frontend (Vitest)** — limited to pure logic, no component-rendering tests:
- Portfolio calculations (live total value, P&L, weights)
- SSE change detection (only changed tickers flash/append) and buffer capping
- Connection-status state derivation from `EventSource` events

### E2E Tests (in `test/`)

**Infrastructure**: `test/docker-compose.test.yml` spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true`.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send "buy", receive the mock response, executed trade appears inline and in the positions table

SSE reconnection is browser-native `EventSource` behavior; it is covered by the frontend connection-status unit test rather than an E2E test.

---

## 14. Build Checklist (repo hygiene)

Easy-to-miss items that agents must create:

- [ ] `db/.gitkeep`
- [ ] `.gitignore` entries for `db/*.db` and `backend/static/` (`.env` is already ignored)
- [ ] `.env.example` matching Section 5
- [ ] `backend` dependencies: `anthropic`, `pydantic`, `python-dotenv`, `fastapi`, `uvicorn`
- [ ] Market data follow-up: `session_open` / `session_change_percent`, and `PriceCache.remove()` bumps `version` (Section 6)
- [ ] `README.md` matches this plan (e.g. database is initialized at startup, not lazily)
