#!/usr/bin/env bash
# Start FinAlly in Docker (macOS/Linux). Idempotent: safe to run repeatedly.
# Usage: scripts/start_mac.sh [--build] [--no-open]
set -euo pipefail

IMAGE="finally"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${PORT:-8000}"
URL="http://localhost:${PORT}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help) echo "Usage: $0 [--build] [--no-open]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "WARNING: .env not found; created it from .env.example."
  echo "         The app runs without keys (simulated prices). AI chat returns 503"
  echo "         unless you set ANTHROPIC_API_KEY or LLM_MOCK=true in .env."
fi

if [[ $BUILD -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building image '$IMAGE'..."
  docker build -t "$IMAGE" .
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Removing existing container '$CONTAINER'..."
  docker rm -f "$CONTAINER" >/dev/null
fi

docker run -d \
  --name "$CONTAINER" \
  -v "$VOLUME":/app/db \
  -p "$PORT":8000 \
  --env-file .env \
  "$IMAGE" >/dev/null

echo "Waiting for FinAlly to become healthy..."
for _ in $(seq 1 30); do
  if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  echo "FinAlly is running at $URL"
else
  echo "FinAlly container started but $URL/api/health is not responding yet."
  echo "Check logs with: docker logs $CONTAINER"
fi

if [[ $OPEN -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
