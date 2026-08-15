# api/limiter.py
"""
Single shared slowapi Limiter instance.

Split into its own module so app.py (which registers it on app.state and
wires the RateLimitExceeded exception handler) and routes.py (which
decorates individual endpoints with it) reference the SAME instance.
Two independently-constructed Limiter() objects would track separate
in-memory counters -- the one enforcing @limiter.limit(...) on a route
would not be the one app.state.limiter exposes to the exception handler,
silently breaking enforcement.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
