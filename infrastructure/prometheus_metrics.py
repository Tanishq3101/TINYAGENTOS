"""
infrastructure/prometheus_metrics.py — Prometheus instrumentation for TinyAgentOS.

This is deliberately a separate module from infrastructure/metrics.py.
That module's MetricsCollector/AgentMetrics is an in-process, per-run data
structure (constructed on Orchestrator.__init__ as self.metrics, but never
actually fed by anything yet — see core/orchestrator.py's docstring: "the
data structure that Week 4's Prometheus exporter will read from"). This
module IS that exporter: it defines real prometheus_client collectors and
exposes them via generate_latest() for a /metrics route to return.

The two are not wired together (yet) — this module's counters/histograms
are updated directly at the call sites in orchestrator.py and routes.py,
not by reading self.metrics. Feeding infrastructure.metrics.MetricsCollector
data into these collectors instead is a reasonable follow-up if you want
per-run summaries AND scrapeable time series from the same source of
truth, but they're independent for now.

Usage:
    from infrastructure.prometheus_metrics import (
        TASKS_TOTAL, TASK_DURATION_SECONDS, AGENT_STEP_DURATION_SECONDS,
        AGENT_STEP_ERRORS_TOTAL, AUTH_FAILURES_TOTAL, ACTIVE_TASKS,
        render_metrics,
    )

    TASKS_TOTAL.labels(task_type="full_pipeline", status="completed").inc()
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
    REGISTRY,
)
import os

# ---------------------------------------------------------------------------
# Registry
#
# If PROMETHEUS_MULTIPROC_DIR is set (i.e. you're running multiple Uvicorn
# workers), use the multiprocess-aware registry so counts aren't scoped to
# a single worker process. Single-worker / dev runs (the common case here)
# just use the default global REGISTRY.
# ---------------------------------------------------------------------------
_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


def _get_registry() -> CollectorRegistry:
    if _MULTIPROC_DIR:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return registry
    return REGISTRY


# ---------------------------------------------------------------------------
# Collectors
#
# Label choices matter here for cardinality: task_id is NEVER a label
# (unbounded — would blow up Prometheus storage). task_type and status
# are both small, fixed sets (SUPPORTED_TASK_TYPES / TaskStatus), so
# they're safe.
# ---------------------------------------------------------------------------

# This is the live task counter: incremented directly in
# core/orchestrator.py's execute_pipeline() on both the completed and
# failed paths. infrastructure/monitoring.py previously defined an
# unused, never-called Counter under this same name (task_counter) --
# that one has been removed rather than this one renamed, since this
# is the collector orchestrator.py actually calls.
TASKS_TOTAL = Counter(
    "tinyagentos_tasks_total",
    "Total tasks processed, by task type and terminal status",
    ["task_type", "status"],
)

TASK_DURATION_SECONDS = Histogram(
    "tinyagentos_task_duration_seconds",
    "End-to-end task execution time (execute_pipeline call), by task type",
    ["task_type"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)

AGENT_STEP_DURATION_SECONDS = Histogram(
    "tinyagentos_agent_step_duration_seconds",
    "Per-agent step execution time, by agent name",
    ["agent_name"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

AGENT_STEP_ERRORS_TOTAL = Counter(
    "tinyagentos_agent_step_errors_total",
    "Agent step failures, by agent name",
    ["agent_name"],
)

# Live gauge: inc()/dec() directly in core/orchestrator.py around
# RUNNING-state transitions (_get_task_for_execution / execute_pipeline).
# infrastructure/monitoring.py previously defined an unused, never-called
# Gauge under this same name -- removed there rather than renamed here.
ACTIVE_TASKS = Gauge(
    "tinyagentos_active_tasks",
    "Tasks currently in RUNNING state",
)

AUTH_FAILURES_TOTAL = Counter(
    "tinyagentos_auth_failures_total",
    "Rejected requests due to invalid/missing API key",
)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for a /metrics response."""
    registry = _get_registry()
    return generate_latest(registry), CONTENT_TYPE_LATEST
