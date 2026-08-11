# Deployment Runbook

Supersedes the 30-day plan's Day 24 draft, which used a bare `docker-compose up -d`
and `/health` -- both wrong for this repo (compose file lives at `docker/`, needs
`--env-file`; the real path is `/api/v1/health`, confirmed by
`test_bare_health_path_404s` and the Dockerfile's own HEALTHCHECK).

## Pre-Deployment Checklist

- [ ] `pytest -v` passes locally (full suite, ~9.5 min with real model inference)
- [ ] `.env` at project root has a real `SECRET_KEY` (32+ chars,
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`) --
      the app refuses to start without one
- [ ] `tinyagentos.db` exists on the host before the first `docker compose up`
      (a missing file mounts as an empty *directory*, breaking SQLite --
      see API_KEY_STORAGE_RUNBOOK.md)
- [ ] At least one API key issued and reachable from both host and
      container (`python -m scripts.manage_api_keys list` should show the
      same rows on both sides once the volume mount is correct)

## Local Development

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
pytest
uvicorn api.app:app --reload
```

## Docker Deployment

Compose file lives in `docker/`, but the project's `.env` lives at the
project root -- always pass `--env-file` explicitly from the root:

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
docker compose -f docker/docker-compose.yml logs -f tinyagentos
```

Verify:

```bash
curl http://localhost:8000/api/v1/health
```

Issue and confirm an API key is visible from both host and container
(catches the "container has a separate DB" failure mode documented in
API_KEY_STORAGE_RUNBOOK.md):

```bash
python -m scripts.manage_api_keys list
docker compose -f docker/docker-compose.yml --env-file .env exec tinyagentos python -m scripts.manage_api_keys list
```

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs, in order:

1. **lint-and-typecheck** -- flake8/mypy/bandit, non-blocking until a
   clean baseline is confirmed (see the ASSUMPTIONS note at the top of
   that file)
2. **unit-tests** -- fast, no model required, every push/PR
3. **integration-e2e-tests** -- real model inference, gated to `main` +
   manual trigger only (too slow/expensive to run on every PR)
4. **build-and-push** -- only on `main` or a `v*.*.*` tag; builds via
   `docker/Dockerfile`, scans with Trivy, pushes to
   `ghcr.io/<owner>/<repo>`

**Required repo secret before this will run successfully:**

| Secret | Purpose |
|---|---|
| `CI_SECRET_KEY` | Settings.SECRET_KEY for CI test runs -- generate the same way as the local `.env` value, but keep it separate; this is a CI-only value, not your production secret |

`GITHUB_TOKEN` (registry push) is provided automatically by Actions --
no separate Docker Hub account or secret needed.

## Kubernetes Deployment

**Read this before applying anything:** `k8s/deployment.yaml` is
deliberately `replicas: 1`. This app uses SQLite; more than one replica
writing to the same DB file will corrupt data. Do not scale this
deployment until `DATABASE_URL` points at a real networked database.

```bash
# One-time: create the secret (never commit a real value)
kubectl create secret generic tinyagentos-secrets \
  --from-literal=secret-key="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"

# Replace IMAGE_PLACEHOLDER in k8s/deployment.yaml with your real
# ghcr.io/<owner>/<repo>:<tag> first, then:
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
- `last_used_at` on an API key row updating after a real request is the
  definitive "auth actually worked end-to-end" signal (per
  API_KEY_STORAGE_RUNBOOK.md)

## Known Gaps -- Not Yet Resolved

- **No Prometheus/Grafana wiring confirmed.** `infrastructure/metrics.py`
  exists and is tested, but nothing in this repo confirms an actual
  Prometheus scrape endpoint or dashboard is deployed. Don't assume
  metrics are being collected externally just because the module exists.
- **No `scripts/download_model.py` confirmed to exist**, despite being
  listed in the 30-day plan's architecture diagram -- it never appeared
  in any coverage report across this repo's test history. The CI
  workflow downloads the model directly via `huggingface-cli` instead;
  local setup still relies on however you originally obtained
  `models/phi-3-mini.gguf`.
- **SQLite is a real long-term constraint**, not just a k8s replica
  count issue -- worth planning a Postgres migration before this needs
  to scale beyond a single instance.
