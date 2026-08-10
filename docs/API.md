# TinyAgentOS API Documentation

Reflects the actual behavior of `api/routes.py` as of Day 14 — several
details here differ from the original 30-day plan's boilerplate example
(noted inline where relevant). If routes.py changes, this file will
drift; treat mismatches as a signal to update docs, not code.

**Scope note:** the codebase also contains a separate conversational
agent (`core.agent.BaseAgent`) with tool use (calculator, weather) and
persistent memory (`core.memory.ConversationMemory`) — see the
Architecture doc's "Conversational Agent Layer" section. It has **no
route in `api/routes.py`** — there is no chat/conversational endpoint
in this API today. Every endpoint documented below goes through
`Orchestrator` (summarizer/extractor/critic), not `BaseAgent`.

## Base URL

All endpoints are under the `/api/v1` prefix (defined by
`router = APIRouter(prefix="/api/v1", ...)` in `routes.py`). There is no
unprefixed `/health` — that path 404s (see
`test_health_endpoint_wrong_path_404s` in the deployment smoke tests).

```
http://localhost:8000/api/v1
```

## Authentication

Authentication is controlled by `settings.REQUIRE_AUTH`
(`infrastructure/config.py`). When enabled, every endpoint except
`/health` requires an `X-API-Key` header, and the key must start with
`sk-` — that prefix is checked literally in `verify_api_key()`, not
just documented convention:

```bash
curl -H "X-API-Key: sk-your-key-here" http://localhost:8000/api/v1/tasks
```

| Failure | Status | Response body |
|---|---|---|
| Header missing | 401 | `{"detail": "Missing X-API-Key header"}` |
| Header present but doesn't start with `sk-` | 401 | `{"detail": "Invalid API key format"}` |

If `REQUIRE_AUTH` is `False`, all endpoints accept requests with no key
at all (`verify_api_key()` short-circuits and returns `"no-auth"`).

Note: there are currently two near-identical implementations of
`verify_api_key()` — one in `api/routes.py` (the one actually wired into
every route via `Depends(verify_api_key)`) and another in
`api/dependencies.py` that isn't imported by `routes.py`. If you're
looking to change auth behavior, edit the one in `routes.py` — the copy
in `dependencies.py` currently has no effect on live requests.

## Endpoints

### Health Check

**GET** `/api/v1/health`

Unauthenticated — no `Depends(verify_api_key)` on this route.

Response `200`:
```json
{
  "status": "healthy",
  "timestamp": "2026-08-10T09:15:00.123456"
}
```

`timestamp` is `datetime.utcnow().isoformat()` — a naive (no `Z`/offset
suffix) ISO 8601 string, not the `...Z`-suffixed format shown in the
original plan's example. There is no `"version"` field.

### Create Task

**POST** `/api/v1/tasks`

Requires `X-API-Key` (unless auth is disabled). Pydantic validation
(`TaskRequest`) runs *before* the auth dependency, so a malformed body
returns `422` even with no API key supplied.

Request:
```json
{
  "text": "Your input text here",
  "task_type": "full_pipeline",
  "priority": 1
}
```

- `text` — required, 1–100,000 characters.
- `task_type` — one of `full_pipeline`, `summarize`, `extract`,
  `evaluate`. Not enforced by the request schema itself (it's a plain
  `str` field) — an invalid value passes Pydantic validation and is
  only rejected once it reaches the orchestrator (see the 500 case
  below).
- `priority` — optional, defaults to `1`, bounded `1`–`10` inclusive
  (`Field(ge=1, le=10)` in `TaskRequest`) — `0` or `11`+ returns `422`,
  unlike `task_type`, which has no schema-level bound at all.

Response `200`:
```json
{
  "task_id": "3f9b1a2c-6e4d-4a1b-9c2e-8d7f5a0b1c3e",
  "status": "created",
  "message": "Task created successfully"
}
```

`task_id` above is illustrative, not observed — `orchestrator.py`
hasn't been reviewed for this doc, so the exact ID generation (`uuid4()`
is the likely candidate, but unconfirmed) isn't verified. Treat the
format as unconfirmed until checked against `core/orchestrator.py`.

