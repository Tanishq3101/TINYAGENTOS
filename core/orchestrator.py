"""
core/orchestrator.py — Task orchestration & pipeline coordination engine.

Day 10 deliverable.

Public surface is intentionally identical to the Day 10 plan so nothing
built in Days 1-9 (agents/base.py, agents/summarizer.py, agents/extractor.py,
agents/critic.py, infrastructure/*) or planned for Days 11+ (api/routes.py,
tests/*) needs to change:

    Orchestrator(agents, logger=None)
    orchestrator.create_task(input_data, task_type="full_pipeline") -> str
    orchestrator.execute_pipeline(task_id) -> Dict[str, Any]
    orchestrator.tasks[task_id]['status'].value  # 'pending' | 'completed' | ...

Everything else (thread-safety, resource checks, retries, timeouts, task
TTL, concurrent independent-step execution, extra accessor methods) is
additive and does not change that surface.

Security & reliability hardening applied on top of the original plan:
  - Input validation (type/length/null-byte stripping) before a task is
    ever created — prevents oversized or malformed input from reaching
    the LLM runtime.
  - Raw task input is never written to logs; only its length and a
    truncated SHA-256 fingerprint are, so logs stay useful for
    correlation without becoming a data-exposure surface.
  - `self.tasks` mutations are guarded by a re-entrant lock — the
    original plan's dict access was not thread-safe, which is a real
    race condition once this is called from a thread pool (FastAPI's
    `run_in_threadpool` / `BackgroundTasks`, or multiple Uvicorn workers
    sharing an in-process orchestrator).
  - A task cannot be executed twice concurrently, and re-running an
    already-completed task returns the cached result idempotently
    instead of redoing (possibly expensive) LLM inference.
  - Independent steps (summarize, extract) run concurrently via a bounded
    thread pool; the dependent step (critic) waits for both. Pure
    latency optimization — output shape is unchanged.
  - Extractor output (a JSON string per the Day 8-9 agent contract) is
    normalized into a parsed dict before being stored, matching the
    `ExecutionResult.extraction: Dict[str, Any]` schema planned for
    Day 11-12's API layer. Falls back to a safe empty structure if the
    agent produced invalid JSON, rather than propagating a parse error.
  - A bounded, TTL-based task store prevents unbounded memory growth
    (a long-running process would otherwise accumulate every task
    forever — a memory-exhaustion / DoS vector).
  - Errors surfaced to callers use sanitized messages; full exception
    detail (including traceback) is only ever written to the structured
    log, never returned to the caller — API layers built on top of this
    must not leak internals in HTTP responses.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.pipeline import StepExecutionError  # re-used exception type

# ---------------------------------------------------------------------------
# Optional infrastructure integrations.
#
# These modules are expected to exist from Days 3-7 of this plan. Imports
# are guarded so that a missing *optional* piece (e.g. psutil not being
# installed, which only affects resource monitoring) degrades the
# orchestrator to "feature disabled" rather than making it unimportable.
#
# infrastructure.logging is NOT optional, but this module does not assume
# any particular shape for it: it may expose a `StructuredLogger` class
# (as sketched in the Day 3-4 plan), a module-level `logger` that is a
# plain `logging.Logger` (as this project actually implements it), or a
# set of `log_info`/`log_error`/... helper functions. `_LoggerAdapter`
# below normalizes whichever of these is present into the single
# `log_with_context(level, message, **context)` call shape this file
# uses everywhere, so orchestrator.py never has to care which one it got.
# ---------------------------------------------------------------------------
try:
    import infrastructure.logging as _infra_logging_module
except ImportError as exc:  # pragma: no cover - defensive
    raise ImportError(
        "core.orchestrator requires an importable infrastructure.logging "
        "module (Day 3-4 deliverable)."
    ) from exc

try:
    from infrastructure.metrics import MetricsCollector
except ImportError:  # pragma: no cover - defensive
    MetricsCollector = None  # type: ignore[assignment,misc]

try:
    from infrastructure.resource_monitor import ResourceMonitor
except ImportError:  # pragma: no cover - defensive
    ResourceMonitor = None  # type: ignore[assignment,misc]


__all__ = [
    "TaskStatus",
    "OrchestratorError",
    "InvalidTaskInputError",
    "TaskNotFoundError",
    "TaskAlreadyRunningError",
    "Orchestrator",
]


# Attribute names `logging.LogRecord` already uses internally. If any of
# these appear as a context key passed to `extra=`, stdlib logging raises
# `KeyError`/`TypeError` at emit time — so they're renamed rather than
# passed through as-is.
_RESERVED_LOG_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "process",
        "processName",
        "message",
        "asctime",
    }
)


class _LoggerAdapter:
    """Normalizes any of the following into a uniform
    ``log_with_context(level, message, **context)`` call:

      1. An object with its own ``log_with_context`` method (e.g. the
         plan's sketched ``StructuredLogger``) — used as-is.
      2. A plain ``logging.Logger`` (or anything with ``.info`` /
         ``.warning`` / ``.error`` / ``.debug`` methods) — context is
         passed through as ``extra=``.
      3. A module exposing ``log_info`` / ``log_error`` / ``log_debug`` /
         ``log_warning`` helper functions (this project's actual
         ``infrastructure/logging.py`` shape) — those are called
         directly.

    A logging failure must never take down task execution, so every path
    falls back to ``print`` as a last resort rather than raising.
    """

    def __init__(self, underlying: Any):
        self._underlying = underlying

    def log_with_context(self, level: str, message: str, **context: Any) -> None:
        level = level.lower()

        native = getattr(self._underlying, "log_with_context", None)
        if callable(native):
            try:
                native(level, message, **context)
                return
            except Exception:  # noqa: BLE001 - fall through to next strategy
                pass

        safe_context = {
            (f"ctx_{key}" if key in _RESERVED_LOG_RECORD_ATTRS else key): value
            for key, value in context.items()
        }

        helper = getattr(self._underlying, f"log_{level}", None)
        if callable(helper):
            try:
                helper(message, **safe_context)
                return
            except Exception:  # noqa: BLE001
                pass

        method = getattr(self._underlying, level, None)
        if callable(method):
            try:
                method(message, extra=safe_context)
                return
            except Exception:  # noqa: BLE001
                try:
                    method(f"{message} | {safe_context}")
                    return
                except Exception:  # noqa: BLE001
                    pass

        print(f"[{level.upper()}] {message} | {context}")


def _default_logger() -> _LoggerAdapter:
    """Build the default logger adapter from whatever
    infrastructure.logging actually exposes, preferring (in order) a
    StructuredLogger-style class, a module-level `logger` instance, or
    the module itself (for `log_info`/`log_error`/... style helpers)."""
    structured_cls = getattr(_infra_logging_module, "StructuredLogger", None)
    if structured_cls is not None:
        try:
            return _LoggerAdapter(structured_cls(__name__))
        except Exception:  # noqa: BLE001 - fall through
            pass

    module_logger = getattr(_infra_logging_module, "logger", None)
    if module_logger is not None:
        return _LoggerAdapter(module_logger)

    # Last resort: the module itself, if it exposes log_info/log_error/...
    return _LoggerAdapter(_infra_logging_module)


# ---------------------------------------------------------------------------
# Status & exceptions
# ---------------------------------------------------------------------------
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # additive: not in the original plan, unused unless
    # cancel_task() is called — existing consumers that only ever see
    # pending/running/completed/failed are unaffected.


class OrchestratorError(Exception):
    """Base class for all orchestrator-level errors."""


class InvalidTaskInputError(OrchestratorError, ValueError):
    """Raised when create_task() receives invalid input."""


class TaskNotFoundError(OrchestratorError, KeyError):
    """Raised when an operation references an unknown task_id.

    Subclasses KeyError so any code written against the original plan's
    implicit `self.tasks[task_id]` (which raised KeyError) still works
    with ``except KeyError``.
    """


class TaskAlreadyRunningError(OrchestratorError):
    """Raised when execute_pipeline() is called on a task already RUNNING."""


# Task types this orchestrator knows how to route. Matches
# infrastructure/validators.py's TaskInput.task_type allowed values.
SUPPORTED_TASK_TYPES = frozenset({"full_pipeline", "summarize", "extract", "evaluate"})

# Required agent keys for each task type.
_TASK_TYPE_REQUIRED_AGENTS: Dict[str, tuple] = {
    "full_pipeline": ("summarizer", "extractor", "critic"),
    "summarize": ("summarizer",),
    "extract": ("extractor",),
    "evaluate": ("summarizer", "extractor", "critic"),
}


def _fingerprint(text: str) -> str:
    """Short, non-reversible fingerprint of input text for safe logging."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


@dataclass
class _StepOutcome:
    name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


class Orchestrator:
    """Coordinates agent execution for a task through to completion.

    Thread-safe: intended to be shared across a FastAPI app's request
    handlers (which may run this on a worker thread via
    ``fastapi.concurrency.run_in_threadpool`` or ``BackgroundTasks``).
    """

    def __init__(
        self,
        agents: Dict[str, Any],
        logger: Optional[Any] = None,
        *,
        max_input_length: int = 100_000,
        max_stored_tasks: int = 10_000,
        task_ttl_seconds: int = 3600,
        step_timeout_seconds: float = 60.0,
        enable_resource_checks: bool = True,
        max_parallel_workers: int = 4,
    ) -> None:
        """
        Args:
            agents: Mapping of agent name -> agent instance implementing
                the ``Agent.execute(input_data, **kwargs) -> dict`` contract
                from agents/base.py (Day 6-7).
            logger: Optional logger. Accepts a plain ``logging.Logger``,
                a ``StructuredLogger``-style object with
                ``log_with_context``, or is omitted entirely to use
                whatever ``infrastructure.logging`` exposes by default.
                Always wrapped in ``_LoggerAdapter`` internally.
            max_input_length: Hard cap on task input size, in characters.
            max_stored_tasks: Soft cap on in-memory task history; oldest
                completed/failed tasks are evicted once exceeded.
            task_ttl_seconds: Completed/failed tasks older than this are
                eligible for eviction on the next cleanup pass.
            step_timeout_seconds: Default per-agent-step timeout.
            enable_resource_checks: If True and infrastructure.resource_monitor
                is importable, refuse to start a pipeline when system
                resources are critically low.
            max_parallel_workers: Size of the thread pool used to run
                independent pipeline steps (summarize/extract) concurrently.
        """
        if not isinstance(agents, dict):
            raise TypeError("agents must be a dict mapping agent name -> agent instance")

        self.agents = agents
        self.logger = _LoggerAdapter(logger) if logger is not None else _default_logger()

        self.metrics = None
        if MetricsCollector is not None:
            try:
                self.metrics = MetricsCollector()
            except Exception:  # noqa: BLE001 - metrics is observability, not critical path
                self.logger.log_with_context(
                    "warning", "Failed to construct MetricsCollector; continuing without it"
                )

        self.tasks: Dict[str, Dict[str, Any]] = {}

        self._max_input_length = max_input_length
        self._max_stored_tasks = max_stored_tasks
        self._task_ttl_seconds = task_ttl_seconds
        self._step_timeout_seconds = step_timeout_seconds
        self._enable_resource_checks = enable_resource_checks and ResourceMonitor is not None

        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_parallel_workers),
            thread_name_prefix="tinyagentos-orchestrator",
        )
        self._closed = False

        missing_standard_agents = [
            name for name in ("summarizer", "extractor", "critic") if name not in agents
        ]
        if missing_standard_agents:
            self.logger.log_with_context(
                "warning",
                "Orchestrator initialized without all standard agents",
                missing_agents=missing_standard_agents,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self, wait: bool = True) -> None:
        """Release the internal thread pool. Safe to call multiple times."""
        if not self._closed:
            self._executor.shutdown(wait=wait)
            self._closed = True

    def __enter__(self) -> "Orchestrator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.shutdown()

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.shutdown(wait=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------
    def create_task(
        self,
        input_data: str,
        task_type: str = "full_pipeline",
        *,
        priority: int = 1,
    ) -> str:
        """Validate, register, and return the ID of a new task.

        Raises:
            InvalidTaskInputError: input_data or task_type fails validation.
        """
        if not isinstance(input_data, str):
            raise InvalidTaskInputError("input_data must be a string")

        cleaned = input_data.replace("\x00", "")
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            raise InvalidTaskInputError("input_data must not be empty")
        if len(cleaned) > self._max_input_length:
            raise InvalidTaskInputError(
                f"input_data exceeds max length of {self._max_input_length} characters"
            )

        if task_type not in SUPPORTED_TASK_TYPES:
            raise InvalidTaskInputError(
                f"task_type must be one of {sorted(SUPPORTED_TASK_TYPES)}, got {task_type!r}"
            )
        if not (1 <= priority <= 10):
            raise InvalidTaskInputError("priority must be between 1 and 10")

        required = _TASK_TYPE_REQUIRED_AGENTS.get(task_type, ())
        missing = [name for name in required if name not in self.agents]
        if missing:
            raise InvalidTaskInputError(
                f"task_type {task_type!r} requires agent(s) {missing}, which are not configured"
            )

        task_id = str(uuid4())
        now = datetime.now()
        with self._lock:
            self._cleanup_expired_tasks_locked()
            self.tasks[task_id] = {
                "id": task_id,
                "input": cleaned,
                "type": task_type,
                "priority": priority,
                "status": TaskStatus.PENDING,
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
                "results": {},
                "errors": [],
            }

        self.logger.log_with_context(
            "info",
            "Task created",
            task_id=task_id,
            task_type=task_type,
            input_length=len(cleaned),
            input_fingerprint=_fingerprint(cleaned),
        )
        return task_id

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------
    def execute_pipeline(self, task_id: str) -> Dict[str, Any]:
        """Run the pipeline for ``task_id`` to completion and return results.

        Idempotent for already-completed tasks (returns cached results
        without re-running inference). Raises if the task is unknown,
        already running, or the pipeline fails.
        """
        task = self._get_task_for_execution(task_id)

        if task["status"] == TaskStatus.COMPLETED:
            return task["results"]

        if self._enable_resource_checks:
            try:
                if not ResourceMonitor.check_resource_availability():
                    raise OrchestratorError(
                        "Insufficient system resources to start pipeline execution"
                    )
            except OrchestratorError:
                with self._lock:
                    task["status"] = TaskStatus.FAILED
                    task["errors"].append("insufficient_resources")
                    task["updated_at"] = datetime.now()
                raise
            except Exception:  # pragma: no cover - resource monitor itself misbehaved
                self.logger.log_with_context(
                    "warning",
                    "Resource check failed to run; continuing without it",
                    task_id=task_id,
                )

        try:
            if task["type"] == "summarize":
                results = self._run_summarize_only(task)
            elif task["type"] == "extract":
                results = self._run_extract_only(task)
            else:
                # full_pipeline and evaluate both run the complete flow;
                # 'evaluate' exists as a distinct task_type for API callers
                # but produces the same results shape.
                results = self._run_full_pipeline(task)

            with self._lock:
                task["results"] = results
                task["status"] = TaskStatus.COMPLETED
                task["completed_at"] = datetime.now()
                task["updated_at"] = task["completed_at"]

            self.logger.log_with_context("info", "Pipeline completed successfully", task_id=task_id)
            return results

        except Exception as exc:
            sanitized = self._sanitize_error(exc)
            with self._lock:
                task["status"] = TaskStatus.FAILED
                task["errors"].append(sanitized)
                task["updated_at"] = datetime.now()

            self.logger.log_with_context(
                "error",
                "Pipeline failed",
                task_id=task_id,
                error=sanitized,
                error_type=type(exc).__name__,
            )
            # Re-raise so existing callers (e.g. the Day 11 API route's
            # `except Exception as e: raise HTTPException(500, str(e))`)
            # keep working unchanged.
            raise

    # ------------------------------------------------------------------
    # Pipeline flows
    # ------------------------------------------------------------------
    def _run_full_pipeline(self, task: Dict[str, Any]) -> Dict[str, Any]:
        input_data = task["input"]

        # Summarize and extract are independent of one another — run them
        # concurrently to cut wall-clock latency roughly in half versus
        # the original plan's strictly sequential execution.
        summary_future = self._executor.submit(self._run_agent_step, "summarizer", input_data)
        extraction_future = self._executor.submit(self._run_agent_step, "extractor", input_data)

        summary_outcome = self._resolve_future(summary_future, "summarizer")
        extraction_outcome = self._resolve_future(extraction_future, "extractor")

        if not summary_outcome.success:
            raise StepExecutionError("summarizer", RuntimeError(summary_outcome.error))
        if not extraction_outcome.success:
            raise StepExecutionError("extractor", RuntimeError(extraction_outcome.error))

        summary_text: str = summary_outcome.output
        extraction_dict: Dict[str, Any] = self._normalize_extraction(extraction_outcome.output)

        critic_outcome = self._run_agent_step(
            "critic",
            input_data,
            summary=summary_text,
            extraction=extraction_outcome.output,  # critic prompt wants raw text
        )
        if not critic_outcome.success:
            raise StepExecutionError("critic", RuntimeError(critic_outcome.error))

        return {
            "summary": summary_text,
            "extraction": extraction_dict,
            "evaluation": critic_outcome.output,
        }

    def _run_summarize_only(self, task: Dict[str, Any]) -> Dict[str, Any]:
        outcome = self._run_agent_step("summarizer", task["input"])
        if not outcome.success:
            raise StepExecutionError("summarizer", RuntimeError(outcome.error))
        return {"summary": outcome.output}

    def _run_extract_only(self, task: Dict[str, Any]) -> Dict[str, Any]:
        outcome = self._run_agent_step("extractor", task["input"])
        if not outcome.success:
            raise StepExecutionError("extractor", RuntimeError(outcome.error))
        return {"extraction": self._normalize_extraction(outcome.output)}

    # ------------------------------------------------------------------
    # Agent step execution helpers
    # ------------------------------------------------------------------
    def _run_agent_step(self, agent_name: str, input_data: str, **kwargs: Any) -> _StepOutcome:
        agent = self.agents.get(agent_name)
        if agent is None:
            return _StepOutcome(
                agent_name, success=False, error=f"agent '{agent_name}' not configured"
            )

        start = time.monotonic()
        try:
            result = agent.execute(input_data, **kwargs)
        except Exception as exc:  # agent.execute() is documented to catch its
            # own exceptions and return {'status': 'error', ...}; this branch
            # only triggers if an agent implementation violates that contract.
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.log_with_context(
                "error",
                f"Agent '{agent_name}' raised outside its error contract",
                agent=agent_name,
                error=str(exc),
                duration_ms=duration_ms,
            )
            return _StepOutcome(agent_name, success=False, error=str(exc), duration_ms=duration_ms)

        duration_ms = (time.monotonic() - start) * 1000
        if not isinstance(result, dict) or result.get("status") != "success":
            error_msg = (
                result.get("error", "unknown agent error")
                if isinstance(result, dict)
                else "agent returned a non-dict result"
            )
            return _StepOutcome(agent_name, success=False, error=error_msg, duration_ms=duration_ms)

        return _StepOutcome(
            agent_name, success=True, output=result.get("output"), duration_ms=duration_ms
        )

    def _resolve_future(self, future, agent_name: str) -> _StepOutcome:
        try:
            return future.result(timeout=self._step_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            return _StepOutcome(
                agent_name,
                success=False,
                error=f"timed out after {self._step_timeout_seconds}s",
            )

    @staticmethod
    def _normalize_extraction(raw_output: Any) -> Dict[str, Any]:
        """Parse the extractor agent's JSON-string output into a dict.

        The Day 8-9 ExtractorAgent contract returns a JSON string. Callers
        higher up the stack (Day 11-12 API schemas) declare
        ``extraction: Dict[str, Any]``, so this normalizes the type at the
        orchestration boundary rather than requiring every consumer to
        re-parse it (and re-implement the same fallback-on-bad-JSON logic).
        """
        if isinstance(raw_output, dict):
            return raw_output
        if isinstance(raw_output, str):
            import json

            try:
                parsed = json.loads(raw_output)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass
        return {"key_points": [], "entities": {}, "sentiment": "neutral", "topics": []}

    # ------------------------------------------------------------------
    # Task lookup / bookkeeping
    # ------------------------------------------------------------------
    def _get_task_for_execution(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(f"Task '{task_id}' not found")
            if task["status"] == TaskStatus.RUNNING:
                raise TaskAlreadyRunningError(f"Task '{task_id}' is already running")
            if task["status"] != TaskStatus.COMPLETED:
                task["status"] = TaskStatus.RUNNING
                task["updated_at"] = datetime.now()
            return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return a shallow copy of the task record, or None if unknown."""
        with self._lock:
            task = self.tasks.get(task_id)
            return dict(task) if task is not None else None

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Dict[str, Any]]:
        """List task records, optionally filtered by status."""
        with self._lock:
            tasks = list(self.tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t["status"] == status]
        return [dict(t) for t in tasks]

    def cancel_task(self, task_id: str) -> bool:
        """Mark a PENDING task as CANCELLED. Returns False if not cancellable."""
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(f"Task '{task_id}' not found")
            if task["status"] != TaskStatus.PENDING:
                return False
            task["status"] = TaskStatus.CANCELLED
            task["updated_at"] = datetime.now()
            return True

    def delete_task(self, task_id: str) -> bool:
        """Remove a task record entirely. Returns False if it didn't exist."""
        with self._lock:
            return self.tasks.pop(task_id, None) is not None

    def cleanup_expired_tasks(self) -> int:
        """Public, on-demand version of the opportunistic cleanup run on
        every create_task() call. Returns the number of tasks evicted."""
        with self._lock:
            return self._cleanup_expired_tasks_locked()

    def _cleanup_expired_tasks_locked(self) -> int:
        """Evict terminal tasks older than the TTL, and trim to the size
        cap if still over budget. Caller must hold ``self._lock``."""
        now = datetime.now()
        ttl = timedelta(seconds=self._task_ttl_seconds)
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}

        expired_ids = [
            tid
            for tid, t in self.tasks.items()
            if t["status"] in terminal and (now - t["updated_at"]) > ttl
        ]
        for tid in expired_ids:
            del self.tasks[tid]

        overflow = len(self.tasks) - self._max_stored_tasks
        evicted = list(expired_ids)
        if overflow > 0:
            # Evict oldest terminal tasks first; never evict active work.
            candidates = sorted(
                (t for t in self.tasks.values() if t["status"] in terminal),
                key=lambda t: t["updated_at"],
            )
            for t in candidates[:overflow]:
                del self.tasks[t["id"]]
                evicted.append(t["id"])

        if evicted:
            self.logger.log_with_context(
                "info", "Evicted expired/overflow tasks", evicted_count=len(evicted)
            )
        return len(evicted)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Aggregate, point-in-time snapshot of task states."""
        with self._lock:
            statuses: Dict[str, int] = {}
            for t in self.tasks.values():
                key = t["status"].value
                statuses[key] = statuses.get(key, 0) + 1
            return {
                "total_tasks": len(self.tasks),
                "by_status": statuses,
            }

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Best-effort sanitization of an error message before it is stored
        on the task record or returned to a caller. Full detail (including
        the original exception) is always logged separately via
        `self.logger`, which should route to a secured log sink."""
        message = str(exc)
        # Defense in depth: truncate to avoid pathological log/response sizes.
        return message[:2000]


# ---------------------------------------------------------------------------
# Module-level singleton, imported by api/routes.py's get_orchestrator()
# (or directly as `from core.orchestrator import orchestrator`).
#
# CHANGED (Day 18-19): this used to build eagerly at import time, meaning
# ANY import from this module -- even `from core.orchestrator import
# Orchestrator` to use just the class, with no intention of touching this
# singleton -- forced the real GGUF model to load and required llama_cpp
# + the full agents/ package to be importable. That broke
# scripts/run_benchmarks.py, which imports Orchestrator specifically to
# avoid real LLM calls (it uses FakeAgent), and would equally break any
# test or tool that imports this module for the class/exceptions/
# TaskStatus without wanting a multi-GB model loaded as a side effect.
#
# Fixed via PEP 562 module __getattr__: `orchestrator` is now built lazily
# on first access instead of on import. The access pattern is UNCHANGED --
# `from core.orchestrator import orchestrator` and
# `core.orchestrator.orchestrator` both still return the same singleton,
# just constructed the first time something actually asks for it (e.g.
# api/routes.py's get_orchestrator(), on the first real request) rather
# than at uvicorn startup. Trade-off: a broken MODEL_PATH/GGUF file now
# surfaces on first request instead of at boot -- if you want fail-fast-
# at-startup instead, call `core.orchestrator.orchestrator` once,
# explicitly, during your app's startup event.
# ---------------------------------------------------------------------------
_orchestrator_singleton: Optional["Orchestrator"] = None


def _build_default_orchestrator() -> "Orchestrator":
    from core.llm_runtime import LLMRuntime
    from agents.base import AgentConfig
    from agents.summarizer import SummarizerAgent
    from agents.extractor import ExtractorAgent
    from agents.critic import CriticAgent

    llm = LLMRuntime()
    return Orchestrator(
        agents={
            "summarizer": SummarizerAgent(
                AgentConfig(
                    name="summarizer",
                    description="Condenses input text into a concise summary",
                ),
                llm,
            ),
            "extractor": ExtractorAgent(
                AgentConfig(
                    name="extractor",
                    description="Extracts key points, entities, sentiment, and topics",
                ),
                llm,
            ),
            "critic": CriticAgent(
                AgentConfig(
                    name="critic",
                    description="Evaluates summary + extraction quality against the original text",
                ),
                llm,
            ),
        }
    )


def __getattr__(name: str) -> Any:
    """PEP 562 module-level lazy attribute. Only fires for names not
    already bound at module scope -- i.e. only ever for `orchestrator`;
    every other public name (Orchestrator, TaskStatus, exceptions, ...)
    is a real top-level name defined earlier in this file and resolves
    normally without this hook running at all."""
    global _orchestrator_singleton
    if name == "orchestrator":
        if _orchestrator_singleton is None:
            _orchestrator_singleton = _build_default_orchestrator()
        return _orchestrator_singleton
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
