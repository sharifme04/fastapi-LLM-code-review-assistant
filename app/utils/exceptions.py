"""Global exception handlers for FastAPI.

Never expose stack traces to clients. Log full details server-side,
return clean error responses.
"""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger("code_review_assistant")


class AppError(Exception):
    """Base application error with status code and user-friendly message."""

    def __init__(self, message: str, status_code: int = 500, detail: Any = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class ReviewError(AppError):
    """Error during code review (LLM call failure, tool error, etc.)."""

    def __init__(self, message: str = "Review failed", detail: Any = None):
        super().__init__(message=message, status_code=502, detail=detail)


class CostLimitExceededError(AppError):
    """Daily cost limit exceeded — block further LLM calls."""

    def __init__(self, daily_total: float, limit: float):
        super().__init__(
            message=f"Daily cost limit exceeded: ${daily_total:.2f} / ${limit:.2f}",
            status_code=429,
            detail={"daily_total": daily_total, "limit": limit},
        )


class ValidationFailureError(AppError):
    """Code submission failed validation (too many lines, unsupported language, etc.)."""

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message=message, status_code=422, detail=detail)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.error(
            "Application error: %s",
            exc.message,
            extra={"status_code": exc.status_code, "detail": exc.detail},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        logger.warning("Validation error: %s", str(exc))
        return JSONResponse(
            status_code=422,
            content={"error": "Validation error", "detail": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )
