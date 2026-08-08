"""
tests/test_day10.py — Orchestrator & pipeline tests.

FIXED vs. the version that produced `TypeError: unexpected keyword
argument 'use_retry'` — see inline notes at each changed test. Summary
of what changed and why (all verified against the real orchestrator.py
you pasted, not the plan's sketch):

1. Dropped `use_retry=...` from every Orchestrator(...) call — the real
   __init__ has no such parameter, and _run_agent_step() calls
   agent.execute() exactly once with no retry path at all.
2. test_get_unknown_task_raises — orch.get_task() returns None for an
   unknown id (it's written as a "safe" accessor), it does not raise.
   Direct `orch.tasks[task_id]` access *does* raise plain KeyError
   (self.tasks is a plain dict) — that's the pattern routes.py actually
   depends on, so this test now checks both.
3. test_orchestrator_requires_all_agents — the constructor only logs a
   warning for missing standard agents, it never raises. The real
   enforcement is in create_task(), which raises InvalidTaskInputError
   (a ValueError subclass) if the requested task_type needs an agent
   that isn't configured. Rewrote the test around that actual behavior.
4. test_execute_pipeline_failure_marks_task_failed — task["results"] is
   NEVER partially populated on failure (execute_pipeline only assigns
   it in the success branch), so the old assertion that
   task["results"]["summary"] survives a downstream failure was wrong
   for this implementation. Also, the exception that propagates out of
   execute_pipeline() is StepExecutionError (imported from
   core.pipeline), not a bare RuntimeError — I don't have that class's
   exact __str__/attribute shape, so this test now catches Exception
   broadly and checks task["errors"] (which goes through
   Orchestrator._sanitize_error -> str(exc)) instead of asserting on
   the exception object's type directly. Please confirm
   StepExecutionError's shape and tighten this if you want a more
   specific assertion.
5. test_execute_pipeline_retries_transient_failure — REMOVED. It tested
   retry behavior that doesn't exist in the current Orchestrator. Kept
   as a skipped stub with a note in case you want to add real retry
   support later.
6. test_execute_pipeline_failure_marks_task_failed — now that the real
   core/pipeline.py confirms StepExecutionError's exact shape
   (.step_name, .original, and str() ==
   f"Step '{step_name}' failed: {original}"), tightened this from a
   broad `except Exception` to catching StepExecutionError specifically
   and asserting on .step_name.

NOW VERIFIED against the real core/pipeline.py you provided (previous
version of this file guessed at these three, since only the plan's
sketch was available):
- test_generic_pipeline_chaining — FIXED. The original was missing
  `input_key` on every PipelineStep (a required field, no default —
  would raise TypeError at construction). Its step functions also
  assumed the whole context dict gets passed to `agent`
  (`def summarize_step(context): context["input"]...`), but the real
  Pipeline.execute() calls `step.agent(step_input, **extra_kwargs)` —
  a single value from `input_key`, plus kwargs from `extra_input_keys`.
  Rewrote the step functions and PipelineStep construction to match.
  Also fixed the execution_history assertion: entries store
  `StepStatus.SUCCESS.value` (a plain string) not the enum itself, so
  `h["status"] == "success"`, not `h["status"].value == "success"`
  (the latter would AttributeError on a str).
- test_generic_pipeline_missing_required_input — FIXED. Same missing
  `input_key` bug. Now tightened to catch MissingInputError
  specifically (confirmed to subclass ValueError, so `except ValueError`
  still works if you prefer that in other code).
- test_generic_pipeline_with_retry_policy — REPLACED with two tests,
  since the original tested behavior the module explicitly documents as
  NOT its job:
    (a) test_pipeline_does_not_auto_apply_retry_policy — confirms
        retry_policy is inert at the Pipeline level (a flaky step fails
        on its first attempt, call_count == 1, even with retry_policy
        set).
    (b) test_pipeline_step_retries_when_agent_wrapped_with_retry_on_exception
        — the actual intended usage pattern per the docstring: wrap the
        agent callable itself with infrastructure.retry.retry_on_exception
        BEFORE handing it to PipelineStep. Pipeline doesn't need to know
        retries are happening.
"""

from core.orchestrator import (
    InvalidTaskInputError,
    Orchestrator,
    TaskStatus,
)
from core.pipeline import MissingInputError, Pipeline, PipelineStep, StepExecutionError
from infrastructure.retry import RetryPolicy, retry_on_exception

SAMPLE_TEXT = """
Artificial intelligence is transforming how software is built. Small,
efficient language models can now run directly on consumer laptops,
enabling developers to build offline-first AI applications.
"""


