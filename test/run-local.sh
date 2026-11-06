#!/usr/bin/env bash
# Run the E2E suite against a local backend with a FRESH temporary SQLite DB.
# Requires a frontend build in backend/static/ (cd frontend && npm run build && cp -r out ../backend/static).
# Usage: test/run-local.sh [playwright args...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# Not 8000: the real app container (scripts/start_mac.sh) may own that port.
PORT="${PORT:-8010}"
BASE="http://127.0.0.1:$PORT"
if curl -s -o /dev/null "$BASE/api/health"; then
  echo "port $PORT already in use; set PORT=<free port>"; exit 1
fi
TMPDB="$(mktemp -d)/finally-e2e.db"

(cd "$ROOT/backend" && DB_PATH="$TMPDB" LLM_MOCK=true MASSIVE_API_KEY= \
  uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$HERE/backend-e2e.log" 2>&1) &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null; pkill -f "uvicorn app.main:app --host 127.0.0.1 --port $PORT" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  curl -sf "$BASE/api/health" >/dev/null && break
  sleep 0.5
done
curl -sf "$BASE/api/health" >/dev/null || { echo "backend failed to start; see test/backend-e2e.log"; exit 1; }

cd "$HERE"
[ -d node_modules ] || npm install --no-audit --no-fund
BASE_URL="$BASE" npx playwright test "$@"
