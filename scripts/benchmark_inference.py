"""
scripts/benchmark_inference.py — Real LLM inference latency & memory benchmarks.

Day 18-19 deliverable (Performance Optimization & Profiling).

Companion to scripts/run_benchmarks.py, which deliberately excludes real LLM
inference (see its docstring) using FakeAgent stand-ins. This script fills
that gap now that core/llm_runtime.py is stable (Day 14 lock fix): it calls
LLMRuntime.generate() directly -- no Orchestrator, no agents/ package -- so
these numbers measure model/hardware cost in isolation, not orchestration
overhead (already covered by run_benchmarks.py).

NOTE ON run_benchmarks.py: `core/orchestrator.py` currently instantiates a
real LLMRuntime + full agent graph as a module-level side effect (its
bottom ~40 lines), so `from core.orchestrator import Orchestrator` always
loads the real GGUF model regardless of which script imports it. This
script avoids that entirely by importing only `core.llm_runtime`.

WHAT THIS MEASURES
-------------------
1. Model load time and memory footprint (RSS delta from before
   LLMRuntime() to after) -- llama-cpp-python holds weights in native
   (non-Python) heap memory via llama.cpp's C++ allocator, so this is
   invisible to Python's tracemalloc (stdlib), which only instruments the
   Python heap. psutil's RSS-delta approach is the only reliable way to
   see it from pure Python without a native profiler.
2. Per-call inference latency across representative prompt lengths
   (short/medium/long), reported as mean/median/p95/p99/min/max --
   matching scripts/run_benchmarks.py's LatencyStats shape exactly so the
   two result sets are directly comparable.
3. Memory growth across repeated generate() calls -- checks whether RSS
   climbs unboundedly (a KV-cache or context leak) or plateaus once
   n_ctx is saturated, by sampling RSS after every call.

CONTRACT THIS RELIES ON (core/llm_runtime.py, verified against your file)
---------------------------------------------------------------------------
- LLMRuntime is a singleton: LLMRuntime() with NO constructor args; the
  GGUF load happens once, inside __init__, guarded by self._initialized.
- generate(self, prompt: str, max_tokens=None, temperature=None) -> str
  Returns a plain string (not a dict, no built-in timing/token metadata)
  -- so all timing here is measured externally with time.perf_counter(),
  and there is no token count to compute tokens/sec from without adding
  a tokenizer call this script deliberately doesn't add scope for.
- generate() internally serializes on a threading.Lock around the native
  self.model(...) call (the Day 14 fix) -- irrelevant here since this
  script calls it single-threaded, but means these numbers represent
  true single-call cost, not lock-contended cost.

WHY THIS CAN'T RUN IN THE DEV SANDBOX
---------------------------------------
Requires llama-cpp-python (compiled, no network here to install it) and a
real GGUF file at settings.MODEL_PATH (none present). Run this on a
machine with the actual model and dependencies installed, then share the
--output JSON so docs/OPTIMIZATION.md can be filled with real numbers.

NEW DEPENDENCY
---------------
Adds `psutil` to requirements.txt for the RSS-delta measurements -- see
that file's diff. Flagged explicitly, not added silently. If you skip
installing it, latency benchmarks still run fine (--skip-memory or the
import guard below handles it); only load-footprint and growth numbers
are unavailable.

USAGE
-----
    python scripts/benchmark_inference.py
    python scripts/benchmark_inference.py --calls 20 --max-tokens 256 \
        --output docs/inference_benchmark_results.json
    python scripts/benchmark_inference.py --skip-memory   # if psutil unavailable
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

from core.llm_runtime import LLMRuntime


# ---------------------------------------------------------------------------
# Timing helpers -- deliberately identical shape to scripts/run_benchmarks.py
# so the two result sets can be diffed/merged without reconciling schemas.
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


def _rss_mb() -> Optional[float]:
    """Current process resident set size in MB, or None if psutil is
    unavailable. RSS (not Python-heap-only) is what catches native
    llama.cpp allocations that tracemalloc would miss entirely."""
    if not _PSUTIL_AVAILABLE:
        return None
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ---------------------------------------------------------------------------
# Representative prompts covering the realistic range of a
# summarize/extract/critic call. Word counts, not real token counts --
# fine for relative benchmarking, not for exact token accounting.
# ---------------------------------------------------------------------------
_PROMPTS: Dict[str, str] = {
    "short": "Summarize the benefits of regular exercise in one sentence.",
    "medium": (
        "Summarize the following text and extract the three most important "
        "points: " + ("The quarterly report showed steady growth. " * 15)
    ),
    "long": (
        "Summarize the following text and extract key entities, sentiment, "
        "and topics: " + ("The quarterly report showed steady growth. " * 60)
    ),
}


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
def load_model_and_measure() -> Tuple[LLMRuntime, Dict[str, Any]]:
    """Construct LLMRuntime (triggers the actual GGUF load on first call,
    since it's a singleton) and measure RSS before/after."""
    rss_before = _rss_mb()
    start = time.perf_counter()
    runtime = LLMRuntime()
    load_seconds = time.perf_counter() - start
    rss_after = _rss_mb()

    stats: Dict[str, Any] = {"load_seconds": round(load_seconds, 3)}
    if rss_before is not None and rss_after is not None:
        stats["rss_before_mb"] = round(rss_before, 1)
        stats["rss_after_mb"] = round(rss_after, 1)
        stats["model_footprint_mb"] = round(rss_after - rss_before, 1)
    return runtime, stats


def warm_up(runtime: LLMRuntime, max_tokens: int = 32) -> float:
    """One throwaway generate() call to absorb first-call costs (mmap page
    faults pulling the GGUF into RAM, llama.cpp thread-pool spin-up, etc.)
    before any timed benchmark runs. Returns the warm-up call's own
    duration in ms, purely for logging -- it is never counted in results.
    """
    start = time.perf_counter()
    runtime.generate(_PROMPTS["short"], max_tokens=max_tokens)
    return (time.perf_counter() - start) * 1000


def benchmark_latency_by_prompt_size(
    runtime: LLMRuntime, calls_per_size: int = 10, max_tokens: int = 128
) -> Dict[str, Any]:
    """Per-call generate() latency, broken out by prompt size."""
    results: Dict[str, Any] = {}
    for label, prompt in _PROMPTS.items():
        durations_ms: List[float] = []
        for _ in range(calls_per_size):
            start = time.perf_counter()
            runtime.generate(prompt, max_tokens=max_tokens)
            durations_ms.append((time.perf_counter() - start) * 1000)
        results[label] = asdict(_stats_from_durations_ms(durations_ms))
    return results


def benchmark_memory_growth(
    runtime: LLMRuntime, calls: int = 20, prompt: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Repeated generate() calls, sampling RSS after each, to catch
    unbounded growth across calls (vs. a plateau, which is expected)."""
    if not _PSUTIL_AVAILABLE:
        return None

    prompt = prompt or _PROMPTS["medium"]
    samples_mb: List[float] = []
    for _ in range(calls):
        runtime.generate(prompt, max_tokens=64)
        rss = _rss_mb()
        if rss is not None:
            samples_mb.append(round(rss, 1))

    if not samples_mb:
        return None

    return {
        "n_calls": len(samples_mb),
        "rss_mb_first": samples_mb[0],
        "rss_mb_last": samples_mb[-1],
        "rss_mb_min": min(samples_mb),
        "rss_mb_max": max(samples_mb),
        "growth_mb": round(samples_mb[-1] - samples_mb[0], 1),
        "samples_mb": samples_mb,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_latency_stats(label: str, stats: Dict[str, Any]) -> None:
    print(f"\n{label} (n={stats['n']})")
    print(f"  mean:   {stats['mean_ms']:8.2f} ms")
    print(f"  median: {stats['median_ms']:8.2f} ms")
    print(f"  p95:    {stats['p95_ms']:8.2f} ms")
    print(f"  p99:    {stats['p99_ms']:8.2f} ms")
    print(f"  min:    {stats['min_ms']:8.2f} ms")
    print(f"  max:    {stats['max_ms']:8.2f} ms")


def _print_rss_checkpoints(checkpoints: Dict[str, Any]) -> None:
    """Print RSS at each phase boundary plus the delta since the previous
    checkpoint, so growth can be attributed to a specific phase (load /
    warm-up / latency bench / growth-check) instead of only comparing
    the very first and very last measurement of the whole run."""
    print("\nRSS checkpoints (by phase)")
    labels = {
        "after_load_mb": "after model load",
        "after_warmup_mb": "after warm-up call",
        "after_latency_bench_mb": "after latency benchmarks",
        "after_growth_check_mb": "after memory growth check",
    }
    prev_value: Optional[float] = None
    for key, label in labels.items():
        value = checkpoints.get(key)
        if value is None:
            continue
        if prev_value is None:
            print(f"  {label:28s} {value:9.1f} MB")
        else:
            delta = value - prev_value
            sign = "+" if delta >= 0 else ""
            print(f"  {label:28s} {value:9.1f} MB   ({sign}{delta:.1f} MB)")
        prev_value = value


def run_all_benchmarks(
    calls_per_size: int = 10,
    max_tokens: int = 128,
    memory_calls: int = 20,
    skip_memory: bool = False,
) -> Dict[str, Any]:
    print("=" * 60)
    print("TinyAgentOS — Real LLM Inference Benchmarks")
    print(f"(calls per prompt size: {calls_per_size}, max_tokens: {max_tokens})")
    print("=" * 60)

    if not _PSUTIL_AVAILABLE and not skip_memory:
        print("\n[warning] psutil not installed -- skipping memory measurements.")
        print("          pip install psutil (now in requirements.txt) to enable them.")

    results: Dict[str, Any] = {}

    print("\nLoading model...")
    runtime, load_stats = load_model_and_measure()
    print(f"  load time: {load_stats['load_seconds']}s")
    if "model_footprint_mb" in load_stats:
        print(f"  model RSS footprint: {load_stats['model_footprint_mb']} MB")
    results["model_load"] = load_stats

    # RSS checkpoints -- taken at each phase boundary so memory growth can
    # be attributed to a specific phase (load / warm-up / latency bench /
    # growth-check) instead of only comparing before-everything to
    # after-everything, which hides *when* memory was actually added.
    # Printed at the end of the run via _print_rss_checkpoints() so this
    # is visible without needing --output.
    rss_checkpoints: Dict[str, Any] = {}
    rss_checkpoints["after_load_mb"] = _rss_mb()

    print("\nWarming up...")
    warm_up_ms = warm_up(runtime)
    print(f"  warm-up call: {warm_up_ms:.2f} ms (not counted in results)")
    rss_checkpoints["after_warmup_mb"] = _rss_mb()

    print("\nRunning latency benchmarks...")
    latency_results = benchmark_latency_by_prompt_size(
        runtime, calls_per_size=calls_per_size, max_tokens=max_tokens
    )
    for label, stats in latency_results.items():
        _print_latency_stats(f"Inference latency ({label} prompt)", stats)
    results["latency_by_prompt_size"] = latency_results
    rss_checkpoints["after_latency_bench_mb"] = _rss_mb()

    if not skip_memory and _PSUTIL_AVAILABLE:
        print("\nRunning memory growth check...")
        growth = benchmark_memory_growth(runtime, calls=memory_calls)
        if growth:
            print(
                f"  RSS: {growth['rss_mb_first']} MB -> {growth['rss_mb_last']} MB "
                f"over {growth['n_calls']} calls (growth: {growth['growth_mb']} MB)"
            )
        results["memory_growth"] = growth
        rss_checkpoints["after_growth_check_mb"] = _rss_mb()

    if _PSUTIL_AVAILABLE:
        results["rss_checkpoints"] = rss_checkpoints
        _print_rss_checkpoints(rss_checkpoints)

    print("\n" + "=" * 60)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TinyAgentOS real LLM inference benchmarks")
    parser.add_argument(
        "--calls", type=int, default=10, help="Calls per prompt size for latency benchmark"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=128, help="max_tokens passed to generate()"
    )
    parser.add_argument(
        "--memory-calls", type=int, default=20, help="Number of calls for memory growth check"
    )
    parser.add_argument(
        "--skip-memory", action="store_true", help="Skip memory measurements entirely"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write results as JSON (e.g. docs/inference_benchmark_results.json)",
    )
    args = parser.parse_args()

    results = run_all_benchmarks(
        calls_per_size=args.calls,
        max_tokens=args.max_tokens,
        memory_calls=args.memory_calls,
        skip_memory=args.skip_memory,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
