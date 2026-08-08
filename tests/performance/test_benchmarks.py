"""
tests/performance/test_benchmarks.py — pytest-level performance sanity
checks for core/orchestrator.py.

Day 13 deliverable (Comprehensive Testing Suite).

ASSUMPTIONS (please confirm)
------------------------------
- `scripts/run_benchmarks.py` is importable as `scripts.run_benchmarks`.
  scripts/ has no __init__.py per the project's documented architecture,
  but Python 3's implicit namespace packages mean this should still work
  as long as the project root is on sys.path — which it already is for
  every other test file here (`from api.middleware import ...` etc.).
  If this import fails in your environment, tell me and I'll duplicate
  the FakeAgent/build_orchestrator code directly into this file instead
  of importing it.
- No `pytest-benchmark` plugin is installed (not in your plugin list:
  typeguard, anyio, asyncio, cov). These tests use plain time.perf_counter
  timing with generous thresholds instead — loose enough to not be flaky
  on a loaded CI box, tight enough to catch a real regression (e.g.
  someone accidentally making the "independent steps run concurrently"
  optimization sequential again).
- These are sanity/regression gates, not precise measurements. For
  detailed numbers, run `python scripts/run_benchmarks.py` directly.

These tests use latency_ms=0 (no simulated per-agent delay) so the
thresholds below are checking *orchestration overhead* only, not
simulated work — keeps the suite fast and keeps flakiness sources to a
minimum. The one exception is
`test_full_pipeline_runs_agents_concurrently_not_sequentially`, which
needs a nonzero simulated per-agent latency specifically to prove the
concurrency optimization is real (if it silently regressed to
sequential execution, wall-clock time would roughly double).
"""

from __future__ import annotations

import time

import pytest

from scripts.run_benchmarks import (
    benchmark_single_task_latency,
    benchmark_task_creation_overhead,
    benchmark_throughput,
    build_orchestrator,
)


@pytest.fixture()
def fast_orchestrator():
    """Zero simulated agent latency -- pure orchestration overhead."""
    orch = build_orchestrator(latency_ms=0.0)
    yield orch
    orch.shutdown()


# ---------------------------------------------------------------------------
# Task creation overhead
# ---------------------------------------------------------------------------
def test_task_creation_overhead_is_low(fast_orchestrator) -> None:
    stats = benchmark_task_creation_overhead(fast_orchestrator, n=100)
    # Generous: pure validation + lock + dict insert should be well under
    # 10ms per call even on a slow/loaded machine. Real regressions here
    # (e.g. an accidental O(n) scan added to create_task) would show up
    # as a much larger jump than this margin allows.
    assert stats.median_ms < 10.0, f"create_task() median latency regressed: {stats.median_ms}ms"
    assert stats.p99_ms < 50.0, f"create_task() p99 latency regressed: {stats.p99_ms}ms"


# ---------------------------------------------------------------------------
# Single-task latency, per task_type
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("task_type", ["summarize", "extract", "full_pipeline"])
def test_single_task_latency_is_reasonable(fast_orchestrator, task_type: str) -> None:
    stats = benchmark_single_task_latency(fast_orchestrator, n=20, task_type=task_type)
    # With zero simulated agent latency, this is measuring pure
    # orchestration overhead (locking, thread pool submission/resolution,
    # extraction JSON normalization). Should stay comfortably under
    # 100ms even for full_pipeline, which touches all three agents.
    assert (
        stats.median_ms < 100.0
    ), f"{task_type} median latency regressed: {stats.median_ms}ms (n={stats.n})"


def test_full_pipeline_slower_than_single_agent_task(fast_orchestrator) -> None:
    """Sanity check that full_pipeline (3 agents) costs more than
    summarize (1 agent) -- catches a broken benchmark/fixture before it
    catches a real regression."""
    summarize_stats = benchmark_single_task_latency(fast_orchestrator, n=20, task_type="summarize")
    full_stats = benchmark_single_task_latency(fast_orchestrator, n=20, task_type="full_pipeline")
    assert full_stats.median_ms >= summarize_stats.median_ms


# ---------------------------------------------------------------------------
# Concurrency: independent steps (summarize/extract) should run in
# parallel, not sequentially, inside a single full_pipeline execution.
# ---------------------------------------------------------------------------
def test_full_pipeline_runs_agents_concurrently_not_sequentially() -> None:
    """With simulated per-agent latency, a full_pipeline task should take
    roughly (critic_latency + max(summarizer_latency, extractor_latency))
    -- NOT the sum of all three -- because summarize/extract run
    concurrently in _run_full_pipeline(). If this ever regresses to
    sequential execution, total latency would jump to roughly 3x a
    single agent's latency instead of roughly 2x."""
    latency_ms = 100.0  # large enough that thread-scheduling noise is negligible
    orchestrator = build_orchestrator(latency_ms=latency_ms, max_parallel_workers=4)
    try:
        task_id = orchestrator.create_task("concurrency check", task_type="full_pipeline")
        start = time.perf_counter()
        orchestrator.execute_pipeline(task_id)
        elapsed_ms = (time.perf_counter() - start) * 1000
    finally:
        orchestrator.shutdown()

    # Sequential (summarizer + extractor + critic) would be ~300ms.
    # Concurrent (max(summarizer, extractor) + critic) would be ~200ms.
    # Assert well below the sequential figure, with slack for scheduling
    # overhead, so this doesn't flake on a busy machine.
    assert elapsed_ms < latency_ms * 2.5, (
        f"full_pipeline took {elapsed_ms:.0f}ms with {latency_ms}ms/agent -- "
        "expected roughly 2x, not 3x. Independent-step concurrency may have regressed."
    )


# ---------------------------------------------------------------------------
# Throughput under concurrent load
# ---------------------------------------------------------------------------
def test_throughput_meets_minimum_tasks_per_second() -> None:
    orchestrator = build_orchestrator(latency_ms=0.0, max_parallel_workers=4)
    try:
        result = benchmark_throughput(orchestrator, n_tasks=40, concurrency=8)
    finally:
        orchestrator.shutdown()

    # With zero simulated agent latency, this is almost entirely
    # scheduling/locking overhead -- should comfortably clear a low bar.
    # This is a floor, not a target: it exists to catch a severe
    # regression (e.g. accidental global lock serializing everything),
    # not to enforce a specific throughput number.
    assert result["tasks_per_second"] > 20.0, (
        f"Throughput regressed: {result['tasks_per_second']} tasks/sec "
        f"({result['n_tasks']} tasks in {result['elapsed_seconds']}s)"
    )
