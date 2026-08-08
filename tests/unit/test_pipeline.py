"""
tests/test_pipeline.py — unit tests for core/pipeline.py in isolation.

Complements the 4 pipeline-related tests already in test_day10.py (which
exercise Pipeline through Orchestrator-shaped usage). This file drills
into core/pipeline.py's own documented guarantees directly:
  - PipelineStep.__post_init__ validation
  - No mutable-default-argument sharing across instances
  - optional=True skip-vs-raise semantics
  - stop_on_error's actual scope (confirmed by reading execute(): it only
    affects the "agent raised during execution" path, NOT the "required
    input missing" path -- a non-optional step with missing required
    input always raises MissingInputError regardless of stop_on_error)
  - StepExecutionError / MissingInputError / StepTimeoutError shapes
  - execution_history bookkeeping, reset_history(), to_dict()

Run with:
    pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import pytest

from core.pipeline import (
    MissingInputError,
    Pipeline,
    PipelineError,
    PipelineStep,
    StepExecutionError,
    StepStatus,
    StepTimeoutError,
)


# ---------------------------------------------------------------------------
# PipelineStep construction / validation
# ---------------------------------------------------------------------------
def test_pipeline_step_valid_construction() -> None:
    step = PipelineStep(name="s1", agent=lambda x: x, input_key="in", output_key="out")
    assert step.name == "s1"
    assert step.required_inputs == []
    assert step.extra_input_keys == []
    assert step.optional is False
    assert step.timeout_seconds is None
    assert step.retry_policy is None


def test_pipeline_step_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineStep(name="", agent=lambda x: x, input_key="in", output_key="out")


def test_pipeline_step_non_string_name_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineStep(name=None, agent=lambda x: x, input_key="in", output_key="out")  # type: ignore[arg-type]


def test_pipeline_step_non_callable_agent_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineStep(name="s1", agent="not callable", input_key="in", output_key="out")  # type: ignore[arg-type]


def test_pipeline_step_missing_input_key_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineStep(name="s1", agent=lambda x: x, input_key="", output_key="out")


def test_pipeline_step_missing_output_key_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineStep(name="s1", agent=lambda x: x, input_key="in", output_key="")


def test_pipeline_step_required_inputs_default_not_shared_across_instances() -> None:
    """The dataclass docstring specifically calls out avoiding the
    `List[str] = None` / shared-mutable-default trap. Confirm two
    instances don't share the same list object."""
    step_a = PipelineStep(name="a", agent=lambda x: x, input_key="in", output_key="out")
    step_b = PipelineStep(name="b", agent=lambda x: x, input_key="in", output_key="out")

    step_a.required_inputs.append("something")
    assert step_a.required_inputs == ["something"]
    assert step_b.required_inputs == []  # unaffected by step_a's mutation

    step_a.extra_input_keys.append("extra")
    assert step_b.extra_input_keys == []


# ---------------------------------------------------------------------------
# Pipeline construction / add_step
# ---------------------------------------------------------------------------
def test_pipeline_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        Pipeline("")


def test_add_step_requires_pipeline_step_instance() -> None:
    pipeline = Pipeline("p1")
    with pytest.raises(TypeError):
        pipeline.add_step({"name": "not a real step"})  # type: ignore[arg-type]


def test_add_step_returns_self_for_chaining() -> None:
    pipeline = Pipeline("p1")
    step = PipelineStep(name="s1", agent=lambda x: x, input_key="in", output_key="out")
    result = pipeline.add_step(step)
    assert result is pipeline
    assert pipeline.steps == [step]


# ---------------------------------------------------------------------------
# execute() -- basic chaining
# ---------------------------------------------------------------------------
def test_execute_single_step_success() -> None:
    pipeline = Pipeline("p1").add_step(
        PipelineStep(name="upper", agent=lambda x: x.upper(), input_key="text", output_key="result")
    )
    result = pipeline.execute({"text": "hello"})
    assert result["result"] == "HELLO"
    assert result["text"] == "hello"  # initial_input preserved in context


