"""
Target path in repo: tests/integration/test_pipeline.py

Integration tests for core/orchestrator.py + core/pipeline.py (Day 10).
Extends the test shapes from the Day 13 plan so they can be merged in
directly when the full test suite is built; also covers the hardening
added on top of the original plan.

Run with `-s` to see the print output:
    pytest -s tests/integration/test_pipeline.py

NOTE: an earlier version of this file also had a standalone
`if __name__ == "__main__":` block that re-implemented every test case a
second time in plain Python, so it could be run directly with
`python tests/integration/test_pipeline.py`. That block never executed
under pytest (the condition is only true when the file is run as a
script), which meant coverage tools correctly reported ~50% of this
file as "never run" -- not a bug, just dead weight from coverage's
point of view, and a duplication risk (the two implementations could
silently drift apart over time). Removed in favor of `pytest -s`, which
gives the same visible print() output without maintaining two copies of
every test.
"""

import json
import threading
import time

import pytest
from unittest.mock import Mock

from agents.base import AgentConfig
from agents.critic import CriticAgent
from agents.extractor import ExtractorAgent
from agents.summarizer import SummarizerAgent
from core.orchestrator import (
    InvalidTaskInputError,
    Orchestrator,
    TaskAlreadyRunningError,
    TaskNotFoundError,
)

# A JSON string matching ExtractorAgent's expected schema. LLMRuntime.generate()
# returns a plain str for every agent (summarizer/extractor/critic all share the
# same mocked llm), so this one string has to double as: extractable JSON for
# the extractor, and "just some text" for summarizer/critic, which only care
# that it's a non-empty string.
VALID_EXTRACTION_JSON = json.dumps(
    {
        "key_points": ["point1", "point2"],
        "entities": {"person": ["Alice"], "organization": ["Acme"], "location": []},
        "sentiment": "positive",
        "topics": ["testing"],
    }
)


def make_agents(llm):
    return {
        "summarizer": SummarizerAgent(AgentConfig(name="summarizer", description=""), llm),
        "extractor": ExtractorAgent(AgentConfig(name="extractor", description=""), llm),
        "critic": CriticAgent(AgentConfig(name="critic", description=""), llm),
    }


