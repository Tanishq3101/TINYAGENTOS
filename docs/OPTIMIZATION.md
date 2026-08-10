# Performance & Optimization Notes (Day 18-19)

Measured with `scripts/benchmark_inference.py` (real LLM inference,
`core/llm_runtime.py` called directly, no orchestrator) and
`scripts/run_benchmarks.py` (orchestration overhead only, `FakeAgent`
stand-ins, no real inference). See each script's docstring for why the
two are kept separate.

## Orchestration overhead (no real LLM)

| Benchmark | mean | median | p95 | p99 |
|---|---|---|---|---|
| Task creation | 0.32-1.07 ms | 0.28-0.93 ms | 0.5-2.0 ms | 0.8-3.7 ms |
| Single task — summarize | ~14.7 ms | ~14.3 ms | ~17-20 ms | ~22-23 ms |
| Single task — extract | ~14.6 ms | ~14.5 ms | ~16-18 ms | ~22-28 ms |
| Single task — full_pipeline | ~30.5 ms | ~30.2 ms | ~32 ms | ~44-50 ms |
| Throughput (concurrency=8) | 121-126 tasks/sec | | | |

Simulated 5ms per-agent-call latency; confirms the orchestrator's own
bookkeeping/locking/concurrent-step scheduling overhead is small
(single-digit-to-low-double-digit ms) and not the bottleneck — real
inference dominates end-to-end latency by 2-3 orders of magnitude (see
below).

## Model load & memory footprint

| Phase | RSS | Delta |
|---|---|---|
| Baseline | ~51 MB | — |
| After model load | ~830 MB | +780 MB |
| After first `generate()` call | ~2.7-3.1 GB | **+1.9-2.2 GB** |
| After 30+90 further calls | ~2.9-3.2 GB | +0.1-0.3 MB (negligible) |

Reproduced across 3 independent runs. Model load itself costs ~780MB
RSS. The **first** `generate()` call adds a further ~1.9-2.2GB one-time
cost — this is llama.cpp allocating the KV-cache sized to the full
configured context window (`n_ctx`), which happens once regardless of
prompt length (even the short warm-up prompt triggers the full
allocation). No further growth was observed across repeated calls in
any run — **no leak**. Plan capacity around a steady-state footprint of
**~2.9-3.2GB per model instance**.

## Inference latency (real model, `n=30` per prompt size)

| Prompt size | mean | median | p95 | p99 | max |
|---|---|---|---|---|---|
| Short | 7180 ms | 4780 ms | 18434 ms | 19236 ms | 19236 ms |
| Medium | 9980 ms | 8009 ms | 19260 ms | 20343 ms | 20343 ms |
| Long | 7228 ms | 6052 ms | 9456 ms | 36188 ms | 36188 ms |

**Caveats, since these numbers are noisy and shouldn't be over-read:**

- Latency is CPU-bound and highly variable run-to-run on this
  (non-GPU) dev machine — median latency for the same benchmark moved
  by ~75% between separate runs. Treat these as directional, not tight
  SLA numbers.
- The long-prompt p99 (36.2s) is a single outlier out of 30 calls
  (p95 was 9.5s) — not a stable tail, a one-off spike.
- Two benchmark runs were manually aborted before completing because
  they appeared to hang for an extended period with no progress. Root
  cause not yet identified — candidates include thermal
  throttling, background system load, or llama.cpp context-shift
  behavior. Worth investigating before relying on this model/hardware
  combination for latency-sensitive production traffic.

## Known follow-up items

- Investigate the occasional multi-second-to-tens-of-second stalls
  noted above (including the two aborted benchmark runs).
- `tests/integration/test_deployment_smoke.py`'s `EXECUTE_TIMEOUT_SECONDS`
  was raised from 90s to 180s to accommodate real inference latency
  variance on `full_pipeline` (3 serialized real inference calls per
  request); re-measure and adjust if the model or hardware changes.