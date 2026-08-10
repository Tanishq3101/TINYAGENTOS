# TinyAgentOS Security Guide

Reflects what's actually enforced by the running code as of Day 14 —
not what the plan's original draft described. The single most important
thing in this document is the gap in the first section: real security
utilities exist (`infrastructure/security.py`) but the live
authentication path doesn't use them yet.

## API Key Authentication — current state

**What actually runs today, in `api/routes.py`'s `verify_api_key()`:**

1. If `settings.REQUIRE_AUTH` is `False`, every request is accepted with
   no key at all.
2. If `REQUIRE_AUTH` is `True`, a request is accepted if it supplies an
   `X-API-Key` header whose value **starts with the string `"sk-"`** —
   full stop. Any string starting with `sk-` is currently a valid key.

**What is *not* happening, despite the infrastructure existing for it:**

`infrastructure/security.py`'s `SecurityManager` has real,
well-implemented pieces for proper API key management —
`generate_api_key()` (cryptographically random, via `secrets`),
`hash_api_key()` (SHA-256), and `verify_api_key(candidate, stored_hash)`
(constant-time comparison via `hmac.compare_digest`, so it doesn't leak
timing information about how much of the key matched). **None of this
is called from `api/routes.py`.** There is no endpoint to generate or
register a key, no `ApiKeyModel` in `storage/models.py` to persist a
hash against, and no lookup happening anywhere in the request path.
Current "auth" is a format check, not a credential check.

If you deploy this as-is with `REQUIRE_AUTH=true`, understand that it
stops casual/unauthenticated access and nothing more — it does not
distinguish one caller from another, cannot be revoked per-key, and any
value matching `sk-*` gets in. Closing this gap means: add an
`ApiKeyModel` (hash + metadata) to `storage/models.py`, a way to issue
keys (even a CLI script is fine to start), and change
`verify_api_key()` to look up the presented key's hash via
`storage.database` and call `SecurityManager.verify_api_key()` against
it, instead of the current prefix check.

There are also currently **two implementations** of `verify_api_key()`
— one in `api/routes.py` (the one actually wired into every route via
`Depends()`) and a near-identical one in `api/dependencies.py` that
nothing imports. If/when this gets fixed, fix the one in `routes.py`;
consider deleting the other or making `routes.py` import from it, so
there's one source of truth.

## Encryption at Rest — current state

`SecurityManager.encrypt_sensitive_data()` /
`decrypt_sensitive_data()` (Fernet symmetric encryption) exist and are
correctly implemented — notably, the encryption key is **never**
auto-generated as a fallback (the constructor raises if
`encryption_key` is falsy), specifically to avoid the failure mode
where a silently-generated key makes previously-encrypted data
unreadable after a restart, or where multiple instances behind a load
balancer end up using different keys.

**Nothing in `storage/database.py` or `storage/models.py` currently
calls this.** Task input/output is stored in plain SQLite via
`TaskModel`, unencrypted. If task content is ever sensitive, wiring
`SecurityManager.encrypt_sensitive_data()` into `Database` before
`session.add()` (and decrypting on read) is the integration point — it
isn't done yet.

## Conversation Memory Persistence — current state

`core/memory.py`'s `ConversationMemory` (used by the standalone
`core.agent.BaseAgent` — see Architecture doc, this is not part of the
Orchestrator/pipeline request path) writes full conversation history —
every user prompt and every assistant response — to
**plaintext JSON files on disk**, at `memory_store/<session_id>.json`,
by default. This is a real, previously-undocumented data-at-rest gap,
separate from and in addition to the database encryption gap above:

- No encryption — `SecurityManager.encrypt_sensitive_data()` is not
  used here either.
- No retention policy or automatic expiry — a session's file persists
  indefinitely once written, until something explicitly calls
  `ConversationMemory.clear()`. There's no TTL, no scheduled cleanup,
  and no cap on the number of session files that can accumulate under
  `memory_store/`.
- `session_id` **is** sanitized before being used as a filename
  (`_file_path()` keeps only alphanumerics, `-`, and `_`, falling back
  to `"default"` otherwise) — this correctly prevents path traversal
  via a malicious `session_id`, which is worth confirming stays true if
  this code changes.
