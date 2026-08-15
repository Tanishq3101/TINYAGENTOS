# TinyAgentOS v0.1.0 — Release Notes

## Overview

First production-track release of TinyAgentOS: a small-footprint,
local, multi-agent LLM framework (FastAPI + llama.cpp/Phi-3 Mini) with
a summarizer/extractor/critic pipeline, task orchestration, monitoring,
and a hardened security posture.

## New Features

- REST API (`api/routes.py`) for task creation, execution, status
  polling, health checks, and API key rotation
- Multi-agent `full_pipeline` (summarizer → extractor → critic),
  plus standalone `summarize` / `extract` task types
- API key authentication with real credential verification (hashed,
  constant-time compare, revocation support) — not a format check
- Rate limiting (slowapi) on every task-mutating endpoint, keyed by
  client IP
- CORS locked to the actual origin/methods/headers the app uses, not
  a wildcard
- Encryption at rest for sensitive stored fields (Fernet, via
  `infrastructure/security.py` + `config.py`'s `FERNET_KEY`)
- Structured logging with input fingerprinting — raw task text is
  never written to logs, only length + a truncated SHA-256 hash
- Stall watchdog and error tracking wired at startup (`infrastructure/
  stall_watchdog.py`, `infrastructure/error_tracking.py`)
- TLS-terminating ingress (`k8s/ingress.yaml`) via ingress-nginx +
  cert-manager, forcing HTTP→HTTPS
- Encrypted, automated database backups (`scripts/backup_db.sh` +
  `k8s/backup-cronjob.yaml`) — SQLite `.backup`, gpg AES256, S3
  upload with server-side encryption

## Improvements

- **Fixed a process-crashing native assertion**
  (`GGML_ASSERT(buf != NULL ...)`): concurrent LLM inference calls
  from the orchestrator's parallel summarize/extract steps were
  hitting `llama.cpp`'s decode path simultaneously. Inference is now
  serialized behind a lock in `LLMRuntime.generate()`.
- **Fixed request-handling deadlock under load**: orchestrator calls
  were synchronous inside `async def` route handlers, so a single
  in-flight `full_pipeline` execution blocked the entire event loop —
  even `/health` would time out. All three blocking orchestrator
  calls now run via `run_in_threadpool`.
- **Fixed a startup-ordering gap**: the LLM model now loads eagerly
  at container boot (inside the health-check start-period window)
  instead of lazily on the first real request, so a broken
  `MODEL_PATH` fails loudly at startup rather than as a mysterious
  ~26s stall on someone's first task.
- Orchestrator's module-level singleton changed from eager (any
  import of `core.orchestrator` triggered a multi-GB model load) to
  lazy via PEP 562 `__getattr__` — tooling and tests that only need
  the `Orchestrator` class no longer pay that cost.
- Extractor JSON output is normalized into a parsed dict at the
  orchestration boundary, with a safe fallback on malformed JSON,
  instead of pushing raw-string handling onto every downstream
  consumer.
- Bounded, TTL-based in-memory task store — prevents unbounded memory
  growth from long-running processes accumulating every task forever.

## Security Hardening (Day 28)

- Rate limiting, CORS narrowing, API key rotation, dependency
  scanning, encryption at rest, TLS/ingress, network policy, DB
  backup encryption, and PII handling documentation are all in place.
  Full detail in `docs/PII_HANDLING.md` and the security checklist.
- **Still open, not a code fix**: GitHub branch protection requiring
  PR approval before merge to `main` — configured in GitHub's repo
  settings, not something this codebase can enforce on its own.

## Performance (Day 18-19 measurements, consolidated Day 28)

- Orchestration overhead (locking, scheduling, bookkeeping) is
  14-31ms even for the full 3-agent pipeline — real inference, not
  orchestration, is the latency bottleneck by 2-3 orders of magnitude.
- Real inference latency (CPU-only dev hardware): P50 ranges 4.8-8.0s
  depending on prompt size, with noisy P95/P99 tails (see
  `docs/PERFORMANCE_RESULTS.md` for the full breakdown and caveats).
- Model load: ~780MB RSS; first inference call adds a further
  ~1.9-2.2GB one-time KV-cache allocation. Steady-state footprint
  ~2.9-3.2GB per model instance, no observed leak across repeated calls.

## Bug Fixes

- Fixed native crash from concurrent inference calls (see above)
- Fixed event-loop blocking under concurrent load (see above)
- Fixed mypy false positives around `SECRET_KEY`'s required-field
  handling and a malformed `type: ignore` comment that was
  misinterpreted as attaching to the wrong line
- Fixed duplicated, divergent `verify_api_key()` implementations
  (one live in `routes.py`, one dead in `dependencies.py`) — now a
  single implementation in `dependencies.py`

## Breaking Changes

None.

## Deprecations

None.

## Known Issues

- **No verified real-world throughput/concurrency number.**
  `LLMRuntime.generate()` serializes all inference behind a lock, so
  the orchestrator's concurrent summarize/extract optimization has
  only been benchmarked against fake, non-locking agents — its real
  benefit under actual inference is unconfirmed. Extrapolated
  (not measured) real throughput is roughly 0.12-0.21 tasks/sec.
- Two benchmark runs stalled for an extended period for an
  unidentified reason (candidates: thermal throttling, background
  load, llama.cpp context-shift behavior) — not yet root-caused.
- `k8s/backup-cronjob.yaml` mounts the same PVC as the app
  (`ReadWriteOnce` per `deployment.yaml`) — whether the backup Job can
  mount it concurrently depends on your storage class/CSI driver and
  hasn't been tested against a real cluster.
- The backup script's ConfigMap in `k8s/backup-cronjob.yaml` is a
  placeholder — it must be generated for real via `kubectl create
  configmap ... --from-file` before applying, or the CronJob runs a
  broken script.
- `k8s/ingress.yaml` assumes `ingress-nginx` and `cert-manager` are
  already installed in the target cluster — neither is verified from
  here.
- Latency is highly variable on CPU-only hardware — the same
  benchmark moved ~75% between runs. Treat published latency numbers
  as directional, not an SLA.

## Upgrade Path

```bash
git checkout v0.1.0
docker-compose up -d
# or: kubectl apply -f k8s/
curl -f http://localhost:8000/api/v1/health
```

## Support

See `docs/` for API, architecture, security, and deployment guides.
