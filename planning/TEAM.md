# FinAlly — Agent Team Contract

Shared contract for the build team. `PLAN.md` is the spec; this file defines **who owns which files** and **the interfaces between owners**. If you need to change an interface below, edit this file and message every affected teammate.

## Team & File Ownership

Only edit files you own. If you need a change in someone else's files, message the owner (SendMessage) with the exact request.

| Member (agent name) | Owns |
|---|---|
| `db-engineer` | `backend/app/db/**`, `backend/tests/db/**` |
| `backend-engineer` | `backend/app/main.py`, `backend/app/config.py`, `backend/app/errors.py`, `backend/app/api/**` (except `chat.py`), `backend/app/portfolio/**`, `backend/app/tracking.py`, `backend/tests/api/**`, `backend/tests/portfolio/**`, `backend/tests/conftest.py`, `backend/pyproject.toml` + `uv.lock` (others ask to add deps). `backend/app/market/**` only for bug fixes. |
| `llm-engineer` | `backend/app/llm/**`, `backend/app/api/chat.py`, `backend/tests/llm/**` |
| `frontend-engineer` | `frontend/**` |
| `devops-engineer` | `Dockerfile`, `.dockerignore`, `scripts/**`, `.env.example`, `.gitignore`, `db/.gitkeep`, `README.md`, `test/docker-compose.test.yml`, `.github/workflows/**` |
| `integration-tester` | `test/**` (except `docker-compose.test.yml`; coordinate with devops-engineer), `test/package.json`, `playwright.config.ts` |

## Backend Interfaces

### `app.db` (db-engineer)
Stdlib `sqlite3`, short-lived connection per operation. All functions take `user_id: str = "default"`. Timestamps are stored as ISO 8601 UTC strings and **returned as Unix float seconds**.

```python
def init_db(db_path: str | None = None) -> None        # create tables + seed if users_profile empty; called in lifespan
def get_db_path() -> str                               # DB_PATH env or <project root>/db/finally.db
def connect() -> sqlite3.Connection                    # row_factory=sqlite3.Row, foreign keys on

# reads
def get_cash(user_id="default") -> float
def get_positions(user_id="default") -> list[dict]     # {ticker, quantity, avg_cost, updated_at}
def get_position(ticker, user_id="default") -> dict | None
def get_watchlist(user_id="default") -> list[dict]     # {ticker, added_at}, ordered by added_at
def get_tracked_tickers(user_id="default") -> list[str]  # watchlist ∪ positions
def is_on_watchlist(ticker, user_id="default") -> bool

# watchlist writes
def add_watchlist(ticker, user_id="default") -> tuple[dict, bool]   # (entry, created)
def remove_watchlist(ticker, user_id="default") -> bool             # False if not present

# trades — ONE transaction: cash, position upsert/delete (<0.0001 deletes), trades row, snapshot
def apply_trade(ticker, side, quantity, price, new_cash, new_quantity, new_avg_cost,
                snapshot_total_value, user_id="default") -> dict    # trade row {id,ticker,side,quantity,price,executed_at}
#   apply_trade should run under BEGIN IMMEDIATE and re-validate cash/shares inside the txn,
#   raising app.db.TradeConflict if state changed. Business rules live in app.portfolio.

# snapshots
def record_snapshot_if_stale(total_value, max_age_s=25.0, user_id="default") -> bool  # BEGIN IMMEDIATE, re-check inside
def get_snapshot_history(hours=24, max_points=1000, user_id="default") -> list[dict] # {timestamp, total_value}, oldest first, evenly downsampled

# chat
def add_chat_message(role, content, actions: dict | None, user_id="default") -> dict  # {id, role, content, created_at, actions}
def get_chat_messages(limit=50, user_id="default") -> list[dict]                      # oldest first
```

### `app.portfolio` (backend-engineer)
One shared function for manual and LLM trades (PLAN §9).