class TestPipelineIntegration:
    @pytest.fixture
    def mock_llm(self):
        mock = Mock()
        # LLMRuntime.generate() returns a plain str, not a dict.
        mock.generate.return_value = VALID_EXTRACTION_JSON
        return mock

    @pytest.fixture
    def orchestrator(self, mock_llm):
        orch = Orchestrator(make_agents(mock_llm))
        yield orch
        orch.shutdown()

    def test_full_pipeline_execution(self, orchestrator):
        print("\n[test_full_pipeline_execution] creating task...")
        task_id = orchestrator.create_task("Test input")
        print(f"[test_full_pipeline_execution] task_id={task_id}")

        results = orchestrator.execute_pipeline(task_id)
        print(f"[test_full_pipeline_execution] results={results}")

        assert "summary" in results
        assert "extraction" in results
        assert "evaluation" in results
        # Hardening: extraction is normalized to a dict for downstream
        # API schemas (Day 11-12 ExecutionResult.extraction: Dict[str, Any]).
        assert isinstance(results["extraction"], dict)
        print("[test_full_pipeline_execution] PASSED")

    def test_task_status_tracking(self, orchestrator):
        print("\n[test_task_status_tracking] creating task...")
        task_id = orchestrator.create_task("Test input")
        status_before = orchestrator.tasks[task_id]["status"].value
        print(f"[test_task_status_tracking] status before execute: {status_before}")
        assert status_before == "pending"

        orchestrator.execute_pipeline(task_id)
        status_after = orchestrator.tasks[task_id]["status"].value
        print(f"[test_task_status_tracking] status after execute: {status_after}")
        assert status_after == "completed"
        print("[test_task_status_tracking] PASSED")

    def test_reexecute_is_idempotent(self, orchestrator):
        print("\n[test_reexecute_is_idempotent] creating task...")
        task_id = orchestrator.create_task("Test input")

        first = orchestrator.execute_pipeline(task_id)
        print(f"[test_reexecute_is_idempotent] first run results={first}")

        second = orchestrator.execute_pipeline(task_id)
        print(f"[test_reexecute_is_idempotent] second run results={second}")

        assert first == second
        print("[test_reexecute_is_idempotent] PASSED - cached result returned, no re-inference")

    def test_unknown_task_raises(self, orchestrator):
        print("\n[test_unknown_task_raises] executing a task_id that was never created...")
        with pytest.raises(TaskNotFoundError) as exc_info:
            orchestrator.execute_pipeline("nonexistent-id")
        print(f"[test_unknown_task_raises] raised TaskNotFoundError: {exc_info.value}")

        # Compatibility: TaskNotFoundError is also a KeyError.
        with pytest.raises(KeyError):
            orchestrator.execute_pipeline("nonexistent-id")
        print("[test_unknown_task_raises] PASSED - also catchable as KeyError (backward compat)")

    def test_empty_input_rejected(self, orchestrator):
        print("\n[test_empty_input_rejected] creating task with empty input...")
        with pytest.raises(InvalidTaskInputError) as exc_info:
            orchestrator.create_task("")
        print(f"[test_empty_input_rejected] raised InvalidTaskInputError: {exc_info.value}")
        print("[test_empty_input_rejected] PASSED")

    def test_oversized_input_rejected(self, mock_llm):
        print("\n[test_oversized_input_rejected] creating orchestrator with max_input_length=10...")
        orch = Orchestrator(make_agents(mock_llm), max_input_length=10)
        with pytest.raises(InvalidTaskInputError) as exc_info:
            orch.create_task("this input is definitely too long")
        print(f"[test_oversized_input_rejected] raised InvalidTaskInputError: {exc_info.value}")
        orch.shutdown()
        print("[test_oversized_input_rejected] PASSED")

    def test_unknown_task_type_rejected(self, orchestrator):
        print(
            "\n[test_unknown_task_type_rejected] creating task with task_type='not_a_real_type'..."
        )
        with pytest.raises(InvalidTaskInputError) as exc_info:
            orchestrator.create_task("Test input", task_type="not_a_real_type")
        print(f"[test_unknown_task_type_rejected] raised InvalidTaskInputError: {exc_info.value}")
        print("[test_unknown_task_type_rejected] PASSED")

    def test_summarize_only_task_type(self, orchestrator):
        print("\n[test_summarize_only_task_type] creating task with task_type='summarize'...")
        task_id = orchestrator.create_task("Test input", task_type="summarize")
        results = orchestrator.execute_pipeline(task_id)
        print(f"[test_summarize_only_task_type] results={results}")
        assert set(results.keys()) == {"summary"}
        print("[test_summarize_only_task_type] PASSED - only 'summary' key present")

    def test_extractor_bad_json_recovers_gracefully(self):
        print("\n[test_extractor_bad_json_recovers_gracefully] LLM will return invalid JSON...")
        llm = Mock()
        llm.generate.return_value = "not valid json"
        orch = Orchestrator(make_agents(llm))
        task_id = orch.create_task("Test input", task_type="extract")
        results = orch.execute_pipeline(task_id)
        print(f"[test_extractor_bad_json_recovers_gracefully] results={results}")
        assert results["extraction"]["sentiment"] == "neutral"
        orch.shutdown()
        print("[test_extractor_bad_json_recovers_gracefully] PASSED - fell back to default dict")

    def test_agent_failure_marks_task_failed(self):
        print("\n[test_agent_failure_marks_task_failed] LLM will raise an exception...")
        llm = Mock()
        llm.generate.side_effect = Exception("LLM error")
        orch = Orchestrator(make_agents(llm))
        task_id = orch.create_task("Test input")
        with pytest.raises(Exception) as exc_info:
            orch.execute_pipeline(task_id)
        print(f"[test_agent_failure_marks_task_failed] raised: {exc_info.value}")
        print(
            f"[test_agent_failure_marks_task_failed] task status: {orch.tasks[task_id]['status'].value}"
        )
        print(
            f"[test_agent_failure_marks_task_failed] task errors: {orch.tasks[task_id]['errors']}"
        )
        assert orch.tasks[task_id]["status"].value == "failed"
        assert orch.tasks[task_id]["errors"]
        orch.shutdown()
        print("[test_agent_failure_marks_task_failed] PASSED")

    def test_concurrent_execution_of_same_task_is_blocked(self):
        print(
            "\n[test_concurrent_execution_of_same_task_is_blocked] starting slow pipeline on a background thread..."
        )
        llm = Mock()

        def slow_inference(*args, **kwargs):
            time.sleep(0.3)
            return VALID_EXTRACTION_JSON

        llm.generate.side_effect = slow_inference
        orch = Orchestrator(make_agents(llm))
        task_id = orch.create_task("Concurrency test")

        outcomes = {}

        def run():
            try:
                orch.execute_pipeline(task_id)
                outcomes["first"] = "ok"
                print(
                    "[test_concurrent_execution_of_same_task_is_blocked] background thread: execution completed OK"
                )
            except Exception as exc:  # noqa: BLE001
                outcomes["first_error"] = str(exc)
                print(
                    f"[test_concurrent_execution_of_same_task_is_blocked] background thread errored: {exc}"
                )

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)

        print(
            "[test_concurrent_execution_of_same_task_is_blocked] main thread: attempting duplicate execute_pipeline() call..."
        )
        with pytest.raises(TaskAlreadyRunningError) as exc_info:
            orch.execute_pipeline(task_id)
        print(
            f"[test_concurrent_execution_of_same_task_is_blocked] main thread raised TaskAlreadyRunningError: {exc_info.value}"
        )

        t.join()
        print(f"[test_concurrent_execution_of_same_task_is_blocked] outcomes={outcomes}")
        assert outcomes.get("first") == "ok"
        orch.shutdown()
        print("[test_concurrent_execution_of_same_task_is_blocked] PASSED")

    def test_task_not_found_for_missing_agent_at_creation(self, mock_llm):
        print(
            "\n[test_task_not_found_for_missing_agent_at_creation] orchestrator configured with only 'summarizer'..."
        )
        # Requesting full_pipeline without a configured critic should fail
        # fast at create_task(), not mid-pipeline.
        orch = Orchestrator({"summarizer": make_agents(mock_llm)["summarizer"]})
        with pytest.raises(InvalidTaskInputError) as exc_info:
            orch.create_task("Test input", task_type="full_pipeline")
        print(
            f"[test_task_not_found_for_missing_agent_at_creation] raised InvalidTaskInputError: {exc_info.value}"
        )
        orch.shutdown()
        print(
            "[test_task_not_found_for_missing_agent_at_creation] PASSED - failed fast at creation, not mid-pipeline"
        )
