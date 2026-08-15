"""
infrastructure/monitoring.py — Per-agent LLM call latency histogram for
TinyAgentOS.

Day 20 deliverable (Monitoring, Observability & Error Tracking).

TRIMMED (post-Day 20): this module originally also defined
task_counter, pipeline_latency, active_tasks, and memory_usage, plus a
track_task_execution() context manager and a sample_memory_usage()
helper. Those were never wired to any call site -- app.py,
core/orchestrator.py, api/routes.py, and every agents/*.py were audited
and none of them called track_task_execution, read/wrote active_tasks,
task_counter, pipeline_latency, or memory_usage, or called
sample_memory_usage(). They were also duplicating names
(tinyagentos_tasks_total, tinyagentos_active_tasks) with
infrastructure/prometheus_metrics.py's TASKS_TOTAL/ACTIVE_TASKS, which
*are* live (called directly from core/orchestrator.py's
execute_pipeline). Rather than keep dead collectors around that only
existed to collide with the real ones, they've been removed here.
Re-add task-level tracking only if/when something actually calls it --
prometheus_metrics.py already covers that job.

What's left is the one histogram that IS live: llm_call_latency, fed by
record_llm_call(), which infrastructure/stall_watchdog.py's
track_call() calls on every completed generate() call. Confirmed live
by direct call sites in agents/summarizer.py, agents/extractor.py, and
agents/critic.py, each of which wraps its LLMRuntime.generate() call in
`with track_call(agent_name=...)`.

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

GRACEFUL DEGRADATION IF prometheus_client ISN'T INSTALLED YET
------------------------------------------------------------------
Mirrors the psutil-optional pattern already used elsewhere in this
project, for the same reason: this module should be importable (and
record_llm_call() usable/testable) even before prometheus_client is
added to requirements.txt and installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# WHY TYPE_CHECKING, NOT A PLAIN try/except FOR THE TYPES
# ---------------------------------------------------------
# mypy statically analyzes BOTH branches of a runtime try/except
# regardless of whether the import actually succeeds. That means what
# mypy thinks `Histogram` IS depends on whether prometheus_client
# happens to be installed in whatever environment mypy itself is run
# in -- which silently drifts between machines/CI. `if TYPE_CHECKING:`
# sidesteps that: mypy always evaluates this branch (and only this
# branch) for type-checking purposes, so call sites are always checked
# against the real prometheus_client class, and the runtime fallback
# logic below is never type-checked branch-by-branch (so reassigning
# the name there isn't a "redefinition" to mypy).
if TYPE_CHECKING:
    from prometheus_client import Histogram

    _PROMETHEUS_AVAILABLE = True
else:
    try:
        from prometheus_client import Histogram

        _PROMETHEUS_AVAILABLE = True
    except ImportError:
        _PROMETHEUS_AVAILABLE = False

        class _NoOpMetric:
            """Stand-in used when prometheus_client isn't installed, so
            llm_call_latency.labels(...).observe(...) still works
            without branching on availability at the call site."""

            def __init__(self, *args, **kwargs) -> None:
                pass

            def labels(self, *args, **kwargs) -> "_NoOpMetric":
                return self

            def observe(self, *args, **kwargs) -> None:
                pass

        Histogram = _NoOpMetric  # type: ignore[misc,assignment]


# Buckets sized to Day 18-19's real observed range (see module docstring).
_LATENCY_BUCKETS = [1.0, 2.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0, 50.0, 75.0, 120.0]


llm_call_latency = Histogram(
    "tinyagentos_llm_call_latency_seconds",
    "Single LLMRuntime.generate() call latency, by calling agent",
    ["agent_name"],
    buckets=_LATENCY_BUCKETS,
)


def record_llm_call(agent_name: str, duration_seconds: float) -> None:
    """Record a single generate() call's duration against the
    per-agent histogram. Called by infrastructure/stall_watchdog.py's
    track_call() on successful completion -- kept as a standalone
    function (not only inside track_call) so tests and any future
    call site can record a duration without needing the full
    watchdog/context-manager machinery.
    """
    llm_call_latency.labels(agent_name=agent_name).observe(duration_seconds)