def test_execute_chains_output_to_next_steps_input() -> None:
    pipeline = (
        Pipeline("p1")
        .add_step(
            PipelineStep(
                name="step1", agent=lambda x: x + 1, input_key="n", output_key="after_step1"
            )
        )
        .add_step(
            PipelineStep(
                name="step2",
                agent=lambda x: x * 2,
                input_key="after_step1",
                output_key="after_step2",
            )
        )
    )
    result = pipeline.execute({"n": 5})
    assert result["after_step1"] == 6
    assert result["after_step2"] == 12


def test_execute_extra_input_keys_forwarded_as_kwargs() -> None:
    def combine(base, bonus=None, tag=None):
        return f"{base}-{bonus}-{tag}"

    pipeline = (
        Pipeline("p1")
        .add_step(
            PipelineStep(name="seed_bonus", agent=lambda x: "B", input_key="n", output_key="bonus")
        )
        .add_step(
            PipelineStep(name="seed_tag", agent=lambda x: "T", input_key="n", output_key="tag")
        )
        .add_step(
            PipelineStep(
                name="combine",
                agent=combine,
                input_key="n",
                output_key="result",
                extra_input_keys=["bonus", "tag"],
            )
        )
    )
    result = pipeline.execute({"n": "base"})
    assert result["result"] == "base-B-T"


def test_execute_accepts_pre_existing_context() -> None:
    pipeline = Pipeline("p1").add_step(
        PipelineStep(name="s1", agent=lambda x: x + 1, input_key="n", output_key="out")
    )
    result = pipeline.execute({"n": 1}, context={"preexisting": "kept"})
    assert result["preexisting"] == "kept"
    assert result["out"] == 2


# ---------------------------------------------------------------------------
# execute() -- missing required_inputs
# NOTE: reading execute() carefully -- a non-optional step with missing
# required_inputs ALWAYS raises MissingInputError, regardless of
# stop_on_error. Only step.optional determines skip-vs-raise for this
# specific failure mode; stop_on_error only governs the "agent callable
# raised during execution" path (see tests further down).
# ---------------------------------------------------------------------------
def test_execute_missing_required_input_raises_even_with_stop_on_error_false() -> None:
    pipeline = Pipeline("p1").add_step(
        PipelineStep(
            name="needs_x",
            agent=lambda n: n,
            input_key="n",
            output_key="out",
            required_inputs=["missing_key"],
        )
    )
    with pytest.raises(MissingInputError):
        pipeline.execute({"n": 1}, stop_on_error=False)


def test_execute_missing_required_input_on_optional_step_is_skipped_not_raised() -> None:
    pipeline = (
        Pipeline("p1")
        .add_step(
            PipelineStep(
                name="needs_x",
                agent=lambda n: "should not run",
                input_key="n",
                output_key="out",
                required_inputs=["missing_key"],
                optional=True,
            )
        )
        .add_step(
            PipelineStep(name="after", agent=lambda n: n + 1, input_key="n", output_key="after")
        )
    )
    result = pipeline.execute({"n": 1})

    # optional step was skipped -- its output_key never got set, and the
    # pipeline continued to the next step rather than aborting.
    assert "out" not in result
    assert result["after"] == 2

    skipped_entries = [h for h in pipeline.execution_history if h["step"] == "needs_x"]
    assert len(skipped_entries) == 1
    assert skipped_entries[0]["status"] == StepStatus.SKIPPED.value


def test_execute_missing_required_input_error_message_lists_missing_keys() -> None:
    pipeline = Pipeline("p1").add_step(
        PipelineStep(
            name="needs_two",
            agent=lambda n: n,
            input_key="n",
            output_key="out",
            required_inputs=["alpha", "beta"],
        )
    )
    with pytest.raises(MissingInputError) as exc_info:
        pipeline.execute({"n": 1})
    assert "alpha" in str(exc_info.value)
    assert "beta" in str(exc_info.value)


def test_execute_required_input_present_but_none_still_counts_as_missing() -> None:
    """execute() checks `working_context.get(key) is None` too, not just
    key-not-present -- a required input explicitly set to None should
    still be treated as missing."""
    pipeline = Pipeline("p1").add_step(
        PipelineStep(
            name="needs_x",
            agent=lambda n: n,
            input_key="n",
            output_key="out",
            required_inputs=["dep"],
        )
    )
    with pytest.raises(MissingInputError):
        pipeline.execute({"n": 1, "dep": None})