def _expected_cleaned_input(text: str) -> str:
    """Mirrors Orchestrator.create_task()'s input normalization exactly:
    strip null bytes, then collapse all whitespace (including the
    newlines in SAMPLE_TEXT's triple-quoted form) down to single spaces
    and trim the ends. task["input"] stores this cleaned form, never the
    raw input verbatim -- assert against this, not against SAMPLE_TEXT
    directly."""
    return " ".join(text.replace("\x00", "").split())


# ============================================================
# Mock agent -- lets us test Orchestrator's task-state machinery
# and error handling without needing the actual LLM loaded.
# ============================================================
class MockAgentConfig:
    def __init__(self, retry_count=1):
        self.retry_count = retry_count


class MockAgent:
    """Mimics agents.base.Agent's .execute() contract: returns
    {'status': 'success'/'error', 'output'/'error': ...}."""

    def __init__(self, output=None, fail=False, fail_times=0, retry_count=1):
        self.output = output
        self.fail = fail
        self.fail_times = fail_times  # fail this many times, then succeed
        self.call_count = 0
        self.config = MockAgentConfig(retry_count=retry_count)

    def execute(self, input_data, **kwargs):
        self.call_count += 1

        if self.fail_times and self.call_count <= self.fail_times:
            return {"status": "error", "error": f"mock transient failure #{self.call_count}"}

        if self.fail:
            return {"status": "error", "error": "mock permanent failure"}

        return {"status": "success", "output": self.output}


# ============================================================
# Orchestrator: task lifecycle
# ============================================================
def test_create_and_get_task():
    agents = {
        "summarizer": MockAgent(output="summary"),
        "extractor": MockAgent(output='{"key_points": []}'),
        "critic": MockAgent(output={"score": 8, "evaluation": "good"}),
    }
    orch = Orchestrator(agents)

    task_id = orch.create_task(SAMPLE_TEXT)
    task = orch.get_task(task_id)

    assert task["status"] == TaskStatus.PENDING
    assert task["input"] == _expected_cleaned_input(SAMPLE_TEXT)
    assert task["results"] == {}

    print("✅ create_task/get_task work")


def test_get_unknown_task_raises():
    orch = Orchestrator(
        {
            "summarizer": MockAgent(output="x"),
            "extractor": MockAgent(output="x"),
            "critic": MockAgent(output="x"),
        }
    )

    # get_task() is a "safe" accessor -- returns None, does not raise.
    assert orch.get_task("does-not-exist") is None

    # Direct dict access on .tasks (the pattern routes.py uses for
    # get_task_status/execute_task) raises a plain KeyError.
    try:
        orch.tasks["does-not-exist"]
        assert False, "expected KeyError"
    except KeyError:
        print("✅ get_task() returns None; orch.tasks[...] raises KeyError")


def test_missing_agent_only_warns_at_construction():
    """Constructor does NOT raise for missing standard agents -- it only
    logs a warning. Confirms Orchestrator({"summarizer": ...}) succeeds."""
    orch = Orchestrator({"summarizer": MockAgent(output="x")})
    assert "summarizer" in orch.agents
    assert "extractor" not in orch.agents
    print("✅ missing agents at construction time only warn, do not raise")


def test_create_task_raises_when_required_agent_missing():
    """The real enforcement point: create_task() checks
    _TASK_TYPE_REQUIRED_AGENTS and raises InvalidTaskInputError (a
    ValueError subclass) if the requested task_type needs an agent that
    isn't configured."""
    orch = Orchestrator({"summarizer": MockAgent(output="x")})

    try:
        orch.create_task(SAMPLE_TEXT, task_type="full_pipeline")
        assert False, "expected InvalidTaskInputError"
    except InvalidTaskInputError as e:
        assert "extractor" in str(e) or "critic" in str(e)
        print(f"✅ create_task rejects task_type needing missing agents: {e}")

    # "summarize" only needs the summarizer, which IS configured -- should
    # succeed even though extractor/critic are missing.
    task_id = orch.create_task(SAMPLE_TEXT, task_type="summarize")
    assert task_id
    print("✅ create_task succeeds for a task_type whose required agents are present")


# ============================================================
# Orchestrator: successful pipeline (mocked, fast, deterministic)
# ============================================================
def test_execute_pipeline_success():
    agents = {
        "summarizer": MockAgent(output="A short summary."),
        "extractor": MockAgent(output='{"key_points": ["a", "b"]}'),
        "critic": MockAgent(output={"score": 9, "evaluation": "solid"}),
    }
    orch = Orchestrator(agents)

    task_id = orch.create_task(SAMPLE_TEXT)
    results = orch.execute_pipeline(task_id)

    task = orch.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert results["summary"] == "A short summary."
    # NOTE: orchestrator.py normalizes extractor output into a parsed dict
    # via _normalize_extraction() before storing it in results["extraction"]
    # -- it is NOT the raw JSON string. Confirm this matches your intent;
    # the original test asserted the raw string, which is wrong for this
    # implementation.
    assert results["extraction"] == {"key_points": ["a", "b"]}
    assert results["evaluation"]["score"] == 9

    print("✅ execute_pipeline succeeds and populates results correctly")


