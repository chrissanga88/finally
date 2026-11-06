"""Paths and environment helpers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BACKEND_DIR.parent  # finally/
STATIC_DIR = BACKEND_DIR / "static"  # Next.js export lands here (Docker: /app/static)
DEV_FRONTEND_ORIGIN = "http://localhost:3000"


def load_env() -> None:
    """Load the project-root .env (no-op if missing). Real env vars win."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def is_dev() -> bool:
    """Dev mode: FINALLY_DEV=true, or no frontend build present (local backend dev).

    Only used to allow CORS from the Next dev server (PLAN §4).
    """
    flag = os.environ.get("FINALLY_DEV", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return not STATIC_DIR.is_dir()
