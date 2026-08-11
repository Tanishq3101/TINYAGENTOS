"""
infrastructure/monitoring.py — Prometheus metrics for TinyAgentOS.

Day 20 deliverable (Monitoring, Observability & Error Tracking).

WHY THE BUCKETS DIFFER FROM THE ORIGINAL PLAN TEMPLATE
---------------------------------------------------------
The 30-day plan's template used buckets=[0.5, 1.0, 2.0, 5.0, 10.0]. That
range was written before real hardware numbers existed. Day 18-19's
actual measurements (docs/OPTIMIZATION.md, scripts/benchmark_inference.py
output) show medians of 5-8s per prompt size and a p99 outlier at 36s on
this CPU-bound dev machine. With the original buckets, essentially every
real observation would land in the +Inf overflow bucket, making the
histogram useless for anything except "yes, calls happened." Buckets
below are sized to the observed p50-p99 range with resolution around it.

Re-tune these buckets again once you benchmark on production hardware
(especially if GPU acceleration changes the shape of the distribution
significantly) -- they are a snapshot of one dev machine's behavior,
not a universal constant.

TWO LATENCY HISTOGRAMS, DELIBERATELY SEPARATE
------------------------------------------------
- pipeline_latency: whole execute_pipeline() call (may chain multiple
  agent/LLM calls, e.g. full_pipeline = 3 serialized generate() calls).
- llm_call_latency: a single generate() call, labeled by agent_name.

Keeping these separate matters for diagnosis: if only pipeline_latency
existed, a stall inside one agent's single generate() call would just
look like "a slow pipeline" with no way to localize which step caused
it. llm_call_latency lets you tell "one agent is slow" from "everything
is slow" from "the orchestration overhead grew" (which task_creation /
run_benchmarks.py's orchestration-only numbers would catch separately).

GRACEFUL DEGRADATION IF prometheus_client ISN'T INSTALLED YET
------------------------------------------------------------------
Mirrors the psutil-optional pattern already used in
scripts/benchmark_inference.py, for the same reason: this module should
be importable (and its non-Prometheus helpers usable/testable) even
before the dependency is added to requirements.txt and installed.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

# WHY TYPE_CHECKING, NOT A PLAIN try/except FOR THE TYPES
# ---------------------------------------------------------
# mypy statically analyzes BOTH branches of a runtime try/except
# regardless of whether the import actually succeeds. That means what
# mypy thinks `Counter`/`Gauge`/`Histogram` ARE depends on whether
# prometheus_client happens to be installed in whatever environment
# mypy itself is run in -- which silently drifts between machines/CI.
# `if TYPE_CHECKING:` sidesteps that: mypy always evaluates this branch
# (and only this branch) for type-checking purposes, so call sites are
# always checked against the real prometheus_client classes, and the
# runtime fallback logic below is never type-checked branch-by-branch
# (so reassigning the names there isn't a "redefinition" to mypy).
if TYPE_CHECKING:
    from prometheus_client import Counter, Gauge, Histogram

    _PROMETHEUS_AVAILABLE = True
else:
    try:
        from prometheus_client import Counter, Gauge, Histogram

        _PROMETHEUS_AVAILABLE = True
    except ImportError:
        _PROMETHEUS_AVAILABLE = False

        class _NoOpMetric:
            """Stand-in used when prometheus_client isn't installed, so every
            call site (task_counter.labels(...).inc(), histogram.observe(...),
            etc.) still works without branching on availability everywhere."""

            def __init__(self, *args, **kwargs) -> None:
                pass

            def labels(self, *args, **kwargs) -> "_NoOpMetric":
                return self

            def inc(self, *args, **kwargs) -> None:
                pass

            def dec(self, *args, **kwargs) -> None:
                pass

            def observe(self, *args, **kwargs) -> None:
                pass

            def set(self, *args, **kwargs) -> None:
                pass

        Counter = Gauge = Histogram = _NoOpMetric  # type: ignore[misc,assignment]


# Buckets sized to Day 18-19's real observed range (see module docstring).
# Shared between the two latency histograms since both measure the same
# kind of thing (wall-clock seconds for LLM-involving work) at different
# granularity.
_LATENCY_BUCKETS = [1.0, 2.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0, 50.0, 75.0, 120.0]


task_counter = Counter(
    "tinyagentos_tasks_total",
    "Total tasks processed",
    ["status"],
)

pipeline_latency = Histogram(
    "tinyagentos_pipeline_latency_seconds",
    "Full pipeline execution latency (may chain multiple LLM calls)",
    buckets=_LATENCY_BUCKETS,
)

llm_call_latency = Histogram(
    "tinyagentos_llm_call_latency_seconds",
    "Single LLMRuntime.generate() call latency, by calling agent",
    ["agent_name"],
    buckets=_LATENCY_BUCKETS,
)

active_tasks = Gauge(
    "tinyagentos_active_tasks",
    "Number of active tasks",
)

memory_usage = Gauge(
    "tinyagentos_memory_mb",
    "Process RSS memory usage in MB",
)


@contextmanager
def track_task_execution(task_id: str) -> Iterator[None]:
    """Context manager for tracking whole-pipeline execution.

    Usage:
        with track_task_execution(task_id):
            orchestrator.execute_pipeline(task_id)
    """
    active_tasks.inc()
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        active_tasks.dec()
        duration = time.time() - start_time
        pipeline_latency.observe(duration)
        task_counter.labels(status=status).inc()


def record_llm_call(agent_name: str, duration_seconds: float) -> None:
    """Record a single generate() call's duration against the
    per-agent histogram. Called by infrastructure/stall_watchdog.py's
    track_call() on successful completion -- kept as a standalone
    function (not only inside track_call) so tests and any future
    call site can record a duration without needing the full
    watchdog/context-manager machinery.
    """
    llm_call_latency.labels(agent_name=agent_name).observe(duration_seconds)


def sample_memory_usage() -> Optional[float]:
    """Sample current process RSS and push it into the memory_usage
    gauge. Returns the sampled value in MB, or None if psutil isn't
    available (kept optional for the same reason prometheus_client is:
    this module must not hard-require every dependency to be importable
    at all)."""
    try:
        import psutil
    except ImportError:
        return None

    # MYPY FIX (was: "Returning Any from function declared to return
    # 'float | None'"): psutil has no installed type stubs, so
    # memory_info().rss types as Any -- the explicit float() cast gives
    # mypy a concrete return type instead of propagating Any. (Installing
    # `types-psutil` is the other option here, per the earlier mypy run's
    # "Library stubs not installed for psutil" warning -- either fixes
    # this line, but the cast doesn't depend on that being done too.)
    rss_mb = float(psutil.Process().memory_info().rss / (1024 * 1024))
    memory_usage.set(rss_mb)
    return rss_mb