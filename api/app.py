"""
FastAPI application for TinyAgentOS.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.limiter import limiter
from api.routes import router, metrics_router
from api.middleware import LoggingMiddleware
from infrastructure.config import get_settings
from infrastructure.logging import logger
from infrastructure.error_tracking import ErrorTracker
from infrastructure.stall_watchdog import configure_default_watchdog
from starlette.responses import Response

# ========================================
# Lifespan Management
# ========================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info("TinyAgentOS starting up")

    # DAY 20: wire the stall watchdog to a real ErrorTracker before
    # anything can call generate(). Placed ahead of the
    # core.orchestrator import below on purpose -- that import
    # constructs the agents, and while model *loading* isn't itself
    # tracked (only generate() calls are, via each agent's
    # track_call() wrapper), this keeps a single obvious startup
    # ordering: monitoring armed first, then the components it watches.
    error_tracker = ErrorTracker()
    watchdog = configure_default_watchdog(
        error_tracker=error_tracker,
        stall_threshold_seconds=settings.STALL_THRESHOLD_SECONDS,
        check_interval_seconds=settings.STALL_WATCHDOG_INTERVAL_SECONDS,
    )
    app.state.error_tracker = error_tracker

    # Forces core.orchestrator's module-level import to run now, at boot,
    # rather than lazily on the first request that calls get_orchestrator()
    # in api/routes.py. This is what actually triggers LLMRuntime() and
    # the ~26s GGUF model load -- doing it here means a broken MODEL_PATH
    # or corrupted model file fails loudly during container startup
    # (inside the HEALTHCHECK start-period window), instead of surfacing
    # as an unexplained ~26s stall on someone's first real task request.
    from core.orchestrator import orchestrator  # noqa: F401

    yield

    # Shutdown
    watchdog.stop()
    logger.info("TinyAgentOS shutting down")


# ========================================
# Application Setup
# ========================================

settings = get_settings()

app = FastAPI(
    title="TinyAgentOS API",
    version=settings.APP_VERSION,
    description="Resource-aware multi-agent AI framework",
    lifespan=lifespan,
)

# ========================================
# Rate Limiting
# ========================================
#
# Config-only until now: default.yaml has always declared
# rate_limit_per_minute: 60, but nothing enforced it. `limiter` is
# imported from api/limiter.py -- the SAME instance api/routes.py's
# @limiter.limit(...) decorators use -- rather than constructed here, so
# enforcement and the exception handler below operate on one shared
# counter instead of two disconnected Limiter objects. key_func=
# get_remote_address limits per client IP; behind a reverse proxy/load
# balancer, this needs ProxyHeadersMiddleware or an
# X-Forwarded-For-aware key_func to see the real client IP instead of
# the proxy's.
app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RateLimitExceeded)
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# ========================================
# Middleware
# ========================================

# CORS
# NARROWED from allow_methods=["*"] / allow_headers=["*"]: this API only
# ever needs GET/POST and two request headers, so wildcarding both was
# more permissive than the app actually requires -- not a fix for a
# missing feature, just tightening what was already here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# Logging
app.add_middleware(LoggingMiddleware)

# ========================================
# Routes
# ========================================

app.include_router(router)
# Unauthenticated on purpose -- see the comment on metrics_router in
# api/routes.py for why, and what to change if /metrics needs to be
# reachable from outside the compose network.
app.include_router(metrics_router)


# ========================================
# Exception Handlers
# ========================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
    )
