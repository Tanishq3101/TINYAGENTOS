"""
infrastructure/stall_watchdog.py — In-flight stall detection for
LLMRuntime.generate() calls.

Day 20 deliverable (Monitoring, Observability & Error Tracking).

WHY THIS EXISTS AS ITS OWN MODULE
-------------------------------------
infrastructure/monitoring.py's llm_call_latency histogram only records
a duration once a call *completes*. A genuine hang -- like the two
benchmark runs in OPTIMIZATION.md's "Known follow-up items" that were
manually aborted after appearing stuck -- produces zero signal on that
histogram until (if ever) the call finishes. This module closes that
gap with a background thread that periodically checks which calls are
currently in flight and how long they've been running, so a stall gets
flagged *while it's happening* instead of only being visible after the
fact (or not at all, if the process has to be killed).

Kept separate from monitoring.py (metrics definitions) and
error_tracking.py (error recording) rather than folded into either:
it owns its own lifecycle (a thread that must be started/stopped) and
its own mutable state (the in-flight call registry), and mixing that
with either of the other two modules' responsibilities would make both
harder to test in isolation.

WHERE THIS GETS WIRED IN
-----------------------------
NOT inside core/llm_runtime.py. That file is locked (Day 14 fix around
its internal threading.Lock on the native model(...) call) and editing
it risks the exact class of concurrency regression that lock exists to
prevent -- out of scope for a Day 20 monitoring task regardless.

Instead, wrap `track_call()` around the generate() call site inside
each agent's execute() (agents/summarizer.py, agents/extractor.py,
agents/critic.py):

    from infrastructure.stall_watchdog import track_call

    def execute(self, input_data, **kwargs):
        with track_call(agent_name="summarizer"):
            output = self.llm_runtime.generate(prompt, max_tokens=...)
        ...

This is intentionally NOT applied automatically/globally -- it needs an
explicit agent_name per call site, and agents/*.py aren't available in
this context to edit directly. See docs/DAY20_INTEGRATION_GUIDE.md.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from infrastructure.error_tracking import ErrorTracker
from infrastructure.monitoring import record_llm_call

# Above your observed p95 (~9-19s across prompt sizes in OPTIMIZATION.md),
# below the single p99 outlier (36s) that was called out there as "a
# one-off spike, not a stable tail." Sitting between the two means normal
# slow-but-fine calls don't trip the watchdog, but a genuine hang like the
# two aborted runs would. Override via configure() once you have config
# wired in (see docs/DAY20_INTEGRATION_GUIDE.md) -- hardcoded default here
# only so this module works standalone without a config dependency.
DEFAULT_STALL_THRESHOLD_SECONDS = 25.0
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 5.0


class StallWatchdog:
    """Tracks in-flight generate() calls and flags ones that have been
    running longer than a threshold, on a periodic background check.

    Not a singleton by design -- tests construct their own instance
    rather than sharing global state, and a real deployment constructs
    one instance at startup (see docs/DAY20_INTEGRATION_GUIDE.md).
    """

    def __init__(
        self,
        error_tracker: Optional[ErrorTracker] = None,
        stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
        check_interval_seconds: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
    ) -> None:
        self.error_tracker = error_tracker
        self.stall_threshold_seconds = stall_threshold_seconds
        self.check_interval_seconds = check_interval_seconds

        self._active_calls: Dict[str, Dict[str, object]] = {}
        self._lock = threading.Lock()
        self._flagged: set[str] = set()  # avoid re-flagging the same call every tick
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # already running; starting twice is a no-op, not an error
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="stall-watchdog", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def __enter__(self) -> "StallWatchdog":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # -- call registry ---------------------------------------------------
    def _register(self, call_id: str, agent_name: str) -> None:
        with self._lock:
            self._active_calls[call_id] = {
                "agent_name": agent_name,
                "start_time": time.time(),
            }

    def _unregister(self, call_id: str) -> None:
        with self._lock:
            self._active_calls.pop(call_id, None)
            self._flagged.discard(call_id)

    def check_once(self) -> None:
        """Run a single stall check pass. Exposed separately from the
        background loop so tests can assert on watchdog behavior
        deterministically without sleeping or racing a real thread."""
        now = time.time()
        with self._lock:
            snapshot = dict(self._active_calls)

        for call_id, info in snapshot.items():
            elapsed = now - float(info["start_time"])  # type: ignore[arg-type]
            if elapsed <= self.stall_threshold_seconds:
                continue
            if call_id in self._flagged:
                continue  # already reported this call; don't spam per tick

            self._flagged.add(call_id)
            if self.error_tracker is not None:
                self.error_tracker.record_error(
                    TimeoutError(
                        f"generate() call by '{info['agent_name']}' "
                        f"running {elapsed:.1f}s "
                        f"(threshold {self.stall_threshold_seconds}s)"
                    ),
                    context={
                        "call_id": call_id,
                        "agent_name": info["agent_name"],
                        "elapsed_seconds": round(elapsed, 1),
                    },
                    severity="warning",
                    is_first_call=False,
                )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.check_once()
            self._stop_event.wait(self.check_interval_seconds)

    @contextmanager
    def track_call(self, agent_name: str) -> Iterator[None]:
        """Context manager: register a call as in-flight, record its
        duration to the llm_call_latency histogram on completion
        (success or failure), and clear it from the stall registry
        either way."""
        call_id = uuid.uuid4().hex
        start = time.time()
        self._register(call_id, agent_name)
        try:
            yield
        finally:
            duration = time.time() - start
            self._unregister(call_id)
            record_llm_call(agent_name=agent_name, duration_seconds=duration)


# ---------------------------------------------------------------------------
# Module-level default instance + convenience wrapper.
#
# Most call sites (agents/*.py) just want `with track_call("summarizer"):`
# without constructing/wiring a StallWatchdog themselves. The default
# instance below has no error_tracker attached (so it observes latency but
# doesn't flag stalls) until the application wires one up via
# `configure_default_watchdog()` at startup -- see
# docs/DAY20_INTEGRATION_GUIDE.md for where that call goes.
# ---------------------------------------------------------------------------
_default_watchdog = StallWatchdog()


def configure_default_watchdog(
    error_tracker: Optional[ErrorTracker] = None,
    stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
    check_interval_seconds: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
    start: bool = True,
) -> StallWatchdog:
    """Replace and (by default) start the module-level default watchdog.
    Call once at application startup, e.g. alongside where
    Orchestrator/agents are constructed."""
    global _default_watchdog
    _default_watchdog.stop()
    _default_watchdog = StallWatchdog(
        error_tracker=error_tracker,
        stall_threshold_seconds=stall_threshold_seconds,
        check_interval_seconds=check_interval_seconds,
    )
    if start:
        _default_watchdog.start()
    return _default_watchdog


def track_call(agent_name: str):
    """Convenience wrapper around the module-level default watchdog's
    track_call(). This is what agents/*.py should import and use:

        from infrastructure.stall_watchdog import track_call

        with track_call("summarizer"):
            output = self.llm_runtime.generate(prompt, ...)
    """
    return _default_watchdog.track_call(agent_name)