There is no `created_at` in this response, unlike the plan's original
example — `created_at` only appears later, on `GET /tasks/{task_id}`.

| Failure | Status | Notes |
|---|---|---|
| `text` empty / missing | 422 | Pydantic validation (`TaskRequest.text`, `min_length=1`) |
| Any orchestrator-side error, including an invalid `task_type` | 500 | `{"detail": "Failed to create task"}` — everything from `orchestrator.create_task()` is caught by a blanket `except Exception`. **This includes invalid task types** — there is currently no `400`/`422` path for that case; it's documented current behavior, not necessarily the intended long-term behavior. See `test_invalid_task_type_is_rejected` in the deployment smoke tests, which asserts this 500 explicitly as a regression guard. |

### Execute Task

**POST** `/api/v1/tasks/{task_id}/execute`

Requires `X-API-Key`. Runs the actual pipeline — for `full_pipeline`,
this means real LLM inference across summarizer, extractor, and critic,
which can take anywhere from several seconds to over a minute depending
on hardware.

Response `200`:
```json
{
  "status": "success",
  "task_id": "3f9b1a2c-6e4d-4a1b-9c2e-8d7f5a0b1c3e",
  "result": {
    "summary": "...",
    "extraction": {
      "key_points": ["..."],
      "entities": {},
      "sentiment": "neutral",
      "topics": ["..."]
    },
    "evaluation": {
      "score": 8.5,
      "feedback": "...",
      "strengths": ["..."],
      "weaknesses": ["..."],
      "recommendations": ["..."]
    }
  }
}
```

`result`'s shape depends on `task_type` — `summarize`-only and
`extract`-only tasks return just the corresponding key, not all three.

| Failure | Status | Response body |
|---|---|---|
| Task ID doesn't exist | 404 | `{"detail": "Task {task_id} not found"}` |
| Task is already running | 409 | `{"detail": "Task {task_id} is already running"}` |
| Any other `ValueError` from the orchestrator | 400 | `{"detail": "<the ValueError's message>"}` |
| Any non-`ValueError` exception | 500 | `{"detail": "Task execution failed"}` |

The not-found/already-running/other-ValueError distinction is done by
substring-matching the exception message (`"not found"` /
`"already running"` inside `str(e)`), not by catching distinct
exception types — worth knowing if you're calling this programmatically
and want to branch on the error, since it's message-text-dependent
rather than a stable error code.

**Undocumented case — re-executing an already-completed task.**
`ARCHITECTURE.md` states `Orchestrator` handles "idempotent
re-execution of already-completed tasks" as one of its
responsibilities, but nothing in `routes.py`'s exception handling above
distinguishes "already completed" from any other state — there's no
substring check for it alongside `"not found"` / `"already running"`.
Whether calling `execute` again on a completed task returns the cached
`200` result, a `409`, or something else depends on
`Orchestrator.execute_pipeline()`'s internals, which haven't been
reviewed for this doc. **Unverified — confirm against
`core/orchestrator.py` before relying on this behavior.**

### Get Task Status

**GET** `/api/v1/tasks/{task_id}`

Requires `X-API-Key`.

Response `200`:
```json
{
  "task_id": "3f9b1a2c-6e4d-4a1b-9c2e-8d7f5a0b1c3e",
  "status": "completed",
  "created_at": "2026-08-10T09:15:00.123456+00:00",
  "results": { "summary": "..." },
  "errors": []
}
```

`status` is whatever string the orchestrator's task dict stores under
`"status"` — returned as-is, not validated against a fixed enum at the
route layer.

| Failure | Status | Response body |
|---|---|---|
| Task ID doesn't exist | 404 | `{"detail": "Task {task_id} not found"}` |
| Unexpected exception while looking up the task | 500 | `{"detail": "Failed to get task status"}` |

## Error response shape

All error responses use FastAPI's default shape:

```json
{ "detail": "..." }
```

Not `{"error": "...", "detail": "..."}` — if you're integrating against
this API, key off `detail` only.
