# api/dependencies.py

"""
Dependency injection functions for API routes.
"""

from fastapi import Header, HTTPException, status
from typing import Optional
from infrastructure.config import get_settings
from infrastructure.logging import logger


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """
    Verify API key from X-API-Key header.

    Args:
        x_api_key: API key from header

    Returns:
        str: Verified API key

    Raises:
        HTTPException: 401 if key is missing or invalid
    """
    settings = get_settings()

    # Skip auth if not required
    if not settings.REQUIRE_AUTH:
        return "no-auth"

    # Check if header is present
    if not x_api_key:
        logger.warning("Missing X-API-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate format (should start with "sk-")
    if not x_api_key.startswith("sk-"):
        logger.warning(f"Invalid API key format: {x_api_key[:10]}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.debug(f"API key validated: {x_api_key[:10]}...")
    return x_api_key


def get_orchestrator():
    """Get orchestrator instance."""
    from core.orchestrator import orchestrator

    return orchestrator