# ---------------------------------------------------------------------------
# execute() -- agent callable raises during execution
# ---------------------------------------------------------------------------
def test_execute_agent_exception_wraps_in_step_execution_error() -> None:
    def boom(n):
        raise ValueError("kaboom")

    pipeline = Pipeline("p1").add_step(
        PipelineStep(name="boom_step", agent=boom, input_key="n", output_key="out")
    )
    with pytest.raises(StepExecutionError) as exc_info:
        pipeline.execute({"n": 1})

    err = exc_info.value
    assert err.step_name == "boom_step"
    assert isinstance(err.original, ValueError)
    assert "kaboom" in str(err)

    failed_entries = [h for h in pipeline.execution_history if h["step"] == "boom_step"]
    assert len(failed_entries) == 1
    assert failed_entries[0]["status"] == StepStatus.FAILED.value
    assert "kaboom" in failed_entries[0]["error"]
    assert failed_entries[0]["duration_ms"] >= 0


def test_execute_optional_step_exception_is_swallowed_and_pipeline_continues() -> None:
    def boom(n):
        raise ValueError("kaboom")

    pipeline = (
        Pipeline("p1")
        .add_step(
            PipelineStep(
                name="boom_step", agent=boom, input_key="n", output_key="out", optional=True
            )
        )
        .add_step(
            PipelineStep(name="after", agent=lambda n: n + 1, input_key="n", output_key="after")
        )
    )
    result = pipeline.execute({"n": 1})

    assert "out" not in result  # boom_step's output never got written
    assert result["after"] == 2  # pipeline continued past the failure

    failed_entries = [h for h in pipeline.execution_history if h["step"] == "boom_step"]
    assert len(failed_entries) == 1
    assert failed_entries[0]["status"] == StepStatus.FAILED.value


def test_execute_non_optional_step_exception_raises_regardless_of_stop_on_error() -> None:
    def boom(n):
        raise ValueError("kaboom")

    pipeline = Pipeline("p1").add_step(
        PipelineStep(name="boom_step", agent=boom, input_key="n", output_key="out")
    )
    # stop_on_error=False does not rescue a non-optional step's exception --
    # only step.optional does.
    with pytest.raises(StepExecutionError):
        pipeline.execute({"n": 1}, stop_on_error=False)


# ---------------------------------------------------------------------------
# reset_history() / to_dict()
# ---------------------------------------------------------------------------
def test_reset_history_clears_execution_history() -> None:
    pipeline = Pipeline("p1").add_step(
        PipelineStep(name="s1", agent=lambda x: x, input_key="n", output_key="out")
    )
    pipeline.execute({"n": 1})
    assert len(pipeline.execution_history) == 1

    pipeline.reset_history()
    assert pipeline.execution_history == []


def test_to_dict_returns_name_steps_and_history() -> None:
    pipeline = (
        Pipeline("p1")
        .add_step(PipelineStep(name="s1", agent=lambda x: x, input_key="n", output_key="mid"))
        .add_step(PipelineStep(name="s2", agent=lambda x: x, input_key="mid", output_key="out"))
    )
    pipeline.execute({"n": 1})

    summary = pipeline.to_dict()
    assert summary["name"] == "p1"
    assert summary["steps"] == ["s1", "s2"]
    assert len(summary["execution_history"]) == 2


# ---------------------------------------------------------------------------
# Exception type shapes (constructed directly, not via Pipeline.execute())
# ---------------------------------------------------------------------------
def test_missing_input_error_is_a_value_error() -> None:
    err = MissingInputError("missing stuff")
    assert isinstance(err, ValueError)
    assert isinstance(err, PipelineError)


def test_step_execution_error_shape() -> None:
    original = RuntimeError("root cause")
    err = StepExecutionError("my_step", original)
    assert err.step_name == "my_step"
    assert err.original is original
    assert "my_step" in str(err)
    assert "root cause" in str(err)
    assert isinstance(err, PipelineError)


def test_step_timeout_error_shape() -> None:
    err = StepTimeoutError("slow_step", 30.0)
    assert err.step_name == "slow_step"
    assert err.timeout_seconds == 30.0
    assert "slow_step" in str(err)
    assert "30.0" in str(err)
    assert isinstance(err, TimeoutError)
    assert isinstance(err, PipelineError)
