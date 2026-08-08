# api/middleware.py

"""
Custom middleware for TinyAgentOS API.
"""

import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from infrastructure.logging import logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next):
        """Process request and log details."""
        start_time = time.time()

        # Log request
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
            },
        )

        # Process request
        response = await call_next(request)

        # Calculate timing
        process_time = time.time() - start_time

        # Log response
        logger.info(
            f"Response: {response.status_code} - {process_time:.2f}s",
            extra={
                "status_code": response.status_code,
                "process_time_ms": process_time * 1000,
            },
        )

        return response
