# TinyAgentOS Architecture

Reflects the system as actually built through Day 14. Where this
differs from the original 30-day plan's sketch, the difference is
called out — the plan's diagram included a Redis cache and Prometheus
monitoring layer that were never built; this doc only describes what
runs today.

**Important naming collision:** there are two separate, unrelated agent
systems in this codebase. `agents.base.Agent` (this doc's "Agents"
section below) is the ABC behind `SummarizerAgent`/`ExtractorAgent`/
`CriticAgent`, driven by `Orchestrator`. `core.agent.BaseAgent` is a
completely different, standalone conversational agent — see
"Conversational Agent Layer" below. Nothing enforces the name
difference at a glance; watch for this when navigating the codebase or
onboarding someone new.

**Not diagrammed:** `core/tools/` (the `calculator`/`weather` tool
implementations themselves) haven't been reviewed for this doc — only
their registration/usage pattern via `core/agent.py`'s `TOOLS` registry
is covered below.

## System Design

```
┌──────────────────────────────────────────────┐
│  FastAPI HTTP Layer (api/)                    │
│  app.py · routes.py · middleware.py · schemas.py │
├──────────────────────────────────────────────┤
│  Orchestrator & Pipeline (core/)              │
│  orchestrator.py · pipeline.py                │
├──────────────────────────────────────────────┤
│  Agents (agents/)                             │
│  summarizer.py · extractor.py · critic.py     │
├──────────────────────────────────────────────┤
│  LLM Runtime (core/llm_runtime.py)            │
│  llama-cpp-python singleton, Phi-3 Mini GGUF  │
├──────────────────────────────────────────────┤
│  Storage (storage/)          Infra (infrastructure/) │
│  database.py · cache.py      config.py · logging.py  │
│  models.py                   security.py · retry.py  │
│                               validators.py · metrics.py │
└──────────────────────────────────────────────┘
```

There is no cache or monitoring layer running in production yet —
`storage/cache.py`'s own docstring calls itself "Phase 1" with a
Redis-backed version as a planned Phase 2, and `docker-compose.yml`
explicitly removed a draft Redis/Prometheus setup because nothing
currently talks to either. Day 20 of the plan ("Monitoring,
Observability & Error Tracking") hasn't happened yet as of Day 14.

## Component Responsibilities

### API Layer (`api/`)

- `app.py` — FastAPI app construction, `TrustedHostMiddleware` (allows
  only `localhost`/`127.0.0.1` — tighten before any real deployment
  behind a real hostname), CORS, the orchestrator singleton lifecycle.
- `routes.py` — all four endpoints (`/health`, `POST /tasks`,
  `POST /tasks/{id}/execute`, `GET /tasks/{id}`). Owns the *actual*
  `verify_api_key()` dependency used at runtime (see Security doc for
  why `api/dependencies.py`'s copy doesn't count).
- `middleware.py` — `LoggingMiddleware`, logs method/path/client on
  request and status/timing on response for every call.
- `schemas.py` — Pydantic request/response models. Validation here
  (e.g. `TaskRequest.text` length) runs before auth, since FastAPI
  validates path-operation body params before resolving `Depends()`.

Route handlers wrap every orchestrator call in
`fastapi.concurrency.run_in_threadpool` — without this, a synchronous,
LLM-bound `execute_pipeline()` call would block the single asyncio
event loop for the full duration of inference, starving every other
concurrent request including `/health`. This was a real bug found via
the Day 14 deployment smoke tests (health checks timing out during a
`full_pipeline` run) before being fixed.

### Orchestrator & Pipeline (`core/orchestrator.py`, `core/pipeline.py`)

- `Orchestrator` owns task lifecycle (`create_task` → `execute_pipeline`
  → status tracking), input validation, a bounded thread pool for
  running independent pipeline steps (summarizer + extractor)
  concurrently, task TTL/eviction, and idempotent re-execution of
  already-completed tasks.
- `Pipeline`/`PipelineStep` (`core/pipeline.py`) is a separate, generic,
  reusable step-chaining primitive with its own validation, timing, and
  execution history. Orchestrator's `_run_full_pipeline` does **not**
  currently use `Pipeline` — it implements summarize/extract/critic
  sequencing manually via its own thread pool. `Pipeline` is available
  as a building block but isn't the thing actually running full-pipeline
  requests today.
- `PipelineStep.retry_policy` is a field that's carried through but
  intentionally *not* enforced by `Pipeline.execute()` itself — retry
  is the caller's responsibility (wrap the agent callable with
  `infrastructure.retry.retry_on_exception` before handing it to
  `PipelineStep`, if retry is wanted for a given step).

