# api/middleware.py

"""
Request/response logging middleware.

ADAPTED FROM PLAN: the plan's version imports a `StructuredLogger` class
from infrastructure.logging and calls `logger.log_with_context(...)`.
Neither exists in our real infrastructure/logging.py — that module exposes
a module-level `logger` object plus `log_info`/`log_error`/etc. helper
functions instead. Using `log_info` directly here.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from infrastructure.logging import log_info


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request and its response status/timing."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        log_info(
            f"Request: {request.method} {request.url.path}",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )

        response = await call_next(request)
        process_time_ms = (time.time() - start_time) * 1000

        log_info(
            f"Response: {response.status_code}",
            status_code=response.status_code,
            process_time_ms=round(process_time_ms, 2),
        )

        return response