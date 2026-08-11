# API Key Storage — Command Runbook

Everything run today to get API key auth working end-to-end between
your Windows host and the Docker container. Kept in order, with a note
on what each step was actually for.

## 1. Issue a key (host side)

```cmd
python -m scripts.manage_api_keys issue --label "local-test"
```
Prints the raw key **once** — copy it immediately. Only its SHA-256
hash is stored afterward; there's no way to retrieve the plaintext again.

```cmd
python -m scripts.manage_api_keys list
```
Confirms the row exists (id, label, revoked, created_at, last_used_at).

```cmd
python -m scripts.manage_api_keys --help
```
If `issue` / `list` / `revoke` ever need double-checking.

## 2. Store the key for reuse (conda env var — what you settled on)

```cmd
conda activate tinyagentos
conda env config vars set TINYAGENTOS_TEST_API_KEY=sk-<your-real-key>
```
Requires closing and reopening the terminal (not just
`deactivate`/`activate` in the same window) for `cmd.exe` to actually
pick up the new var — this was the specific thing that tripped us up.

```cmd
conda env config vars list
```
Confirms it's saved to the env's config.

```cmd
echo %TINYAGENTOS_TEST_API_KEY%
```
Confirms it's loaded into the *current* shell (this is the one that
kept failing until the terminal was fully closed/reopened).

To remove/rotate later:
```cmd
conda env config vars unset TINYAGENTOS_TEST_API_KEY
```

### Alternative: file-based (`.env.test`), if you'd rather not use conda vars
```cmd
echo TINYAGENTOS_TEST_API_KEY=sk-<your-real-key> > .env.test
```
Then each session, load it (Git Bash):
```bash
export $(grep -v '^#' .env.test | xargs)
```
`.env.test` must be in `.gitignore` — never commit it.

## 3. Use the key

```cmd
curl -X POST http://localhost:8000/api/v1/tasks -H "X-API-Key: %TINYAGENTOS_TEST_API_KEY%" -H "Content-Type: application/json" -d "{\"text\": \"...\", \"task_type\": \"summarize\", \"priority\": 1}"
```
`cmd.exe` needs everything on **one line** — no `\` continuation, no `$VAR`.

## 4. The actual root cause: Docker container had a separate DB

Diagnose which server you're hitting:
```cmd
docker ps
```

Add a volume mount so the container reads/writes the **same** SQLite
file as the host, in `docker/docker-compose.yml`:
```yaml
    volumes:
      - ../logs:/app/logs
      - ../models:/app/models
      - ../tinyagentos.db:/app/tinyagentos.db
```

Confirm the file exists on the host first (a missing file mounts as an
empty *directory*, which breaks SQLite):
```cmd
dir tinyagentos.db
```
If missing:
```cmd
type nul > tinyagentos.db
```

Recreate the container cleanly with the new mount:
```cmd
docker compose -f docker/docker-compose.yml --env-file .env down
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

Verify both sides now see the same data:
```cmd
python -m scripts.manage_api_keys list
docker compose -f docker/docker-compose.yml --env-file .env exec tinyagentos python -m scripts.manage_api_keys list
```

Check container health/logs if anything looks wrong:
```cmd
docker compose -f docker/docker-compose.yml logs --tail=30 tinyagentos
docker inspect --format="{{.State.Health.Status}}" docker-tinyagentos-1
```

## 5. Secondary bug found along the way: case-sensitive filename

`scripts/manage_api_keys.PY` (uppercase extension) worked on Windows
(case-insensitive filesystem) but broke `python -m
scripts.manage_api_keys` **inside** the Linux container. Fixed at the
source:
```cmd
git mv scripts/manage_api_keys.PY scripts/manage_api_keys.py
git status
```
If `git status` shows no change (can happen on case-insensitive
filesystems), force it as two renames:
```cmd
git mv scripts/manage_api_keys.PY scripts/manage_api_keys_temp.py
git mv scripts/manage_api_keys_temp.py scripts/manage_api_keys.py
```

Then rebuild (a plain restart won't re-copy the renamed file):
```cmd
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

Confirm the fix:
```cmd
docker compose -f docker/docker-compose.yml --env-file .env exec tinyagentos ls -la /app/scripts
docker compose -f docker/docker-compose.yml --env-file .env exec tinyagentos python -m scripts.manage_api_keys list
```

## Result

Host and container now share one `tinyagentos.db`. Keys issued from
either side are usable from both. `last_used_at` on a key row updating
after a curl call is the definitive "auth actually worked" signal.