### Agents (`agents/`)

Each agent (`SummarizerAgent`, `ExtractorAgent`, `CriticAgent`) builds a
prompt, calls `LLMRuntime.generate()`, and returns
`{"status": "success"/"error", "output"/"error": ...}` — the contract
`Orchestrator._run_agent_step()` depends on. `CriticAgent` is the odd
one out: it requires `summary=` and `extraction=` keyword arguments
(the other two agents' outputs) to evaluate against, and does its own
best-effort text parsing of the model's `Score:`/`Feedback:`/etc.
free-text response (`_parse_evaluation`) rather than requiring
structured output from the model.

`agents/base.py`'s `Agent` ABC (`AgentConfig`, `execute()`,
`AgentMetrics`) wraps every subclass call with a try/except that always
returns `{"status": "success"/"error", ...}` rather than letting
exceptions propagate — this is what lets `Orchestrator._run_agent_step()`
treat "the agent raised" and "the agent returned an error status"
uniformly. Note `AgentMetrics` here is a small, self-contained
dataclass, deliberately separate from `infrastructure.metrics.MetricsCollector`
(which `Orchestrator` uses for its own task-level metrics) — the
codebase currently has two independent, non-unified metrics
mechanisms, one per-agent-call and one per-task.

### Conversational Agent Layer (`core/agent.py`, `core/memory.py`, `core/tools/`)

**Not part of the request path above** — `api/routes.py` has no route
that constructs or calls this. It's a standalone component, most
likely a Day 6-7 deliverable (matches `tests/unit/test_day7.py`'s
`test_calculator`/`test_weather`/`test_llm_query` coverage) that
predates the Orchestrator/pipeline system and hasn't been wired into
the HTTP API.

- `core.agent.BaseAgent` — a ReAct-style conversational agent, distinct
  from `agents.base.Agent` above (see naming-collision warning up top).
  `act(prompt)` asks the model to decide between `{"action": "tool",
  "tool_name": ..., "tool_input": ...}` or `{"action": "llm"}` via a
  JSON-only decision prompt, with retry-once-on-bad-JSON and multiple
  regex-based fallback extraction strategies (`_extract_json`) for when
  the model doesn't return clean JSON. `think(prompt)` is the plain
  LLM-response path (no tool use), used both as the fallback when tool
  selection fails and as the direct call for non-tool prompts.
- Tool inputs get task-specific cleanup before execution
  (`_clean_tool_input`) — e.g. for `calculator`, natural-language
  phrasings ("15% of 200", "times", "divided by") are converted to
  arithmetic symbols and then filtered down to an allowed character set
  (`0123456789+-*/(). `) before being handed to the tool. **What the
  `calculator` tool itself does with that cleaned string
  (`core/tools/calculator.py`) hasn't been reviewed for this doc** — if
  it evaluates the string via Python's `eval()` rather than a
  restricted parser, the character-set filter is worth auditing as a
  security boundary, not just a UX convenience. See Security doc.
- `core.memory.ConversationMemory` — a bounded rolling window (default:
  10 turns / 2000 chars, oldest-first eviction) per `session_id`,
  optionally persisted to `memory_store/<session_id>.json` between
  process restarts. `BaseAgent` uses one of these per instance (shared
  instances can also be injected) to inject prior turns into both
  `think()` and `act()`'s prompts, so follow-up questions work without
  the caller re-stating context. See Security doc for the data-at-rest
  implications of this persistence.
