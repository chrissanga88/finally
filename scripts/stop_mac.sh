#!/usr/bin/env bash
# Stop and remove the FinAlly container (macOS/Linux). The data volume is kept.
# Idempotent: safe to run when nothing is running.
set -euo pipefail

CONTAINER="finally"

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER" >/dev/null
  echo "Stopped and removed container '$CONTAINER'. Data volume 'finally-data' was kept."
else
  echo "Container '$CONTAINER' is not running."
fi
