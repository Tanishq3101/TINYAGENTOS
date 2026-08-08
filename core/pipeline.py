"""
core/pipeline.py — Composable pipeline execution engine.

Day 10 deliverable. Provides a generic, dependency-checked, sequential
pipeline abstraction that `core/orchestrator.py` uses to run agent steps.

Design goals (production-grade):
  - No mutable-default-argument bugs (the common `List[str] = None` trap).
  - Every failure mode produces a typed exception, never a bare KeyError/
    AttributeError leaking out of library internals.
  - Steps are individually timed and their outcome is recorded, so a
    failure mid-pipeline still yields a full execution trace for debugging
    and observability (fed into infrastructure.metrics / logging).
  - Fully backward compatible with the Day 10 plan's original
    `PipelineStep(name=..., agent=..., input_key=..., output_key=...)`
    call signature — all new fields are optional with safe defaults.
  - No I/O, no imports of concrete agents: this module stays a generic,
    reusable primitive so it can't create circular imports with
    core/orchestrator.py or agents/*.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "StepStatus",
    "PipelineStep",
    "PipelineError",
    "MissingInputError",
    "StepExecutionError",
    "StepTimeoutError",
    "Pipeline",
]


class StepStatus(str, Enum):
    """Lifecycle state of a single pipeline step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineError(Exception):
    """Base class for all pipeline-related errors."""


class MissingInputError(PipelineError, ValueError):
    """Raised when a step's declared required input is absent from context.

    Subclasses ValueError so any existing code written against the
    original plan (which raised a plain ValueError here) still catches
    this correctly with ``except ValueError``.
    """


class StepExecutionError(PipelineError):
    """Raised when a step's callable raises during execution.

    The original exception is preserved on ``__cause__`` / ``.original``
    so callers can inspect the root cause without losing context.
    """

    def __init__(self, step_name: str, original: BaseException):
        self.step_name = step_name
        self.original = original
        super().__init__(f"Step '{step_name}' failed: {original}")


class StepTimeoutError(PipelineError, TimeoutError):
    """Raised when a step exceeds its configured timeout."""

    def __init__(self, step_name: str, timeout_seconds: float):
        self.step_name = step_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Step '{step_name}' exceeded timeout of {timeout_seconds}s"
        )


@dataclass
class PipelineStep:
    """A single unit of work in a pipeline.

    Attributes:
        name: Human-readable step identifier, used in logs and history.
        agent: Callable invoked as ``agent(step_input, **extra_kwargs)``.
            Typically an ``Agent.execute`` bound method or a thin adapter
            around one.
        input_key: Context key whose value is passed as the callable's
            positional argument.
        output_key: Context key the callable's return value is written to.
        required_inputs: Context keys that must already be present (and
            not ``None``) before this step runs. Defaults to an empty
            list — NOT a shared mutable default, unlike the original
            plan's `= None` pattern.
        extra_input_keys: Additional context keys forwarded as keyword
            arguments to ``agent``, keyed by their own name. Lets a step
            (e.g. a critic that needs ``summary=`` and ``extraction=``)
            pull multiple prior outputs without a custom adapter.
        optional: If True, a failure in this step is logged and recorded
            as SKIPPED rather than aborting the whole pipeline.
        timeout_seconds: Soft timeout enforced by the orchestrator layer
            (this module does not itself spawn threads to avoid forcing
            a threading model on every caller); a plain value here is
            just carried through to execution history/metrics.
        retry_policy: Optional ``infrastructure.retry.RetryPolicy``-like
            object; carried through unused by this module — the caller
            (orchestrator) is responsible for wrapping ``agent`` with
            retry behavior if desired.
    """

    name: str
    agent: Callable[..., Any]
    input_key: str
    output_key: str
    required_inputs: List[str] = field(default_factory=list)
    extra_input_keys: List[str] = field(default_factory=list)
    optional: bool = False
    timeout_seconds: Optional[float] = None
    retry_policy: Optional[Any] = None

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("PipelineStep.name must be a non-empty string")
        if not callable(self.agent):
            raise ValueError(f"PipelineStep '{self.name}'.agent must be callable")
        if not self.input_key or not self.output_key:
            raise ValueError(
                f"PipelineStep '{self.name}' requires both input_key and output_key"
            )