- Both `BaseAgent` and the pipeline agents share the same
  `LLMRuntime` singleton and therefore the same `_inference_lock` — a
  long-running `execute_pipeline()` call and a concurrent `BaseAgent.act()`
  call would serialize against each other, not just against each other's
  own kind.

### LLM Runtime (`core/llm_runtime.py`)

A true singleton (`__new__` returns the same instance every call)
wrapping one `llama_cpp.Llama` model instance (Phi-3 Mini GGUF).
`generate()` serializes all inference calls through a single
`threading.Lock` (`_inference_lock`) — this was added after a real
crash: `Orchestrator` submitting summarizer and extractor to run
concurrently caused two threads to call into the same `llama.cpp`
context simultaneously, producing a native `GGML_ASSERT` that aborted
the whole process (not a catchable Python exception). The lock trades
away true parallel inference for correctness — summarizer and extractor
still get *submitted* concurrently by the orchestrator, they just queue
on this lock for their actual model call rather than racing on it.

### Storage (`storage/`)

- `database.py` — SQLAlchemy `Database` class over `DATABASE_URL`
  (SQLite by default per `docker-compose.yml`:
  `sqlite:///./tinyagentos.db`). Sessions are context-managed
  (`db.session()`) — commits on success, rolls back and re-raises on
  error, always closes. This fixed a real connection leak in the plan's
  original sketch, which opened sessions and never closed them.
- `cache.py` — `InMemoryCache`, thread-safe (`threading.Lock`),
  per-entry TTL, lazy expiry on `get()` plus an explicit
  `purge_expired()` for entries that are set and never read again.
  In-process only — does not survive a restart and isn't shared across
  multiple app instances/workers.
- `models.py` — SQLAlchemy models (`TaskModel` and others per Day 5's
  test coverage; not reviewed in detail for this doc).

### Infrastructure (`infrastructure/`)

- `config.py` — `Settings` (pydantic-settings), `get_settings()`
  (lru_cached). Refuses to start without an explicit `SECRET_KEY` — see
  Security doc.
- `logging.py` — module-level `logger` plus `log_info`/`log_error`/etc.
  helpers.
- `security.py` — `SecurityManager` (Fernet encryption,
  API-key hashing/verification, HMAC request signatures). **Built, but
  not currently wired into the live request path** — see Security doc,
  this is the most important gap in this whole document.
- `retry.py` — `RetryPolicy` and `retry_on_exception()`, usable by any
  caller (e.g. wrapping an agent callable before handing it to a
  `PipelineStep`); not applied automatically anywhere.
- `validators.py`, `metrics.py` — request/task input validation and
  in-process metrics collection, per Day 8-9's deliverables.

## Deployment

Multi-stage `Dockerfile`: a `builder` stage compiles
`llama-cpp-python` from source (needs `build-essential`/`gcc`/`g++`),
and the runtime stage copies only the built site-packages
(`/usr/local`) plus app code — keeps the final image free of build
toolchain weight. Runs as a non-root `appuser` (uid 1000); the app
directory, `models/`, and `logs/` are chowned to that user at build
time. `HEALTHCHECK` hits `/api/v1/health` (not the plan's original
`/health` — the router's `/api/v1` prefix would 404 that path), with a
45s start-period sized to actual measured model-load time (~26s
locally) rather than an arbitrary guess.

`docker-compose.yml` runs a single `tinyagentos` service — no Redis, no
Prometheus (removed from the plan's draft; nothing currently uses
either). Environment variables map directly to real `Settings` fields
(`HOST`, `PORT`, `SECRET_KEY`, `LOG_LEVEL`, `DEBUG`, `REQUIRE_AUTH`,
`DATABASE_URL`) — the plan's original draft used names like `JWT_SECRET`
and `SERVER_HOST` that don't exist as `Settings` fields, which
pydantic-settings would have silently ignored rather than erroring.
`SECRET_KEY` has no default and must come from a project-root `.env`
file, loaded via `--env-file .env` since the compose file itself lives
in `docker/`.
