"""
FastAPI application for TinyAgentOS.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from datetime import datetime

from api.routes import router
from api.middleware import LoggingMiddleware
from infrastructure.config import get_settings
from infrastructure.logging import logger

# ========================================
# Lifespan Management
# ========================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info("TinyAgentOS starting up")

    yield

    # Shutdown
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
# Middleware
# ========================================

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging
app.add_middleware(LoggingMiddleware)

# ========================================
# Routes
# ========================================

app.include_router(router)


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