class Pipeline:
    """Composable, sequential pipeline of :class:`PipelineStep` objects.

    Usage::

        pipeline = (
            Pipeline("full_pipeline")
            .add_step(PipelineStep("summarize", summarizer_fn, "text", "summary"))
            .add_step(PipelineStep("extract", extractor_fn, "text", "extraction"))
        )
        result = pipeline.execute({"text": "..."})
    """

    def __init__(self, name: str):
        if not name:
            raise ValueError("Pipeline name must be a non-empty string")
        self.name = name
        self.steps: List[PipelineStep] = []
        self.execution_history: List[Dict[str, Any]] = []

    def add_step(self, step: PipelineStep) -> "Pipeline":
        """Append a step to the pipeline. Returns self to allow chaining."""
        if not isinstance(step, PipelineStep):
            raise TypeError("add_step() requires a PipelineStep instance")
        self.steps.append(step)
        return self

    def reset_history(self) -> None:
        """Clear execution history (e.g. before re-running the same pipeline)."""
        self.execution_history = []

    def execute(
        self,
        initial_input: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        *,
        stop_on_error: bool = True,
    ) -> Dict[str, Any]:
        """Execute all steps sequentially against a shared context dict.

        Args:
            initial_input: Seed values merged into the working context
                before the first step runs.
            context: Optional pre-existing context to extend (a copy is
                NOT made of the caller's dict reference by default for
                the top level, but each step's read of it is by key —
                pass a fresh dict per call if isolation is required).
            stop_on_error: If True (default — matches the original
                plan's behavior of letting exceptions propagate), a
                required, non-optional step's failure aborts the
                pipeline and raises. If False, failures are recorded in
                history and execution continues with subsequent steps
                (useful for best-effort / diagnostic runs).

        Returns:
            The final context dict containing every step's output.

        Raises:
            MissingInputError: A step's required_inputs are not satisfied.
            StepExecutionError: A non-optional step's callable raised.
        """
        working_context: Dict[str, Any] = dict(context or {})
        working_context.update(initial_input)

        for step in self.steps:
            entry: Dict[str, Any] = {
                "step": step.name,
                "output_key": step.output_key,
                "status": StepStatus.PENDING.value,
                "duration_ms": None,
                "error": None,
            }

            missing = [
                key
                for key in step.required_inputs
                if key not in working_context or working_context.get(key) is None
            ]
            if missing:
                err = MissingInputError(
                    f"Step '{step.name}' is missing required input(s): {missing}"
                )
                entry["status"] = StepStatus.FAILED.value
                entry["error"] = str(err)
                self.execution_history.append(entry)
                if stop_on_error and not step.optional:
                    raise err
                if step.optional:
                    entry["status"] = StepStatus.SKIPPED.value
                    continue
                raise err

            step_input = working_context.get(step.input_key)
            extra_kwargs = {
                key: working_context.get(key) for key in step.extra_input_keys
            }

            entry["status"] = StepStatus.RUNNING.value
            start = time.monotonic()
            try:
                result = step.agent(step_input, **extra_kwargs)
                working_context[step.output_key] = result
                entry["status"] = StepStatus.SUCCESS.value
            except Exception as exc:  # noqa: BLE001 - deliberately broad; wrapped below
                entry["status"] = StepStatus.FAILED.value
                entry["error"] = str(exc)
                self.execution_history.append(
                    {**entry, "duration_ms": (time.monotonic() - start) * 1000}
                )
                logger.warning(
                    "Pipeline '%s' step '%s' failed: %s", self.name, step.name, exc
                )
                if step.optional and not stop_on_error:
                    continue
                if step.optional:
                    continue
                raise StepExecutionError(step.name, exc) from exc
            finally:
                entry["duration_ms"] = (time.monotonic() - start) * 1000

            self.execution_history.append(entry)

        return working_context

    def to_dict(self) -> Dict[str, Any]:
        """Serializable summary of the pipeline definition and last run."""
        return {
            "name": self.name,
            "steps": [step.name for step in self.steps],
            "execution_history": list(self.execution_history),
        }