- If `BaseAgent` / `ConversationMemory` are ever exposed through a real
  endpoint (they aren't today — see Architecture doc), whatever
  supplies `session_id` needs to not be freely choosable by an
  unauthenticated caller, or one caller could read/overwrite another's
  conversation file by guessing or supplying their session_id — there's
  no access control at the `ConversationMemory` layer itself, just
  filename sanitization.

If conversation content is ever sensitive, this needs the same fix as
the database: wire `SecurityManager.encrypt_sensitive_data()` into
`ConversationMemory._save()` / `_load()`, plus decide on an actual
retention policy.

## Request Signature Verification — current state

`SecurityManager.verify_request_signature()` (HMAC-SHA256, also via
`hmac.compare_digest`) is implemented for verifying inbound webhook-style
payloads. There is no webhook receiver in this codebase yet, so this
function currently has no caller. Keep it in mind if/when an inbound
webhook endpoint is added — this is the right building block for it,
it just isn't connected to anything today.

## Input Validation — current state

This part *is* real and enforced:

- `TaskRequest.text` — 1 to 100,000 characters, enforced by Pydantic
  (`api/schemas.py`) before the request even reaches a route body.
- `Orchestrator.create_task()` independently re-validates length
  (`max_input_length`, default 100,000) and strips null bytes /
  collapses whitespace — defense in depth even if something upstream of
  the orchestrator ever changes.
- `task_type` whitelist — enforced, but **not** at the request-schema
  layer (`TaskRequest.task_type` is a plain `str`, no `Literal`
  constraint). It's enforced deep in `Orchestrator.create_task()`
  against `SUPPORTED_TASK_TYPES`. The practical effect: an invalid
  `task_type` currently surfaces as a generic `500` from `routes.py`'s
  blanket `except Exception`, not a `400`/`422` — see `docs/API.md`'s
  Create Task section for the exact behavior. Worth fixing at the
  schema layer (a `Literal[...]` type) if you want bad `task_type`
  values rejected with a proper 4xx before they even reach the
  orchestrator.
- SQL injection — `storage/database.py` uses SQLAlchemy's ORM query
  interface (`session.query(TaskModel).filter(...)`) throughout, not
  raw string-interpolated SQL, so standard ORM-level injection
  protection applies.

## Database Security — current state

Default configuration (`docker-compose.yml`) is
`DATABASE_URL=sqlite:///./tinyagentos.db` — a local file, no network
exposure by construction, but also: no encrypted connection (there's no
connection to encrypt for local SQLite), no row-level security (SQLite
doesn't have a row-level security concept the way Postgres does), and
no automated backup process configured anywhere in this repo. If this
moves to a real networked database (e.g. Postgres) for a production
deployment, none of the "encrypted connections" / "row-level security"
/ "encrypted backups" claims in the original plan draft are actually
implemented yet — they'd need to be set up at that point, not assumed
to already exist.

## Deployment Security — current state

What's real, confirmed from `Dockerfile`:

- Runs as a non-root user (`appuser`, uid 1000) — the app directory,
  `models/`, and `logs/` are `chown`'d to that user at build time, and
  `USER appuser` is set before the container's entrypoint runs.
- Multi-stage build — the final image doesn't carry the
  `build-essential`/`gcc`/`g++` toolchain needed only to compile
  `llama-cpp-python`, just the compiled result plus the
  `libgomp1` runtime dependency it needs.
- `HEALTHCHECK` hits the correct, real path (`/api/v1/health`) with a
  measured, not guessed, `--start-period` (45s, against ~26s observed
  local model load time).

What's **not** configured, despite being listed as a goal in the
original plan:

- No resource limits (`mem_limit`, `cpus`, or Compose's `deploy.resources`
  block) are set in `docker-compose.yml` — a single container can
  currently consume unbounded host memory/CPU.
- No network policy beyond the single bridge network
  (`tinyagentos-network`) that only this one service is on — there's
  nothing to restrict yet since there's only one service, but this
  becomes relevant the moment a database or cache service is added back.
- No automated security scanning of the built image (e.g. `docker scan`,
  Trivy, Grype) is wired into any CI step in this repo.

## API Key Rotation, Secrets Management

`SECRET_KEY` has no default in `infrastructure/config.py` — the app
refuses to start without one (enforced by a `field_validator` on
`Settings`), which is the correct default-deny posture. It must come
from a project-root `.env` file (`docker-compose.yml` reads it via
`${SECRET_KEY}` and `--env-file .env`), which should never be committed.
There is currently no key-rotation mechanism for `SECRET_KEY` itself,
and — per the API Key Authentication section above — no per-caller API
key system to rotate in the first place yet.