```python
class TradeError(Exception):  code: str; message: str; status: int = 400
async def execute_trade(ticker: str, side: str, quantity: float) -> dict   # returns {"trade": {...}, "cash_balance": float}; raises TradeError
def get_portfolio() -> dict          # GET /api/portfolio shape (records stale snapshot)
async def add_to_watchlist(ticker) -> tuple[dict, bool]   # validates ticker, updates market source; returns (watchlist item w/ price, created)
async def remove_from_watchlist(ticker) -> None           # raises TradeError(NOT_FOUND, 404); stops pricing only if no position
def normalize_ticker(raw: str) -> str                     # uppercase/trim/regex, raises TradeError(INVALID_TICKER)
```
Access to the `PriceCache` and `MarketDataSource` is via module-level getters in `app.tracking`:
`get_price_cache() -> PriceCache` (always available; one process-wide instance shared with the SSE router), `get_market() -> MarketDataSource` (set by the lifespan; `RuntimeError` before startup), and `await sync_tracking(ticker)` (watchlist ∪ positions policy; `app.portfolio` already calls it). Also on `app.state.price_cache` / `app.state.market`.
Extra helpers in `app.portfolio`: `compute_portfolio()` (portfolio shape, **no** snapshot side effect — use for LLM context), `get_watchlist_items()` (alias `get_watchlist`; `[{ticker, price, session_change_percent, added_at}]`), `get_portfolio_history()`. `TradeError` subclasses `app.errors.ApiError`.
Dev CORS for `http://localhost:3000` is on when `backend/static/` is absent or `FINALLY_DEV=true` (`FINALLY_DEV=false` forces it off).

`errors.py`: `ApiError(code, message, status)` + handlers producing `{"error": ..., "code": ...}`; `RequestValidationError` → 400 `VALIDATION_ERROR`; catch-all → 500 `INTERNAL_ERROR`. LLM code raises `ApiError("LLM_ERROR", ..., 502)` / `ApiError("LLM_UNAVAILABLE", ..., 503)`.

### `app.llm` + `app/api/chat.py` (llm-engineer)
Exposes `router` (APIRouter with `POST /api/chat`, `GET /api/chat/history`) in `app/api/chat.py`; backend-engineer includes it in `main.py`. Uses `app.portfolio` for actions and `app.db` for history. Follow PLAN §10 and the `claude-haiku` skill exactly (AsyncAnthropic, `messages.parse`, `claude-haiku-5-5`, effort low).

## Frontend ↔ Backend
The contract is PLAN §6 (SSE payload) and §8 (REST shapes + error shape). Frontend may develop against `LLM_MOCK=true` backend: `cd backend && LLM_MOCK=true uv run uvicorn app.main:app --port 8000`.

## E2E
`integration-tester` runs Playwright against the Docker image via `test/docker-compose.test.yml` with `LLM_MOCK=true`. Failures are reported to the owning teammate by SendMessage with: scenario, expected, actual, and any logs/screenshot path. Owners fix and reply; tester re-runs.

## Status Board
Teammates append one line here when a milestone is done (`- [name] <what> — ready`).

- [db-engineer] `app.db` (schema, seed, all TEAM.md functions, `TradeConflict`, extra `get_trades()`) + 44 tests in `backend/tests/db/` (99% cov) — ready
- [llm-engineer] `app.llm` (models, prompt, history builder, AsyncAnthropic `messages.parse` haiku-5-5 effort low, LLM_MOCK keyword mock, actions watchlist-first then trades) + `app/api/chat.py` router (POST /api/chat, GET /api/chat/history; 503 LLM_UNAVAILABLE, 502 LLM_ERROR stores nothing) + 44 tests in `backend/tests/llm/` — ready
- [backend-engineer] main.py (lifespan, error handlers, routers, static mount, dev CORS), errors, tracking, portfolio, /api/portfolio|trade|history|watchlist|health + tests (331 total passing) — ready
- [devops-engineer] Dockerfile (Node 20 → Python 3.12/uv), .dockerignore, scripts/start|stop_{mac.sh,windows.ps1}, .env.example, .gitignore, db/.gitkeep, README, test/docker-compose.test.yml (Playwright v1.65.0-noble), .github/workflows/ci.yml; image builds, start_mac.sh → /api/health ok, / serves UI (200, assets 200) — ready
- [frontend-engineer] `frontend/` Next.js 15 + TS + Tailwind 4 static export (`cd frontend && npm ci && npm run build` → `frontend/out/`); Lightweight Charts + d3-hierarchy; SSE store w/ 3,600-pt buffers; 24 Vitest tests; full E2E suite (28) passing against dev frontend + LLM_MOCK backend — ready
- [integration-tester] Playwright E2E in `test/` (@playwright/test 1.65.0 + lockfile; `test/e2e/` 6 specs, 28 tests: fresh start, API shapes/error codes/SSE, watchlist add/remove, buy/sell, heatmap + P&L chart, mock AI chat). `docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright` → 28/28 passed, 3 consecutive runs, no retries. Local: `test/run-local.sh` (fresh temp DB, 127.0.0.1:8010) — ready
