# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course. The full specification is in [`planning/PLAN.md`](planning/PLAN.md).

## Features

- **Live price streaming** via SSE with green/red flash animations and sparklines
- **Simulated portfolio**: $10k virtual cash, market orders, instant fills, fractional shares
- **Portfolio visualizations**: heatmap (treemap), P&L chart, positions table
- **AI chat assistant**: analyzes holdings, suggests and auto-executes trades and watchlist changes
- **Watchlist management**: track tickers manually or via the AI
- **Dark terminal aesthetic**: Bloomberg-inspired, data-dense layout

## Architecture

One Docker container serving everything on port 8000:

- **Frontend**: Next.js static export (TypeScript, Tailwind CSS, Lightweight Charts), served by FastAPI
- **Backend**: FastAPI (Python, managed with `uv`), REST under `/api/*`, SSE at `/api/stream/prices`
- **Database**: SQLite at `db/finally.db`, created and seeded **at startup** (no migration step); persisted in the `finally-data` Docker volume
- **AI**: Anthropic Python SDK, Claude Haiku 5.5 with Pydantic structured outputs
- **Market data**: built-in GBM simulator (default) or the Massive (Polygon.io) REST API when `MASSIVE_API_KEY` is set

## Quick Start

Requires Docker.

```bash
# macOS / Linux
./scripts/start_mac.sh            # builds the image on first run, starts the container, opens the browser
./scripts/start_mac.sh --build    # force a rebuild
./scripts/stop_mac.sh             # stop; your data volume is kept
```

```powershell
# Windows PowerShell
.\scripts\start_windows.ps1 [-Build] [-NoOpen]
.\scripts\stop_windows.ps1
```

Then open http://localhost:8000. If `.env` is missing, the start script copies `.env.example` to `.env`. The app runs without any keys: prices are simulated, and chat returns `503` until you set `ANTHROPIC_API_KEY` (or `LLM_MOCK=true`).

Equivalent manual commands:

```bash
cp .env.example .env   # then edit
docker build -t finally .
docker run -d --name finally -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

To wipe all data: `docker volume rm finally-data`.

## Environment Variables

See [`.env.example`](.env.example).

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | For real AI chat | Anthropic API key (Claude Haiku 5.5). Without it, `POST /api/chat` returns `503 LLM_UNAVAILABLE` |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use the simulator |
| `MASSIVE_POLL_INTERVAL` | No | Massive poll interval in seconds (default 15, for the free tier) |
| `LLM_MOCK` | No | `true` for deterministic mock chat replies (testing, no key needed) |
| `DB_PATH` | No | SQLite path. Defaults to `<project root>/db/finally.db` locally; the image sets `/app/db/finally.db` |

## Local Development (without Docker)

```bash
# Backend (reads .env from the project root)
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000

# Frontend (port 3000, proxies /api/* to :8000)
cd frontend
npm install
npm run dev
```

## Testing

```bash
# Backend unit tests
cd backend && uv run --extra dev pytest

# Frontend unit tests
cd frontend && npm test

# End-to-end (Playwright in Docker, LLM_MOCK=true), from the repo root
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v
```

## Project Structure

```
finally/
├── frontend/    # Next.js project (static export)
├── backend/     # FastAPI uv project (app.db holds schema + seed logic)
├── planning/    # Project documentation and agent contracts
├── scripts/     # Start/stop helpers (macOS/Linux and Windows)
├── test/        # Playwright E2E tests + docker-compose.test.yml
├── db/          # SQLite volume mount target (runtime)
└── Dockerfile   # Multi-stage build: Node 20 -> Python 3.12
```

## License

See [LICENSE](LICENSE).
