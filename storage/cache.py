# path: tinyagentos/storage/cache.py

"""
Thread-safe in-memory cache with per-entry TTL.

This is Phase 1's cache — a Redis-backed version is the planned Phase 2
upgrade (see docs/FINAL_STATUS.md "Next Steps"). Keeping the interface
small (get/set/delete/clear) now means swapping the backend later doesn't
touch any calling code.
"""

import threading
from datetime import datetime
from typing import Any, Optional


class CacheEntry:
    """A single cached value with its own TTL."""

    def __init__(self, value: Any, ttl_seconds: int = 3600) -> None:
        self.value = value
        self.created_at = datetime.now()
        self.ttl_seconds = ttl_seconds

    def is_expired(self) -> bool:
        return (datetime.now() - self.created_at).total_seconds() > self.ttl_seconds


class InMemoryCache:
    """Thread-safe in-memory cache."""

    def __init__(self) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        """Set a cache entry."""
        with self._lock:
            self._cache[key] = CacheEntry(value, ttl_seconds)

    def get(self, key: str) -> Optional[Any]:
        """Get a cache entry if present and not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value

    def delete(self, key: str) -> None:
        """Remove a single key, if present. No-op if it doesn't exist."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def purge_expired(self) -> int:
        """Remove all expired entries. Returns the count removed.

        Without this, entries that are set and never read again (no `get`
        ever triggers their lazy eviction) sit in memory until the process
        restarts. Call this periodically (e.g. a background task) in
        production rather than relying only on lazy eviction via get().
        """
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired_keys:
                del self._cache[k]
            return len(expired_keys)