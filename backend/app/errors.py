"""API error shape and exception handlers (PLAN §8).

Every error response is ``{"error": <message>, "code": <CODE>}`` with an appropriate status.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An error that maps directly onto the API error shape."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict[str, str]:
        return {"error": self.message, "code": self.code}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.code!r}, {self.message!r}, {self.status})"


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "code": code})


_HTTP_CODES = {404: "NOT_FOUND", 405: "VALIDATION_ERROR", 400: "VALIDATION_ERROR"}


def _format_validation(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "Invalid request: " + "; ".join(parts) if parts else "Invalid request"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("VALIDATION_ERROR", _format_validation(exc), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        fallback = "INTERNAL_ERROR" if exc.status_code >= 500 else "VALIDATION_ERROR"
        code = _HTTP_CODES.get(exc.status_code, fallback)
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return error_response("INTERNAL_ERROR", "Internal server error", 500)
