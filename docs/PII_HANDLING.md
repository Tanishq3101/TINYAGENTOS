# PII Handling

This document describes what TinyAgentOS actually does with
user-supplied task input — the only field in the system that can
contain PII — based on the current implementation. It is not a legal
policy; it's a factual account of current behavior plus the gaps that
still need a decision.

## What counts as PII here

The only free-text field a caller controls is the task input string
(`POST /api/v1/tasks`, `text` field). It is arbitrary user-supplied
text and may contain names, contact details, or anything else the
caller pastes in. No other field in the system is free text — task
type, priority, and API keys are all structured/generated values, not
user content.

## In-memory (orchestrator)

- `core/orchestrator.py` never logs raw task input. Every log call
  passes only `input_length` and a 12-character truncated SHA-256
  `input_fingerprint` (`_fingerprint()`), never the text itself.
- Tasks live in `Orchestrator.tasks`, an in-memory dict, for up to
  `task_ttl_seconds` (default 3600s / 1 hour) after their last update,
  or until evicted for exceeding `max_stored_tasks` (default 10,000).
  After that, `_cleanup_expired_tasks_locked()` deletes the record —
  including the raw input — entirely.
- This means: if nothing ever persists a task to the database, raw
  input has a hard, automatic expiry of at most ~1 hour.

## At rest (database)

- `storage/models.py`'s `TaskModel.input_text` is where task input
  would be persisted, via `Database.save_task_execution()`.
- As of this document, `database.py` encrypts `input_text` before
  storage **if `FERNET_KEY` is configured** (`infrastructure/config.py`).
  If `FERNET_KEY` is unset, input is stored in plaintext — this is
  logged as a warning on first save, not silently.
- **Gap: no retention policy for persisted rows.** Unlike the in-memory
  orchestrator store, nothing currently deletes or expires `TaskModel`
  rows once written. A task's raw input (encrypted or not) persists in
  the database indefinitely unless something manually deletes it. If
  compliance requires a retention limit (e.g. "delete after 30 days"),
  that needs a scheduled cleanup job — none exists yet.
- **Gap: no content-level redaction.** Encryption protects the data at
  rest from someone reading the raw database file or a stolen backup.
  It does not scrub or detect PII within the text itself — an
  authorized process with the `FERNET_KEY` and DB access can always
  read the original input in full.
- Note also: as of this document, nothing in `api/routes.py` actually
  calls `save_task_execution()` — task state currently only exists in
  the orchestrator's in-memory store, not the database. `TaskModel`
  exists and is ready, but the persistence path is not yet wired into
  the live request flow. Confirm this is intentional before assuming
  the 1-hour in-memory TTL is the only retention window in practice.

## Access control

- All task endpoints require a valid `X-API-Key` (`REQUIRE_AUTH: true`
  by default), verified against a hashed, revocable key in
  `storage/database.py`'s `api_keys` table (`api/dependencies.py`).
  Only holders of a valid key can create tasks or read task results —
  including any PII those results might contain.
- Database-level access (reading `tinyagentos.db` directly, or its
  backups) is controlled entirely outside the application — by
  filesystem permissions, the container's non-root user, and whoever
  has access to `FERNET_KEY` and the backup storage location. This
  document does not cover that layer; see `docs/SECURITY.md` and your
  infrastructure's own access controls.

## Open items

1. **Retention policy for persisted task rows** — decide a retention
   window and build a scheduled deletion job, or confirm indefinite
   retention is acceptable.
2. **Confirm whether `save_task_execution()` is meant to be called from
   the live API flow** — if it isn't wired in yet, this document's
   "at rest" section describes a path that isn't actually exercised in
   production yet, and the in-memory 1-hour TTL is the real retention
   window today.
3. **Backup retention and access** — see `docs/BACKUPS.md` (if backups
   are enabled) for how long encrypted backups are kept and who can
   decrypt them.
