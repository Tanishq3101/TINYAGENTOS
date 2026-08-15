# Alpha Testing Plan

Test scenarios below are written against the **actual** `core/orchestrator.py`
implementation (Day 10), not the generic Day 26-27 plan template. Key
implementation details that shape these scenarios:

- Input limits are **character-based**, not word-based: `max_input_length`
  defaults to 100,000 characters (`Orchestrator.__init__`).
- `summarizer` and `extractor` steps run **concurrently** via a bounded
  `ThreadPoolExecutor` (`max_parallel_workers`, default 4); `critic` waits
  for both. This is not the strictly sequential pipeline the original plan
  assumed.
- Each step has a `step_timeout_seconds` (default 60s). A timed-out step
  fails that step only — it does not hang the task.
- Re-running an already-`COMPLETED` task via `execute_pipeline()` is
  **idempotent** — it returns the cached `results` dict without re-invoking
  the LLM.
- Resource checks (`enable_resource_checks`, default on) gate pipeline start
  via `ResourceMonitor.check_resource_availability()`, but only if
  `infrastructure.resource_monitor` is importable — it degrades gracefully
  (checks skipped) if that module or `psutil` is missing.
- The LLM runtime is **CPU-only by default** (`N_GPU_LAYERS: 0` in
  `config.py` / `default.yaml`), `n_ctx: 2048`, `n_threads: 4`.
- API auth is required by default (`REQUIRE_AUTH: true`, `X-API-Key`
  header) with a rate limit of 60 requests/minute (`default.yaml`).
- Supported `task_type` values: `full_pipeline`, `summarize`, `extract`,
  `evaluate` (`SUPPORTED_TASK_TYPES`).

## Test Scenarios

### Scenario 1: Basic Full Pipeline
- Input: ~1,000-word document, `task_type="full_pipeline"`
- Expected: `summary` (str), `extraction` (dict — key_points/entities/sentiment/topics), `evaluation` (critic output) all populated
- Success Criteria: `orchestrator.get_task(task_id)['status'] == TaskStatus.COMPLETED`; all three result keys present and non-empty

### Scenario 2: Oversized Input
- Input: text exceeding 100,000 characters
- Expected: `create_task()` raises `InvalidTaskInputError` immediately — the task is never registered, no LLM call is made
- Success Criteria: exception raised before any agent execution; no task_id returned

### Scenario 3: Concurrent Independent Steps
- Input: single `full_pipeline` task
- Expected: `summarizer` and `extractor` execute concurrently on the thread pool; `critic` starts only after both resolve
- Success Criteria: wall-clock time for the full pipeline is measurably less than the sum of the three steps run sequentially (verifies concurrency is actually happening, not just configured)

### Scenario 4: Thread Pool Saturation (Concurrent Task Submissions)
- Input: submit more simultaneous tasks than `max_parallel_workers` (default 4) — e.g. 10 tasks at once
- Expected: excess step-executions queue on the bounded `ThreadPoolExecutor` rather than erroring; each step still respects its own `step_timeout_seconds`
- Success Criteria: all 10 tasks reach `COMPLETED` or a clean `FAILED` (with a sanitized error) — no unhandled exceptions, no deadlock

### Scenario 5: Step Timeout
- Input: a task where one agent step is mocked/forced to exceed `step_timeout_seconds` (default 60s)
- Expected: `_resolve_future()` catches the timeout, cancels the future, and the pipeline raises `StepExecutionError` for that named step
- Success Criteria: task ends in `FAILED` with a sanitized, non-leaking error message; full traceback appears only in the structured log, not in the returned error

### Scenario 6: Idempotent Re-execution
- Input: call `execute_pipeline(task_id)` twice on the same completed task_id
- Expected: second call returns the cached `results` dict without re-running any agent
- Success Criteria: identical result object on both calls; no duplicate LLM inference (verify via call count on a mock agent)

### Scenario 7: Resource-Constrained Start
- Input: a task submitted while `ResourceMonitor.check_resource_availability()` reports unavailable (or with `infrastructure.resource_monitor` / `psutil` uninstalled)
- Expected: if the module is present and reports low resources, task fails fast with `"insufficient_resources"` before any agent runs. If the module is absent, the check is skipped and execution proceeds normally.
- Success Criteria: correct behavior in both branches; no crash either way

### Scenario 8: Invalid / Missing Agent Configuration
- Input: `create_task(..., task_type="summarize")` on an `Orchestrator` instantiated without a `summarizer` agent
- Expected: `InvalidTaskInputError` at task creation, per `_TASK_TYPE_REQUIRED_AGENTS` validation
- Success Criteria: fails at `create_task()`, not later at `execute_pipeline()`

### Scenario 9: Malformed Extractor Output
- Input: extractor agent returns a non-JSON string (contract violation)
- Expected: `_normalize_extraction()` falls back to `{"key_points": [], "entities": {}, "sentiment": "neutral", "topics": []}` instead of raising
- Success Criteria: pipeline completes successfully with the fallback structure; no unhandled `JSONDecodeError`

### Scenario 10: API-Level Auth & Rate Limiting
- Input: requests to the FastAPI layer without a valid `X-API-Key`, and requests exceeding 60/minute
- Expected: unauthenticated requests rejected (per `REQUIRE_AUTH: true`); requests beyond the rate limit rejected per `rate_limit_per_minute: 60`
- Success Criteria: correct 4xx responses in both cases; orchestrator is never reached for rejected requests