# ============================================================
# Orchestrator: failure handling
# ============================================================
def test_execute_pipeline_failure_marks_task_failed():
    agents = {
        "summarizer": MockAgent(output="summary ok"),
        "extractor": MockAgent(fail=True),  # always fails
        "critic": MockAgent(output={"score": 5}),
    }
    orch = Orchestrator(agents)

    task_id = orch.create_task(SAMPLE_TEXT)

    try:
        orch.execute_pipeline(task_id)
        assert False, "expected StepExecutionError to propagate"
    except StepExecutionError as e:
        # Now confirmed via the real core/pipeline.py: StepExecutionError
        # exposes .step_name and wraps the original exception, with
        # str() == f"Step '{step_name}' failed: {original}".
        assert e.step_name == "extractor"
        assert "mock permanent failure" in str(e)

    task = orch.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert len(task["errors"]) == 1
    assert "mock permanent failure" in task["errors"][0]
    # Results are NOT partially populated on failure in this
    # implementation -- execute_pipeline only assigns task["results"] in
    # its success branch, so it stays exactly what create_task() set:
    # an empty dict. (The original test asserted the summarizer's output
    # survived here -- that's not how this Orchestrator works.)
    assert task["results"] == {}

    print("✅ pipeline failure correctly marks task FAILED, results stay empty")


def test_execute_pipeline_retries_transient_failure():
    """REMOVED (was testing nonexistent retry behavior).

    The real Orchestrator._run_agent_step() calls agent.execute() exactly
    once -- there is no retry path, so an agent with fail_times=2 simply
    fails the task on the first attempt. If you want retry-on-transient-
    failure as a real feature, it needs to be added to
    Orchestrator._run_agent_step() (e.g. wrap the agent.execute() call
    with infrastructure.retry.retry_on_exception / RetryPolicy). Happy to
    build that if you want it -- say the word and I'll wire it in and
    restore a real version of this test.
    """
    print("⚠️  skipped: retry behavior not implemented in Orchestrator yet")


# ============================================================
# Generic Pipeline (core/pipeline.py) -- verified against the real file.
# ============================================================
def test_generic_pipeline_chaining():
    summarizer = MockAgent(output="a summary")
    extractor = MockAgent(output='{"key_points": []}')
    critic = MockAgent(output={"score": 10})

    # Real Pipeline.execute() calls step.agent(step_input, **extra_kwargs)
    # -- step_input is the single value at context[input_key], NOT the
    # whole context dict. extra_input_keys supplies additional kwargs
    # (e.g. critic needs summary= and extraction= from prior steps).
    def summarize_step(text, **kwargs):
        result = summarizer.execute(text)
        if result["status"] != "success":
            raise RuntimeError(result["error"])
        return result["output"]

    def extract_step(text, **kwargs):
        result = extractor.execute(text)
        if result["status"] != "success":
            raise RuntimeError(result["error"])
        return result["output"]

    def critic_step(text, summary=None, extraction=None):
        result = critic.execute(text, summary=summary, extraction=extraction)
        if result["status"] != "success":
            raise RuntimeError(result["error"])
        return result["output"]

    pipeline = (
        Pipeline("test_pipeline")
        .add_step(
            PipelineStep(
                name="summarize", agent=summarize_step, input_key="input", output_key="summary"
            )
        )
        .add_step(
            PipelineStep(
                name="extract", agent=extract_step, input_key="input", output_key="extraction"
            )
        )
        .add_step(
            PipelineStep(
                name="critique",
                agent=critic_step,
                input_key="input",
                output_key="evaluation",
                required_inputs=["summary", "extraction"],
                extra_input_keys=["summary", "extraction"],
            )
        )
    )

    result = pipeline.execute({"input": SAMPLE_TEXT})

    assert result["summary"] == "a summary"
    assert result["extraction"] == '{"key_points": []}'
    assert result["evaluation"]["score"] == 10
    assert len(pipeline.execution_history) == 3
    # execution_history stores StepStatus.SUCCESS.value (a plain string),
    # not the enum member itself -- .value on a str would AttributeError.
    assert all(h["status"] == "success" for h in pipeline.execution_history)

    print("✅ generic Pipeline chains steps and shares context correctly")


