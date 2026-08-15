"""
scripts/run_benchmarks.py — Performance benchmarks for core/orchestrator.py.

Day 13 deliverable (Comprehensive Testing Suite).

WHAT THIS MEASURES
-------------------
This benchmarks *orchestration overhead* (locking, task bookkeeping,
concurrent step scheduling, TTL cleanup, extraction normalization) —
NOT real LLM inference latency. The two are deliberately kept separate:

  - Real LLM inference time is dominated by model/hardware and belongs
    in its own benchmark once core/llm_runtime.py exists and is stable.
  - Mixing the two here would make orchestration regressions invisible
    (hidden behind LLM noise) and would make every run slow and
    non-deterministic, which defeats the point of a repeatable
    benchmark you can run on every change.

Agents are therefore replaced with lightweight fakes that satisfy the
same contract core/orchestrator.py._run_agent_step() actually checks
for (agents/base.py's Agent.execute() contract):

    result = agent.execute(input_data, **kwargs)
    # must be a dict with result["status"] == "success" and
    # result["output"] holding the payload; anything else is treated
    # as a failed step.

The fakes do NOT subclass the real Agent ABC (agents/base.py) — that
class requires an llm_runtime and AgentConfig, neither of which this
benchmark needs. Satisfying the orchestrator's actual contract directly
is enough, and keeps this script from depending on real agent internals
that may change.

A small configurable time.sleep() per fake agent call simulates
realistic-ish per-step cost (default 5ms) so the "independent steps run
concurrently" optimization in _run_full_pipeline is actually visible in
the numbers, rather than everything reading as ~0ms. Set --latency-ms 0
for a pure overhead measurement with no simulated work at all.

WHAT'S BENCHMARKED
-------------------
1. Task creation overhead (create_task() alone, no execution)
2. Single-task latency, per task_type (summarize / extract / full_pipeline)
3. Throughput under concurrent load (N tasks submitted across a thread
   pool external to the orchestrator's own internal pool, since
   execute_pipeline() is a blocking call — this measures how many
   *tasks* per second the orchestrator can process concurrently, not
   how many *steps within one task* it parallelizes internally)

USAGE
-----
    python scripts/run_benchmarks.py
    python scripts/run_benchmarks.py --iterations 100 --concurrency 16
    python scripts/run_benchmarks.py --latency-ms 0 --output docs/benchmark_results.json

ASSUMPTIONS (please confirm against your real code)
----------------------------------------------------
- Orchestrator(agents, logger=None, ...) works with logger=None, falling
  back to whatever infrastructure.logging exposes by default — confirmed
  from the orchestrator.py you pasted (_default_logger()).
- enable_resource_checks is safe to force off for benchmarking (avoids
  a real infrastructure.resource_monitor, if present, making benchmark
  runs environment-dependent / flaky on loaded CI machines).
- core/pipeline.py's StepExecutionError is importable (orchestrator.py
  imports it at module scope) — not exercised directly here since the
  fake agents never fail, but the import chain must succeed for
  `from core.orchestrator import Orchestrator` to work at all.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from core.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Fake agents — satisfy Agent.execute()'s contract directly, no LLM involved.
# ---------------------------------------------------------------------------
class FakeAgent:
    """Minimal stand-in for a real agent. Returns a fixed, valid `execute()`
    response after an optional simulated delay."""

    def __init__(self, name: str, output: Any, latency_seconds: float = 0.0) -> None:
        self._name = name
        self._output = output
        self._latency_seconds = latency_seconds

    def execute(self, input_data: str, **kwargs: Any) -> Dict[str, Any]:
        if self._latency_seconds > 0:
            time.sleep(self._latency_seconds)
        return {
            "status": "success",
            "output": self._output,
            "metrics": {
                "agent_name": self._name,
                "execution_time_ms": self._latency_seconds * 1000,
            },
        }


def build_orchestrator(
    latency_ms: float = 5.0,
    max_parallel_workers: int = 4,
    max_concurrent_executions: int = 8,
) -> Orchestrator:
    """Build an Orchestrator wired with fast, deterministic fake agents.

    `latency_ms` simulates per-agent-call cost (e.g. LLM inference time)
    so the orchestrator's concurrent-step optimization is measurable.

    `max_parallel_workers` and `max_concurrent_executions` are different
    knobs: the former governs concurrency *within* one pipeline
    (summarize+extract overlapping), the latter governs how many
    execute_pipeline() calls may run at once *across* tasks. Without
    passing max_concurrent_executions explicitly here, Orchestrator
    defaults it to infrastructure.config.get_settings().WORKERS (1 in
    production, since real inference is serialized behind one lock) --
    fine for prod, but silently caps this benchmark's throughput test at
    1 regardless of the requested --concurrency, since fake agents have
    no such serialization constraint to model. Defaulting to 8 here
    matches this script's own --concurrency default.
    """
    latency_seconds = max(0.0, latency_ms) / 1000.0

    agents = {
        "summarizer": FakeAgent(
            "summarizer",
            output="This is a fake benchmark summary.",
            latency_seconds=latency_seconds,
        ),
        "extractor": FakeAgent(
            "extractor",
            output=json.dumps(
                {
                    "key_points": ["point a", "point b"],
                    "entities": {"ORG": ["Acme"]},
                    "sentiment": "neutral",
                    "topics": ["benchmarking"],
                }
            ),
            latency_seconds=latency_seconds,
        ),
        "critic": FakeAgent(
            "critic",
            output={"score": 8, "feedback": "looks fine"},
            latency_seconds=latency_seconds,
        ),
    }

    return Orchestrator(
        agents,
        logger=None,
        enable_resource_checks=False,  # keep benchmarks environment-independent
        max_parallel_workers=max_parallel_workers,
        max_concurrent_executions=max_concurrent_executions,
    )


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------
@dataclass
class LatencyStats:
    n: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float


def _percentile(sorted_data: List[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    idx = min(len(sorted_data) - 1, int(round(pct / 100 * (len(sorted_data) - 1))))
    return sorted_data[idx]


def _stats_from_durations_ms(durations_ms: List[float]) -> LatencyStats:
    data = sorted(durations_ms)
    return LatencyStats(
        n=len(data),
        mean_ms=statistics.mean(data) if data else 0.0,
        median_ms=statistics.median(data) if data else 0.0,
        p95_ms=_percentile(data, 95),
        p99_ms=_percentile(data, 99),
        min_ms=min(data) if data else 0.0,
        max_ms=max(data) if data else 0.0,
    )


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
def benchmark_task_creation_overhead(orchestrator: Orchestrator, n: int = 200) -> LatencyStats:
    """Measure create_task() alone — validation + locking + bookkeeping,
    no pipeline execution."""
    durations_ms: List[float] = []
    for i in range(n):
        start = time.perf_counter()
        orchestrator.create_task(f"benchmark input #{i}", task_type="summarize")
        durations_ms.append((time.perf_counter() - start) * 1000)
    return _stats_from_durations_ms(durations_ms)


def benchmark_single_task_latency(
    orchestrator: Orchestrator, n: int = 50, task_type: str = "full_pipeline"
) -> LatencyStats:
    """Measure create_task() + execute_pipeline() together, sequentially,
    for a single task_type. This is the "how long does one request take"
    number."""
    durations_ms: List[float] = []
    for i in range(n):
        task_id = orchestrator.create_task(f"benchmark input #{i}", task_type=task_type)
        start = time.perf_counter()
        orchestrator.execute_pipeline(task_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    return _stats_from_durations_ms(durations_ms)


def benchmark_throughput(
    orchestrator: Orchestrator, n_tasks: int = 100, concurrency: int = 8
) -> Dict[str, Any]:
    """Submit n_tasks full_pipeline tasks across `concurrency` worker
    threads (external to the orchestrator's own internal thread pool,
    since execute_pipeline() blocks per-call) and measure tasks/sec."""
    task_ids = [
        orchestrator.create_task(f"throughput input #{i}", task_type="full_pipeline")
        for i in range(n_tasks)
    ]

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bench-throughput") as pool:
        futures = [pool.submit(orchestrator.execute_pipeline, tid) for tid in task_ids]
        for f in as_completed(futures):
            f.result()  # propagate any exception now, not silently
    elapsed_seconds = time.perf_counter() - start

    return {
        "n_tasks": n_tasks,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "tasks_per_second": (
            round(n_tasks / elapsed_seconds, 2) if elapsed_seconds > 0 else float("inf")
        ),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_latency_stats(label: str, stats: LatencyStats) -> None:
    print(f"\n{label} (n={stats.n})")
    print(f"  mean:   {stats.mean_ms:8.2f} ms")
    print(f"  median: {stats.median_ms:8.2f} ms")
    print(f"  p95:    {stats.p95_ms:8.2f} ms")
    print(f"  p99:    {stats.p99_ms:8.2f} ms")
    print(f"  min:    {stats.min_ms:8.2f} ms")
    print(f"  max:    {stats.max_ms:8.2f} ms")


def run_all_benchmarks(
    iterations: int = 50, concurrency: int = 8, latency_ms: float = 5.0
) -> Dict[str, Any]:
    print("=" * 60)
    print("TinyAgentOS — Orchestrator Performance Benchmarks")
    print(f"(simulated per-agent-call latency: {latency_ms}ms, iterations: {iterations})")
    print("=" * 60)

    results: Dict[str, Any] = {}

    orchestrator = build_orchestrator(latency_ms=latency_ms)
    try:
        creation_stats = benchmark_task_creation_overhead(orchestrator, n=iterations * 2)
        _print_latency_stats("Task creation overhead", creation_stats)
        results["task_creation"] = asdict(creation_stats)

        for task_type in ("summarize", "extract", "full_pipeline"):
            stats = benchmark_single_task_latency(orchestrator, n=iterations, task_type=task_type)
            _print_latency_stats(f"Single-task latency ({task_type})", stats)
            results[f"single_task_{task_type}"] = asdict(stats)
    finally:
        orchestrator.shutdown()

    # Fresh orchestrator for the throughput run so accumulated task history
    # from the latency runs above doesn't skew TTL/eviction behavior.
    # max_concurrent_executions matches `concurrency` so the throughput
    # measurement reflects the thread pool's actual concurrency, not an
    # artificial cap from Orchestrator's own admission-control semaphore.
    throughput_orchestrator = build_orchestrator(
        latency_ms=latency_ms, max_concurrent_executions=concurrency
    )
    try:
        throughput = benchmark_throughput(
            throughput_orchestrator, n_tasks=iterations * 2, concurrency=concurrency
        )
        print(f"\nThroughput (concurrency={concurrency})")
        print(f"  {throughput['n_tasks']} tasks in {throughput['elapsed_seconds']}s")
        print(f"  {throughput['tasks_per_second']} tasks/sec")
        results["throughput"] = throughput
    finally:
        throughput_orchestrator.shutdown()

    print("\n" + "=" * 60)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run TinyAgentOS orchestrator performance benchmarks"
    )
    parser.add_argument(
        "--iterations", type=int, default=50, help="Iterations per latency benchmark"
    )
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Worker threads for throughput test"
    )
    parser.add_argument(
        "--latency-ms",
        type=float,
        default=5.0,
        help="Simulated per-agent-call latency in ms (0 for pure overhead measurement)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write results as JSON (e.g. docs/benchmark_results.json)",
    )
    args = parser.parse_args()

    results = run_all_benchmarks(
        iterations=args.iterations,
        concurrency=args.concurrency,
        latency_ms=args.latency_ms,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()