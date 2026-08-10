# TinyAgentOS Deployment Guide

Reflects the actual `Dockerfile`, `docker/docker-compose.yml`, and
`infrastructure/config.py` as of Day 17 — not the original 30-day
plan's draft, which used env var names (`JWT_SECRET`, `SERVER_HOST`,
`SERVER_PORT`) that don't exist as real `Settings` fields and would
have been silently ignored by pydantic-settings, and which included a
Redis/Prometheus setup that was never wired to anything and has since
been removed from the compose file.

## Prerequisites

- Docker with BuildKit (multi-stage build)
- A `.env` file at the **project root** (not `docker/`) containing at
  minimum a real `SECRET_KEY` — see "Secrets" below. Never commit this
  file; `.env.example` is the template.
- A model file at `models/phi-3-mini.gguf` relative to the project
  root (mounted into the container — not baked into the image).

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
  makes the binaries reachable post-`USER appuser`. (This was a real
  "Permission denied" failure, not a hypothetical one.)
- Runs as non-root `appuser` (uid 1000). `/app`, `/app/models`, and
  `/app/logs` are `chown`'d to `appuser` at build time, before `USER
  appuser` takes effect.
- `HEALTHCHECK` hits `http://localhost:8000/api/v1/health` — not
  `/health`. `api/routes.py`'s router has `prefix="/api/v1"`, so the
  unprefixed path 404s; the plan's original draft used the wrong path.
  `--start-period=45s` is sized against a measured ~26s local model-load
  time, not an arbitrary guess.
- `CMD` invokes `uvicorn api.app:app --host 0.0.0.0 --port 8000`
  directly. **This does not read `Settings.HOST`/`Settings.PORT`** —
  those fields are only consulted by `api/app.py`'s `if __name__ ==
  "__main__":` block (i.e. running `python api/app.py` directly, not
  how the container starts it). If you need the container to bind to a
  different host/port, edit the `CMD` line itself; setting `HOST`/`PORT`
  env vars alone won't change what the container actually binds to.

Build manually with:
```bash
docker build -f docker/Dockerfile -t tinyagentos .
```
(context is the project root, not `docker/` — see the compose file's
`context: ..` below for why).

## Run — docker compose

`docker/docker-compose.yml` runs a single `tinyagentos` service. There
is no Redis or Prometheus service — nothing in `Settings`, `storage/`,
or `infrastructure/` currently talks to either; they were dropped from
the plan's original draft rather than left in unused. Add them back
only once something actually depends on them (e.g. `storage/cache.py`
graduating from its in-memory "Phase 1" to a real Redis-backed Phase 2).

Because the compose file lives in `docker/` but `.env` lives at the
project root, it must be run with an explicit `--env-file` from the
project root:

```bash
docker compose -f docker/docker-compose.yml --env-file .env up --build
```

Compose specifics:
- `build.context: ..` — the build context is the project root (one
  level up from `docker/`), so the `Dockerfile`'s `COPY . .` picks up
  the whole app, not just the `docker/` directory.
- Port `8000:8000`.
- Two bind mounts: `../logs:/app/logs` and `../models:/app/models` —
  logs and the model file live outside the image and persist across
  container rebuilds; the model is *not* baked into the image.
- `SECRET_KEY=${SECRET_KEY}` — pulled from the environment/`.env` file
  at compose time, not hardcoded in the compose file itself.
- `restart: unless-stopped`.
- Single bridge network (`tinyagentos-network`) — there's nothing else
  on it yet, so no network policy is meaningfully enforced today; this
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
   `change_this_in_production`, `changeme`, and `secret` (case-insensitive,
   whitespace-trimmed). A short or obviously-fake value fails startup
   with a clear error rather than silently running insecurely.

Generate a real one with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put it in a project-root `.env` file, never in `.env.example`, never
committed, and never pasted into chat/logs/tickets. If a real key is
ever exposed (committed, pasted, logged), treat it as compromised and
generate a new one — there is currently no key-rotation mechanism, so
"rotate" here means: generate a new value, update `.env`, restart.

## Environment variables

These map directly to real `Settings` fields in
`infrastructure/config.py` — every name below is read; nothing here is
a silently-ignored legacy name from the plan's draft.

| Variable | Settings field | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | `SECRET_KEY` | *(none — required)* | 32+ chars, rejected if a known placeholder. App won't start without it. |
| `REQUIRE_AUTH` | `REQUIRE_AUTH` | `True` | See `API.md`/`SECURITY.md` — even when `True`, current auth is a `sk-` prefix format check, not a real credential check. |
| `DEBUG` | `DEBUG` | `False` | Compose sets this explicitly to `false`; default is already off. |
| `LOG_LEVEL` | `LOG_LEVEL` | `INFO` | |
| `DATABASE_URL` | `DATABASE_URL` | `sqlite:///./tinyagentos.db` | Local SQLite file by default — no network exposure, no encrypted connection (none needed for local SQLite), no automated backups configured. |
| `MODEL_PATH` | `MODEL_PATH` | `models/phi-3-mini.gguf` | Compose overrides to `/app/models/phi-3-mini.gguf` to match the container mount path. |
| `HOST` | `HOST` | `127.0.0.1` | **Not read by the container's actual `uvicorn` CMD** — see Build section. Only affects `python api/app.py` direct runs. |
| `PORT` | `PORT` | `8000` | Same caveat as `HOST`. Validated `1`–`65535` at startup regardless. |
| `N_THREADS` | `N_THREADS` | `4` | LLM inference threads. |
| `N_CTX` | `N_CTX` | `2048` | LLM context window. |
| `N_GPU_LAYERS` | `N_GPU_LAYERS` | `0` | `0` = CPU-only inference. |
| `TEMPERATURE` | `TEMPERATURE` | `0.6` | |
| `ALGORITHM` | `ALGORITHM` | `HS256` | **Present in `Settings` but currently unused** — no JWT encode/decode happens anywhere in the live auth path (`verify_api_key()` is a plain string-prefix check). Configured but dead, same pattern as `infrastructure/security.py`'s `SecurityManager` being built but unwired. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Same caveat — no token-expiry logic currently consumes this. |
| `API_KEY_HEADER` | `API_KEY_HEADER` | `X-API-Key` | Declared in `Settings`, but `verify_api_key()` in `routes.py` reads the header via a hardcoded `Header(None)` parameter named `x_api_key` — **not** by referencing this setting. Changing this env var alone would not change which header name is actually checked; unverified whether this is intentional. |

`extra = "ignore"` is set on `Settings.Config` — an unrecognized env
var (e.g. a typo, or a leftover from the plan's draft like
`JWT_SECRET`) is silently dropped, not an error. If an env var doesn't
appear to be taking effect, check the spelling against the table above
rather than assuming it's a bug elsewhere.

## What's not configured yet

Carried over from `SECURITY.md`'s Deployment Security section, for
visibility in one place:

- No resource limits on the container (see Compose section above)
- No automated image security scanning (Trivy/Grype/`docker scan`) in CI
- No key-rotation mechanism for `SECRET_KEY`
- No real API-key issuance/revocation system — `REQUIRE_AUTH=true`
  currently gates on key *format*, not a stored, revocable credential
