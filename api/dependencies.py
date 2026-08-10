# api/dependencies.py

"""
Dependency injection functions for API routes.

This is now the single source of truth for verify_api_key() -- previously
there were two near-identical copies (this file's, unused, and
api/routes.py's, the one actually wired in). api/routes.py now imports
verify_api_key from here instead of defining its own copy. See
docs/SECURITY.md's "API Key Authentication" section for the history of
why that duplication existed and what changed.

verify_api_key() itself changed from a format check (accept anything
starting with "sk-") to a real credential check: hash the presented key,
look up a stored ApiKeyModel row by that hash, confirm via
SecurityManager's constant-time compare, and reject revoked keys.

get_database() is cached (lru_cache, same pattern as
infrastructure.config.get_settings()) rather than constructing a new
Database -- and therefore a new SQLAlchemy engine -- on every single
request. Uncached, verify_api_key() running on every authenticated
request would have built a fresh engine per call, which is wasteful even
when the DB is healthy and was flagged before this ever shipped.
"""

from functools import lru_cache
from typing import Optional

from fastapi import Header, HTTPException, status

from infrastructure.config import get_settings
from infrastructure.logging import logger
from infrastructure.security import SecurityManager
from storage.database import Database


@lru_cache()
def get_database() -> Database:
    """Get a cached Database instance for the configured DATABASE_URL.

    Built once and reused, matching get_settings()'s lru_cache pattern --
    Database owns a SQLAlchemy engine and session factory, and rebuilding
    those on every request (the previous version of this function) is
    pure waste once the DB is confirmed healthy, and noise to diagnose if
    it isn't.

    init_db() runs here, at first construction, rather than requiring a
    separate app-startup step -- it's idempotent (safe no-op if tables
    already exist, per Database.init_db()'s own docstring), so this is
    the one place that guarantees the api_keys table exists before
    verify_api_key() ever queries it, without depending on some other
    part of the app (e.g. core/orchestrator.py, which does not currently
    touch storage/database.py at all) having already triggered table
    creation first.
    """
    settings = get_settings()
    db = Database(settings.DATABASE_URL)
    db.init_db()
    return db


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """
    Verify API key from X-API-Key header against stored, hashed keys.

    Previously: accepted any string starting with "sk-", no credential
    check at all. Now performs a real check:
      1. Header present?
      2. Hash the candidate; look up a matching ApiKeyModel row by hash.
      3. Confirm via SecurityManager.verify_api_key() (constant-time
         compare against the stored hash).
      4. Reject if the matched key has been revoked.

    Args:
        x_api_key: API key from the X-API-Key header.

    Returns:
        str: "no-auth" if REQUIRE_AUTH is False, otherwise the matched
        ApiKeyModel row's id (never the raw key) -- callers that want to
        attribute a request to a specific issued key can use this without
        ever handling the raw secret again.

    Raises:
        HTTPException: 401 if the key is missing, unrecognized, invalid,
        or revoked. 503 if the API-key store itself is unreachable (e.g.
        the database is down/misconfigured) -- deliberately a different
        status than 401, so a DB outage is never reported to a caller as
        "your credentials are wrong."
    """
    settings = get_settings()

    if not settings.REQUIRE_AUTH:
        return "no-auth"

    if not x_api_key:
        logger.warning("Missing X-API-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        db = get_database()
        candidate_hash = SecurityManager.hash_api_key(x_api_key)
        api_key_row = db.get_api_key_by_hash(candidate_hash)
    except Exception as exc:
        # DB unreachable/misconfigured is a system failure, not a bad
        # credential -- surface it as 503, not 401, and log loudly. This
        # is the failure mode a not-yet-verified DB setup is most likely
        # to hit first; make it obvious rather than indistinguishable
        # from "wrong API key."
        logger.error(f"API key lookup failed -- database unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    if api_key_row is None or not SecurityManager.verify_api_key(
        x_api_key, api_key_row.key_hash
    ):
        logger.warning("API key not recognized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if api_key_row.revoked:
        logger.warning(f"Revoked API key used: {api_key_row.id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Best-effort. This must never turn an already-successful auth into a
    # failed request -- deliberately broad except, matching the pattern
    # routes.py already uses for its own top-level error handling.
    try:
        db.touch_api_key_last_used(api_key_row.id)
    except Exception as exc:  # noqa: BLE001 -- intentionally broad, see above
        logger.error(f"Failed to update last_used_at for API key {api_key_row.id}: {exc}")

    logger.debug(f"API key validated: {api_key_row.id}")
    return api_key_row.id


def get_orchestrator():
    """Get orchestrator instance."""
    from core.orchestrator import orchestrator

    return orchestrator