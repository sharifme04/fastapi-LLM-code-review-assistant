"""FastAPI application entry point.

Real-Time Code Review Assistant.
Mounts REST + WebSocket routes, configures CORS / rate limiting /
structured logging / global exception handlers, and sets up DB lifecycle.
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.database import close_db, init_db
from app.redis_client import close_redis
from app.routers import analytics, health, reviews
from app.utils.exceptions import register_exception_handlers
from app.utils.logging import generate_request_id, logger, request_id_ctx

settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: init DB tables. Shutdown: close DB + Redis pools."""
    logger.info("Starting application", extra={"version": settings.app_version})
    await init_db()
    logger.info("Database tables created/verified")

    yield

    await close_db()
    await close_redis()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="Real-Time Code Review Assistant",
    description=(
        "WebSocket-based code review service powered by Claude with manual "
        "tool-calling (no LangChain), streaming responses, and Redis caching."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Per-request structured log with request_id, method, path, duration."""
    rid = generate_request_id()
    request_id_ctx.set(rid)

    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    logger.info(
        "Request completed",
        extra={
            "request_id": rid,
            "method": request.method,
            "path": str(request.url.path),
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-ID"] = rid
    return response


# --- Exception handlers ---
register_exception_handlers(app)

# --- Routers ---
app.include_router(health.router)
app.include_router(reviews.router)
app.include_router(analytics.router)


# --- Static demo client (browser WebSocket UI) ---
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", response_class=HTMLResponse, tags=["Root"])
async def root() -> HTMLResponse:
    """Serve the demo HTML client when present, otherwise a JSON-style banner."""
    index = _static_dir / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Code Review Assistant</h1>"
        "<p>See <a href='/docs'>/docs</a> for the API. "
        "WebSocket endpoint: <code>ws://&lt;host&gt;/review</code></p>"
    )
