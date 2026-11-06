# syntax=docker/dockerfile:1

# ---------- Stage 1: build the Next.js static export ----------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
RUN npm run build

# ---------- Stage 2: Python runtime ----------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself
COPY backend/ ./
RUN uv sync --frozen --no-dev

# Frontend static export served by FastAPI
COPY --from=frontend /frontend/out/ /app/static/

ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/app/db/finally.db

RUN mkdir -p /app/db
VOLUME ["/app/db"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