def test_generic_pipeline_missing_required_input():
    def critic_step(text, **kwargs):
        return {"score": 1}

    pipeline = Pipeline("broken_pipeline").add_step(
        PipelineStep(
            name="critique",
            agent=critic_step,
            input_key="input",  # required field, unused since the step
            output_key="evaluation",  # never runs -- required_inputs fails first
            required_inputs=["summary"],  # never provided
        )
    )

    try:
        pipeline.execute({"input": SAMPLE_TEXT})
        assert False, "expected MissingInputError"
    except MissingInputError as e:  # also a ValueError subclass, per its docstring
        assert "summary" in str(e)
        print(f"✅ missing required_inputs correctly rejected: {e}")


def test_pipeline_does_not_auto_apply_retry_policy():
    """Confirms PipelineStep.retry_policy is inert at the Pipeline level --
    its own docstring says it's "carried through unused by this module";
    the caller (Orchestrator) is responsible for wrapping the agent
    callable with retry behavior if desired. A step that raises fails on
    its first attempt regardless of retry_policy being set."""
    call_count = {"n": 0}

    def flaky_step(text, **kwargs):
        call_count["n"] += 1
        raise ValueError("transient")

    pipeline = Pipeline("retry_pipeline").add_step(
        PipelineStep(
            name="flaky",
            agent=flaky_step,
            input_key="input",
            output_key="result",
            retry_policy=RetryPolicy(max_retries=3, base_delay=0.01),
        )
    )

    try:
        pipeline.execute({"input": SAMPLE_TEXT})
        assert False, "expected StepExecutionError -- Pipeline does not auto-retry"
    except StepExecutionError:
        pass

    assert call_count["n"] == 1  # no retry happened at the Pipeline level
    print("✅ confirms retry_policy is inert at the Pipeline level (by design)")


def test_pipeline_step_retries_when_agent_wrapped_with_retry_on_exception():
    """The actual intended usage pattern: wrap the agent callable itself
    with infrastructure.retry.retry_on_exception BEFORE handing it to
    PipelineStep. Pipeline.execute() doesn't need to know retries are
    happening -- the wrapped callable just takes longer on failed
    attempts."""
    call_count = {"n": 0}

    def flaky(text, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ValueError("transient")
        return "eventually worked"

    wrapped = retry_on_exception(RetryPolicy(max_retries=3, base_delay=0.01), (ValueError,))(flaky)

    pipeline = Pipeline("retry_pipeline").add_step(
        PipelineStep(name="flaky", agent=wrapped, input_key="input", output_key="result")
    )

    result = pipeline.execute({"input": SAMPLE_TEXT})
    assert result["result"] == "eventually worked"
    assert call_count["n"] == 3

    print(
        "✅ retry-on-transient-failure works when the agent is pre-wrapped with retry_on_exception"
    )


# ============================================================
# Real end-to-end test using the actual LLM + agents
# ============================================================
def test_real_orchestrator_end_to_end():
    """Uses the real LLMRuntime and Summarizer/Extractor/Critic
    agents. Slower, and depends on the model being downloaded/loaded,
    but proves the whole thing actually works together."""
    from core.llm_runtime import LLMRuntime
    from agents.base import AgentConfig
    from agents.summarizer import SummarizerAgent
    from agents.extractor import ExtractorAgent
    from agents.critic import CriticAgent

    llm = LLMRuntime()

    agents = {
        "summarizer": SummarizerAgent(
            AgentConfig(name="summarizer", description="", max_tokens=256), llm
        ),
        "extractor": ExtractorAgent(
            AgentConfig(name="extractor", description="", max_tokens=384), llm
        ),
        "critic": CriticAgent(AgentConfig(name="critic", description="", max_tokens=400), llm),
    }

    orch = Orchestrator(agents)
    task_id = orch.create_task(SAMPLE_TEXT)
    results = orch.execute_pipeline(task_id)

    task = orch.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert "summary" in results and len(results["summary"]) > 0
    assert "extraction" in results
    assert "evaluation" in results

    print(f"✅ Real end-to-end pipeline completed | task_id={task_id}")
    print(f"   Summary: {results['summary'][:80]}...")
    print(f"   Evaluation score: {results['evaluation'].get('score')}")


if __name__ == "__main__":
    test_create_and_get_task()
    test_get_unknown_task_raises()
    test_missing_agent_only_warns_at_construction()
    test_create_task_raises_when_required_agent_missing()
    test_execute_pipeline_success()
    test_execute_pipeline_failure_marks_task_failed()
    test_execute_pipeline_retries_transient_failure()
    test_generic_pipeline_chaining()
    test_generic_pipeline_missing_required_input()
    test_pipeline_does_not_auto_apply_retry_policy()
    test_pipeline_step_retries_when_agent_wrapped_with_retry_on_exception()
    test_real_orchestrator_end_to_end()
    print("\nAll Day 10 tests passed.")
