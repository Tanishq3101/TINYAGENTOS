# TinyAgentOS — Knowledge Transfer / Personal Reference

Written for future-you, not a handoff to someone else. Reflects the
project's actual state as of Day 30, not the 30-day plan's generic
template.

## Project Overview

Small-footprint, local, multi-agent LLM framework:
- Python 3.10 (pinned in Dockerfile to match the dev conda env, not 3.11
  as the original plan assumed)
- FastAPI
- Phi-3 Mini (GGUF, via llama-cpp-python)
- SQLite (via SQLAlchemy) — used for API keys; NOT currently used for
  task storage (see Known Gaps below)
- Docker + Kubernetes manifests (ingress, backup CronJob)

Runs locally on Windows in a conda env named `tinyagentos`, project
directory `D:\tinyagentos`, server on `localhost:8000`.

## Key Components

### Core Framework
- `core/orchestrator.py` — task orchestration. Tasks live in an
  **in-memory dict** (`self.tasks`), TTL-evicted after 1 hour by
  default, not persisted to the DB. Thread-safe via `RLock`.
  Summarize/extract steps submitted concurrently to a thread pool —
  but see Known Gaps, this doesn't currently translate to concurrent
  real inference.
- `core/llm_runtime.py` — singleton LLM wrapper. All real inference
  calls serialize behind `self._inference_lock` (fixes a native
  `GGML_ASSERT` crash from concurrent calls). Falls back to a
  no-model "skip" mode if `MODEL_PATH` is missing or
  `TINYAGENT_SKIP_LLM_LOAD=1` is set — used by CI, never by a real
  deployment.
- `agents/` — summarizer, extractor, critic. Extractor returns a JSON
  string; `orchestrator.py` normalizes it into a dict at the
  orchestration boundary.

### API Layer
- `api/app.py` — FastAPI app, rate limiting (slowapi), CORS locked to
  `http://localhost:3000` and `GET`/`POST` + `X-API-Key`/`Content-Type`
  only.
- `api/routes.py` — `/api/v1/health` (no auth, no rate limit),
  `/api/v1/tasks` (create), `/api/v1/tasks/{id}/execute`,
  `/api/v1/tasks/{id}` (status), `/api/v1/api-keys/rotate`.
- `api/dependencies.py` — real API key verification (hashed,
  constant-time compare, revocation check) against the `api_keys`
  table. `REQUIRE_AUTH=false` bypasses this entirely (returns
  `"no-auth"`) — only meant for local dev, never production.
- `api/schemas.py` — `TaskRequest` (`text`, `task_type` — one of
  `full_pipeline`/`summarize`/`extract`/`evaluate`, `priority` 1-10).

### Infrastructure
- `infrastructure/config.py` — all settings, env-var driven. `.env` at
  project root, never committed. `SECRET_KEY` has no default (startup
  fails without a real 32+ char value). `FERNET_KEY` is optional and
  **easy to forget** — see Known Gaps.
- `infrastructure/security.py` — API key hashing/verification, Fernet
  encryption for data at rest.
- `infrastructure/logging.py` — structured JSON logs.

## Deployment

### Local dev
```
conda activate tinyagentos
cd D:\tinyagentos
uvicorn api.app:app --reload
```
Logs land at `logs/app.json` relative to wherever this is run from.

### Docker
```
docker compose -f docker/docker-compose.yml --env-file .env up --build
curl http://localhost:8000/api/v1/health
```
Logs live in the `tinyagentos_logs` named volume, not a host path —
use `docker compose -f docker/docker-compose.yml logs -f` or
`exec ... cat /app/logs/app.json` to read them.

**Before deploying this way**: confirm `.env` has both `SECRET_KEY` and
`FERNET_KEY` set — `docker-compose.yml` was missing the `FERNET_KEY`
passthrough entirely until this was caught during Day 29 review; fixed
now, but worth double-checking your actual `.env` has a real value, not
just that the compose file has the line.

### Kubernetes
```
kubectl apply -f k8s/
kubectl rollout status deployment/tinyagentos
```
Requires `ingress-nginx` and `cert-manager` pre-installed — not
verified from this environment. Backup CronJob's ConfigMap is a
placeholder until generated via `kubectl create configmap ...
--from-file scripts/backup_db.sh`.

### Deployment verification
`scripts/verify_deployment.sh` — health check, real task
creation/retrieval through the API, api_keys table check, log check.
Requires `TINYAGENTOS_API_KEY` set to a real (non-CI) key.

## Monitoring & Observability — current state, honestly

- **What exists**: structured JSON logs (`logs/app.json`), a stall
  watchdog + error tracker wired at startup (`infrastructure/
  stall_watchdog.py`, `infrastructure/error_tracking.py`).
- **What does NOT exist yet**: no `/metrics` endpoint, no Prometheus
  instrumentation, no Grafana dashboard. `docker-compose.yml`
  deliberately dropped the plan's Prometheus/Grafana services because
  nothing in the codebase talks to them. Want to add this — it's a
  real follow-up task, not a config toggle: needs `prometheus_client`
  instrumentation added to the app plus new compose services.

## Known Gaps (carried forward honestly, not smoothed over)

- **Real end-to-end throughput unmeasured.** Inference serializes on a
  lock, so the orchestrator's concurrent summarize/extract submission
  may not translate into real concurrent inference — extrapolated
  (not measured) throughput is ~0.12-0.21 tasks/sec.
- **Tasks are not persisted.** `storage/models.py` has a `TaskModel`,
  but `save_task_execution()` isn't called from the live API flow —
  task history is lost on restart. `docs/PII_HANDLING.md` flagged this
  as worth confirming is intentional.
- **Two benchmark runs stalled unexplained** for an extended period —
  root cause not identified (candidates: thermal throttling,
  background load, llama.cpp context-shift behavior).
- **GitHub branch protection not configured** — a settings change, not
  code, still outstanding.
- **No Prometheus/Grafana** — see Monitoring section above.

## Next Steps (self-directed, not a formal Phase 2 plan)

1. Wire Prometheus instrumentation + a `/metrics` endpoint, add
   Prometheus + Grafana back into `docker-compose.yml`
2. Measure real end-to-end throughput/concurrency against actual
   `LLMRuntime.generate()` calls (extend `benchmark_inference.py` or
   build a new script combining it with the orchestrator)
3. Decide whether task persistence (`save_task_execution()`) should
   actually be wired into the live flow, or whether in-memory-only is
   intentional — document the decision either way
4. Investigate the two unexplained benchmark stalls
5. Turn on GitHub branch protection
