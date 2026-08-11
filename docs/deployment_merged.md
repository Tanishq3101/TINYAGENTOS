# TinyAgentOS Deployment Guide

Reflects the actual `Dockerfile`, `docker/docker-compose.yml`,
`infrastructure/config.py`, and `.github/workflows/ci.yml` as of this
writing — not the original 30-day plan's draft, which used env var
names (`JWT_SECRET`, `SERVER_HOST`, `SERVER_PORT`) that don't exist as
real `Settings` fields, and included a Redis/Prometheus setup that was
never wired to anything and has since been removed from the compose
file.

This merges the previous `DEPLOYMENT.md` (build/config reference) and
`DEPLOYMENT_1.md` (operational runbook) into one document, and
corrects the CI/CD section, which previously described a 4-job
pipeline (lint-and-typecheck, unit-tests, integration-e2e-tests,
build-and-push, with Trivy scanning and a `CI_SECRET_KEY` repo secret)
that does not match the real `ci.yml` — see the CI/CD section below
for what actually runs.

## Pre-Deployment Checklist

- [ ] `pytest -v` passes locally (full suite, ~9-16 min depending on
      whether real model inference runs)
- [ ] `.env` at project root has a real `SECRET_KEY` (32+ chars,
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`) —
      the app refuses to start without one
- [ ] `tinyagentos.db` exists on the host before the first `docker
      compose up` — a missing file mounts as an empty *directory*
      instead, which breaks SQLite (see `ci.yml`'s own "Ensure SQLite
      db file exists" step, which works around exactly this on every
      CI run via `touch` + `chmod 666`)
- [ ] At least one API key issued and reachable from both host and
      container

## Prerequisites

- Docker with BuildKit (multi-stage build)
- A `.env` file at the **project root** (not `docker/`) containing at
  minimum a real `SECRET_KEY` — see "Secrets" below. Never commit this
  file; `.env.example` is the template.
- A model file at `models/phi-3-mini.gguf` relative to the project
  root (mounted into the container — not baked into the image).

## Local Development

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
pytest
uvicorn api.app:app --reload
```

## Build

Multi-stage build, defined in `docker/Dockerfile`:

- **Builder stage** (`python:3.10-slim`) installs `build-essential`,
  `gcc`, `g++` and compiles `llama-cpp-python` from source via
  `pip install -r requirements.txt`.
- **Runtime stage** (`python:3.10-slim`, matching the dev/test
  environment rather than a newer, untested Python version) installs
  only `libgomp1` (the OpenMP runtime `llama-cpp-python` needs at
  runtime — no compiler toolchain), then copies the builder's
  `/usr/local` site-packages plus app code. The final image never
  carries `build-essential`/`gcc`/`g++`.
- Installed to `/usr/local`, not `pip install --user`. This isn't
  cosmetic: the runtime stage drops to a non-root `appuser`, and
  `/root` is mode `700` — `appuser` can't traverse into it to reach
  `/root/.local/bin/uvicorn` even if the file itself were readable.
  `/usr/local` is world-traversable by default, which is what actually
  makes the binaries reachable post-`USER appuser`.
- Runs as non-root `appuser` (uid 1000). `/app`, `/app/models`, and
  `/app/logs` are `chown`'d to `appuser` at build time, before `USER
  appuser` takes effect.
- `HEALTHCHECK` hits `http://localhost:8000/api/v1/health` — not
  `/health`. `api/routes.py`'s router has `prefix="/api/v1"`, so the
  unprefixed path 404s. `--start-period=45s` is sized against a
  measured ~26s local model-load time, not an arbitrary guess.
- `CMD` invokes `uvicorn api.app:app --host 0.0.0.0 --port 8000`
  directly. **This does not read `Settings.HOST`/`Settings.PORT`** —
  those fields are only consulted by `api/app.py`'s `if __name__ ==
  "__main__":` block, not how the container starts it. To bind to a
  different host/port in the container, edit the `CMD` line itself.

Build manually with:
```bash
docker build -f docker/Dockerfile -t tinyagentos .
```
(context is the project root, not `docker/` — see `build.context: ..`
below.)

## Run — Docker Compose

`docker/docker-compose.yml` runs a single `tinyagentos` service. There
is no Redis or Prometheus service — nothing in `Settings`, `storage/`,
or `infrastructure/` currently talks to either. Add them back only
once something actually depends on them (e.g. `storage/cache.py`
graduating from its in-memory "Phase 1" to a real Redis-backed Phase 2).

Because the compose file lives in `docker/` but `.env` lives at the
project root, it must be run with an explicit `--env-file` from the
project root:

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
docker compose -f docker/docker-compose.yml logs -f tinyagentos
```

Verify:
```bash
curl http://localhost:8000/api/v1/health
```

Issue and confirm an API key is visible from both host and container
(catches a "container has a separate DB" failure mode if the bind
mount is ever misconfigured):
```bash
python -m scripts.manage_api_keys list
docker compose -f docker/docker-compose.yml --env-file .env exec tinyagentos python -m scripts.manage_api_keys list
```

Compose specifics:
- `build.context: ..` — the build context is the project root (one
  level up from `docker/`), so the `Dockerfile`'s `COPY . .` picks up
  the whole app, not just the `docker/` directory.
- Port `8000:8000`.
- Bind mounts: `../logs:/app/logs`, `../models:/app/models`, and
  `../tinyagentos.db:/app/tinyagentos.db` — logs, the model file, and
  the SQLite DB all live outside the image and persist across
  container rebuilds. **The DB file must already exist on the host
  before the first `up`** — a missing file mounts as an empty
  directory instead, breaking SQLite. `ci.yml` handles this with
  `touch tinyagentos.db && chmod 666 tinyagentos.db` before starting
  compose; do the same locally if you don't already have this file.
- `SECRET_KEY=${SECRET_KEY}` — pulled from the environment/`.env` file
  at compose time, not hardcoded in the compose file itself.
- `restart: unless-stopped`.
- Single bridge network (`tinyagentos-network`) — nothing else is on
  it yet, so no network policy is meaningfully enforced today; this
  becomes relevant the moment a second service (e.g. a real database)
  joins it.

**Not set, despite being a stated goal in the original plan:** no
`mem_limit`, `cpus`, or Compose `deploy.resources` block — the
container can currently consume unbounded host memory/CPU. Add
resource limits before any real deployment, especially given
`llama-cpp-python` inference is the dominant resource cost here.

## Secrets

`SECRET_KEY` has no default in `Settings` — the app **refuses to
start** without one. This is enforced by a `field_validator`, not just
convention, and it does two checks:

1. Must be at least 32 characters.
2. Must not be a placeholder value — the validator explicitly rejects
   `change_this_in_production`, `changeme`, and `secret`
   (case-insensitive, whitespace-trimmed).

Generate a real one with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put it in a project-root `.env` file, never in `.env.example`, never
committed, and never pasted into chat/logs/tickets. If a real key is
ever exposed, treat it as compromised and generate a new one — there
is currently no key-rotation mechanism, so "rotate" here means:
generate a new value, update `.env`, restart.

`ci.yml` uses its own hardcoded test-only value
(`test-secret-key-not-for-production-use-only-in-ci-32chars-min`) set
inline in the workflow — this is a CI convenience, not a real secret,
and is never used outside CI runs.

## Environment variables

These map directly to real `Settings` fields in
`infrastructure/config.py`.

| Variable | Settings field | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | `SECRET_KEY` | *(none — required)* | 32+ chars, rejected if a known placeholder. App won't start without it. |
| `REQUIRE_AUTH` | `REQUIRE_AUTH` | `True` | Even when `True`, current auth is a `sk-` prefix format check, not a real credential check. |
| `DEBUG` | `DEBUG` | `False` | Compose sets this explicitly to `false`; default is already off. |
| `LOG_LEVEL` | `LOG_LEVEL` | `INFO` | |
| `DATABASE_URL` | `DATABASE_URL` | `sqlite:///./tinyagentos.db` | Root-level SQLite file. Confirmed against `ci.yml`'s bind mount (`../tinyagentos.db:/app/tinyagentos.db`) and its seed step (`Database('sqlite:///./tinyagentos.db')`). No network exposure, no encrypted connection, no automated backups configured. |
| `MODEL_PATH` | `MODEL_PATH` | `models/phi-3-mini.gguf` | Compose overrides to `/app/models/phi-3-mini.gguf` to match the container mount path. |
| `HOST` | `HOST` | `127.0.0.1` | **Not read by the container's actual `uvicorn` CMD** — see Build section. Only affects `python api/app.py` direct runs. |
| `PORT` | `PORT` | `8000` | Same caveat as `HOST`. Validated `1`–`65535` at startup regardless. |
| `N_THREADS` | `N_THREADS` | `4` | LLM inference threads. |
| `N_CTX` | `N_CTX` | `2048` | LLM context window. |
| `N_GPU_LAYERS` | `N_GPU_LAYERS` | `0` | `0` = CPU-only inference. |
| `TEMPERATURE` | `TEMPERATURE` | `0.6` | |
| `ALGORITHM` | `ALGORITHM` | `HS256` | Present in `Settings` but currently unused — no JWT encode/decode happens anywhere in the live auth path. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Same caveat — no token-expiry logic currently consumes this. |
| `API_KEY_HEADER` | `API_KEY_HEADER` | `X-API-Key` | Declared in `Settings`, but `verify_api_key()` in `routes.py` reads the header via a hardcoded `Header(None)` parameter named `x_api_key` — **not** by referencing this setting. Changing this env var alone would not change which header name is actually checked; unverified whether this is intentional. |
| `TINYAGENT_SKIP_LLM_LOAD` | *(not a Settings field — read directly in `core/llm_runtime.py`)* | unset | Set to `"1"` in `ci.yml` for both the container and the host-side pytest run, so `LLMRuntime` skips loading the real GGUF model. `/health` then reports `model_loaded: false` — expected in CI, not a failure. |

`extra = "ignore"` is set on `Settings.Config` — an unrecognized env
var (e.g. a typo, or a leftover from the plan's draft like
`JWT_SECRET`) is silently dropped, not an error. If an env var doesn't
appear to be taking effect, check the spelling against the table above
rather than assuming it's a bug elsewhere.

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs **one job**, `lint-and-test`, on every
push to `main`/`feature/**` and every PR to `main`. Steps, in order:

1. Checkout code
2. Verify `docker/docker-compose.yml` and `docker/Dockerfile` exist
3. Set up Python 3.10.20 (pip-cached against `requirements.txt`)
4. `pip install -r requirements.txt`
5. Lint: `flake8 .`
6. Format check: `black --check .`
7. Type check: `mypy .`
8. Ensure the SQLite DB file exists on the runner (`touch
   tinyagentos.db && chmod 666 tinyagentos.db`) — without this, Docker
   creates a directory at that bind-mount path instead of a file (see
   the "Run — Docker Compose" section above)
9. Start Docker Compose, with `SECRET_KEY` set inline to a CI-only
   test value and `TINYAGENT_SKIP_LLM_LOAD=1` (no real model in CI)
10. Dump Compose status and logs (`if: always()`, so this runs even on
    failure)
11. Poll `/api/v1/health` for up to 60 attempts (2s apart) before
    failing the job
12. Seed a `sk-test` API key directly via `storage.database.Database`
    and `infrastructure.security.SecurityManager` (not a raw SQL
    insert — this guarantees schema/hashing match the real app; see
    inline comments in `ci.yml` for the full history of why)
13. `pytest --cov=. --cov-report=xml --cov-report=term-missing`, also
    with `TINYAGENT_SKIP_LLM_LOAD=1` set for the host-side process
14. Upload `coverage.xml` as a build artifact
15. Tear down Compose (`down -v`, `if: always()`)

**Not currently in `ci.yml`**, despite being described in an earlier
draft of this document:
- No separate lint/unit/e2e/build-and-push job split — it's all one
  job, one runner, sequentially
- No `bandit` step (being added — not yet wired in as of this writing)
- No Trivy or other image security scan
- No `docker build`-and-push to a registry (`ghcr.io` or otherwise) —
  CI builds and runs the image locally on the runner via Compose, for
  testing only, and does not publish it anywhere
- No `CI_SECRET_KEY` repository secret — the test `SECRET_KEY` is
  hardcoded directly in the workflow file instead

**Practical consequence:** because CI does not build-and-push an
image, there is currently no automated path from "tests pass on
`main`" to "a deployable image exists in a registry." The Kubernetes
section below assumes you build and push `IMAGE_PLACEHOLDER` manually
until that gap is closed.

## Kubernetes Deployment

**Read this before applying anything:** `k8s/deployment.yaml` is
deliberately `replicas: 1`. This app uses SQLite; more than one
replica writing to the same DB file will corrupt data. Do not scale
this deployment until `DATABASE_URL` points at a real networked
database.

**Known inconsistency, not yet resolved:** `k8s/deployment.yaml` sets
`DATABASE_URL=sqlite:///./data/tinyagentos.db` (a `data/` subdirectory,
backed by a separate PVC), but the confirmed real path everywhere else
(`ci.yml`'s bind mount, the env var table above) is root-level
`sqlite:///./tinyagentos.db`, no subdirectory. One of these is stale.
Resolve before actually applying this manifest to a real cluster —
don't assume the k8s file's path is correct just because it's written
with confidence in its own header comment.

```bash
# One-time: create the secret (never commit a real value)
kubectl create secret generic tinyagentos-secrets \
  --from-literal=secret-key="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"

# Replace IMAGE_PLACEHOLDER in k8s/deployment.yaml with your real
# ghcr.io/<owner>/<repo>:<tag> first (built and pushed manually --
# see CI/CD section above), then:
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl rollout status deployment/tinyagentos
```

Verify:
```bash
kubectl port-forward svc/tinyagentos 8000:80
curl http://localhost:8000/api/v1/health
```

## Rollback Procedure

**Docker:**
```bash
docker compose -f docker/docker-compose.yml --env-file .env down
git checkout <previous-known-good-sha>
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

**Kubernetes:**
```bash
kubectl rollout history deployment/tinyagentos
kubectl rollout undo deployment/tinyagentos
```

## Monitoring Post-Deployment

- Health: `curl <base-url>/api/v1/health` (confirmed shape:
  `{"status": "healthy", "timestamp": ...}`)
- Docker logs: `docker compose -f docker/docker-compose.yml logs -f tinyagentos`
- Kubernetes logs: `kubectl logs -f deployment/tinyagentos`
- `last_used_at` on an API key row updating after a real request is a
  useful "auth actually worked end-to-end" signal

### What's Not Configured / Known Gaps

- No resource limits on the Docker container (`mem_limit`, `cpus`, or
  Compose `deploy.resources`) — unbounded host memory/CPU use possible
- No automated image security scanning (Trivy/Grype/`docker scan`) in
  CI
- No key-rotation mechanism for `SECRET_KEY`
- No real API-key issuance/revocation system — `REQUIRE_AUTH=true`
  currently gates on key *format*, not a stored, revocable credential
- `bandit` static analysis not yet wired into `ci.yml` — currently
  only run manually/locally
- No CI build-and-push step — see CI/CD section above
- `k8s/deployment.yaml`'s `DATABASE_URL` path inconsistency — see
  Kubernetes section above
- No Prometheus/Grafana wiring confirmed — `infrastructure/metrics.py`
  exists and is tested, but nothing in this repo confirms an actual
  scrape endpoint or dashboard is deployed
- No `scripts/download_model.py` confirmed to exist, despite being
  listed in the original 30-day plan's architecture diagram — CI
  downloads the model directly via `huggingface-cli` in the
  Kubernetes initContainer instead; local setup still relies on
  however you originally obtained `models/phi-3-mini.gguf`
- SQLite is a real long-term constraint, not just a k8s replica-count
  issue — worth planning a Postgres migration before this needs to
  scale beyond a single instance