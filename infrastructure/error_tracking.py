"""
infrastructure/error_tracking.py — Error recording for TinyAgentOS.

Day 20 deliverable (Monitoring, Observability & Error Tracking).

LOGGER INTERFACE — MATCHED TO YOUR REAL infrastructure/logging.py
-----------------------------------------------------------------
Your actual infrastructure/logging.py doesn't have a log_with_context
method -- `logger` there is a plain stdlib logging.Logger (from
setup_logger()), used the same way agents/critic.py and
agents/extractor.py already use it:
`from infrastructure.logging import logger; logger.warning(...)`.

This module follows that same convention. `severity` strings ("error",
"warning", "info", "debug", "critical") map directly to
logging.Logger's own method names via getattr(), so no translation
layer is needed.

WHY EXTRA-DICT KEYS ARE PREFIXED (error_type / error_context / error_system)
-----------------------------------------------------------------------------
Python's stdlib logging raises KeyError if `extra={}` contains "message"
or "asctime", or any name already on LogRecord (name, msg, args,
levelname, levelno, pathname, filename, module, exc_info, exc_text,
stack_info, lineno, funcName, created, msecs, relativeCreated, thread,
threadName, processName, process). Every key handed to `extra=` here
is prefixed to guarantee no collision, checked against that reserved
list.

WHAT'S ADDED BEYOND THE PLAN'S ORIGINAL TEMPLATE
-----------------------------------------------------
1. `is_first_call` tagging -- the first generate() call after model
   load is structurally slower (KV-cache allocation), so it isn't
   flagged as an anomaly alongside genuine unexplained stalls.
2. System-context capture (CPU%, RSS, load average) at the moment an
   error/stall is recorded.

psutil is optional here, same graceful-degradation pattern as
infrastructure/monitoring.py.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from infrastructure.logging import logger as default_logger

try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

# Keys that Python's stdlib logging.Logger.makeRecord() will reject in
# extra= because they're already LogRecord attributes (or "message" /
# "asctime", which are special-cased).
_LOGGING_RESERVED_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
}


def _capture_system_context() -> Dict[str, Any]:
    """Best-effort snapshot of system state at the moment of an error.
    Returns an empty dict (never raises) if psutil is unavailable --
    error recording must never itself fail a request."""
    if not _PSUTIL_AVAILABLE:
        return {}

    context: Dict[str, Any] = {}
    try:
        context["cpu_percent"] = psutil.cpu_percent(interval=None)
        context["rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        pass

    if hasattr(os, "getloadavg"):
        try:
            context["load_avg"] = os.getloadavg()
        except OSError:
            pass

    return context


class ErrorTracker:
    """Track and log errors for analysis.

    `logger` defaults to infrastructure/logging.py's module-level
    `logger` -- pass a different one only for testing. Any object
    exposing .error()/.warning()/.info()/.debug()/.critical() with the
    stdlib Logger signature (message, extra=...) works.
    """

    def __init__(self, logger: Any = None) -> None:
        self.logger = logger if logger is not None else default_logger
        self.errors: List[Dict[str, Any]] = []

    def record_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        severity: str = "error",
        is_first_call: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Record error with context.

        `is_first_call`: pass True/False when the error originates from
        an LLM call and you know whether it was the first generate()
        call since model load. Left as None (omitted) when not
        applicable, e.g. non-LLM errors.
        """
        error_record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "context": dict(context or {}),
            "severity": severity,
            "system": _capture_system_context(),
        }
        if is_first_call is not None:
            error_record["is_first_call"] = is_first_call

        self.errors.append(error_record)

        self._log(error_record)
        return error_record

    def _log(self, error_record: Dict[str, Any]) -> None:
        """Emit the record through the real logger using its actual
        interface -- getattr(self.logger, severity), matching how
        agents/critic.py and agents/extractor.py already call
        logger.warning(...) directly. Falls back to .error() if an
        unrecognized severity string is passed."""
        message = f"{error_record['type']}: {error_record['message']}"

        extra: Dict[str, Any] = {
            "error_type": error_record["type"],
            "error_context": error_record["context"],
            "error_system": error_record["system"],
        }
        if "is_first_call" in error_record:
            extra["is_first_call"] = error_record["is_first_call"]

        collision = _LOGGING_RESERVED_KEYS & extra.keys()
        if collision:
            raise AssertionError(f"error_tracking._log built a reserved logging key: {collision}")

        log_method = getattr(self.logger, error_record["severity"], None)
        if not callable(log_method):
            log_method = self.logger.error

        log_method(message, extra=extra)

    def get_error_summary(self) -> Dict[str, Any]:
        """Get error summary for monitoring."""
        return {
            "total_errors": len(self.errors),
            "error_types": list(set(e["type"] for e in self.errors)),
            "latest_errors": self.errors[-10:],
        }

    def get_stall_summary(self) -> Dict[str, Any]:
        """Subset of get_error_summary() filtered to stall/timeout-type
        records, excluding known first-call slowness."""
        stalls = [
            e for e in self.errors if e["type"] in ("TimeoutError",) and not e.get("is_first_call")
        ]
        return {
            "total_stalls": len(stalls),
            "stalls": stalls,
        }
