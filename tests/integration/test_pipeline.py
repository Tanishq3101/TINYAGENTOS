"""
Target path in repo: tests/integration/test_pipeline.py

Integration tests for core/orchestrator.py + core/pipeline.py (Day 10).
Extends the test shapes from the Day 13 plan so they can be merged in
directly when the full test suite is built; also covers the hardening
added on top of the original plan.

Run with `-s` to see the print output: pytest -s tests/integration/test_pipeline.py
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
VALID_EXTRACTION_JSON = json.dumps({
    "key_points": ["point1", "point2"],
    "entities": {"person": ["Alice"], "organization": ["Acme"], "location": []},
    "sentiment": "positive",
    "topics": ["testing"],
})


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
        print("\n[test_unknown_task_type_rejected] creating task with task_type='not_a_real_type'...")
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
        print(f"[test_agent_failure_marks_task_failed] task status: {orch.tasks[task_id]['status'].value}")
        print(f"[test_agent_failure_marks_task_failed] task errors: {orch.tasks[task_id]['errors']}")
        assert orch.tasks[task_id]["status"].value == "failed"
        assert orch.tasks[task_id]["errors"]
        orch.shutdown()
        print("[test_agent_failure_marks_task_failed] PASSED")

    def test_concurrent_execution_of_same_task_is_blocked(self):
        print("\n[test_concurrent_execution_of_same_task_is_blocked] starting slow pipeline on a background thread...")
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
                print("[test_concurrent_execution_of_same_task_is_blocked] background thread: execution completed OK")
            except Exception as exc:  # noqa: BLE001
                outcomes["first_error"] = str(exc)
                print(f"[test_concurrent_execution_of_same_task_is_blocked] background thread errored: {exc}")

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)

        print("[test_concurrent_execution_of_same_task_is_blocked] main thread: attempting duplicate execute_pipeline() call...")
        with pytest.raises(TaskAlreadyRunningError) as exc_info:
            orch.execute_pipeline(task_id)
        print(f"[test_concurrent_execution_of_same_task_is_blocked] main thread raised TaskAlreadyRunningError: {exc_info.value}")

        t.join()
        print(f"[test_concurrent_execution_of_same_task_is_blocked] outcomes={outcomes}")
        assert outcomes.get("first") == "ok"
        orch.shutdown()
        print("[test_concurrent_execution_of_same_task_is_blocked] PASSED")

    def test_task_not_found_for_missing_agent_at_creation(self, mock_llm):
        print("\n[test_task_not_found_for_missing_agent_at_creation] orchestrator configured with only 'summarizer'...")
        # Requesting full_pipeline without a configured critic should fail
        # fast at create_task(), not mid-pipeline.
        orch = Orchestrator({"summarizer": make_agents(mock_llm)["summarizer"]})
        with pytest.raises(InvalidTaskInputError) as exc_info:
            orch.create_task("Test input", task_type="full_pipeline")
        print(f"[test_task_not_found_for_missing_agent_at_creation] raised InvalidTaskInputError: {exc_info.value}")
        orch.shutdown()
        print("[test_task_not_found_for_missing_agent_at_creation] PASSED - failed fast at creation, not mid-pipeline")


if __name__ == "__main__":
    # `python -m tests.integration.test_pipeline` (or plain `python
    # tests/integration/test_pipeline.py`) only imports this module — it
    # does not run pytest's collector, so the test methods above never
    # execute on their own. Rather than shell out to `pytest.main()`
    # (which turned out to interact badly with this project's pytest.ini /
    # pyproject.toml settings — captured output, plugin requirements,
    # etc.), this runs the same scenarios directly in plain Python with no
    # pytest dependency at all, so `print()` output is always visible no
    # matter how pytest is configured.
    #
    # This is a convenience path for a quick manual check. For real runs
    # (CI, coverage, fixtures reused across the full suite) use pytest
    # directly:  pytest -s tests/integration/test_pipeline.py

    def _expect_raises(exc_type, fn, *args, **kwargs):
        """Minimal pytest.raises() replacement for standalone execution."""
        try:
            fn(*args, **kwargs)
        except exc_type as exc:
            return exc
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}"
            ) from exc
        raise AssertionError(f"expected {exc_type.__name__}, but nothing was raised")

    def _run_standalone() -> int:
        passed, failed = 0, 0

        def run_case(name, fn):
            nonlocal passed, failed
            print(f"\n{'=' * 70}")
            print(f"RUNNING: {name}")
            print("=" * 70)
            try:
                fn()
                print(f"RESULT: {name} -> PASSED")
                passed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"RESULT: {name} -> FAILED ({type(exc).__name__}: {exc})")
                failed += 1

        def make_mock_llm(text=None, side_effect=None):
            llm = Mock()
            if side_effect is not None:
                llm.generate.side_effect = side_effect
            else:
                # LLMRuntime.generate() returns a plain str. Default to a
                # valid extraction JSON string so it works for summarizer,
                # extractor, and critic alike.
                llm.generate.return_value = text if text is not None else VALID_EXTRACTION_JSON
            return llm

        def case_full_pipeline():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                task_id = orch.create_task("Test input")
                print(f"  task_id={task_id}")
                results = orch.execute_pipeline(task_id)
                print(f"  results={results}")
                assert "summary" in results and "extraction" in results and "evaluation" in results
                assert isinstance(results["extraction"], dict)
            finally:
                orch.shutdown()

        def case_status_tracking():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                task_id = orch.create_task("Test input")
                status_before = orch.tasks[task_id]["status"].value
                print(f"  status before execute: {status_before}")
                assert status_before == "pending"
                orch.execute_pipeline(task_id)
                status_after = orch.tasks[task_id]["status"].value
                print(f"  status after execute: {status_after}")
                assert status_after == "completed"
            finally:
                orch.shutdown()

        def case_idempotent_reexecute():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                task_id = orch.create_task("Test input")
                first = orch.execute_pipeline(task_id)
                second = orch.execute_pipeline(task_id)
                print(f"  first ={first}")
                print(f"  second={second}")
                assert first == second
            finally:
                orch.shutdown()

        def case_unknown_task():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                exc = _expect_raises(TaskNotFoundError, orch.execute_pipeline, "nonexistent-id")
                print(f"  raised TaskNotFoundError: {exc}")
                _expect_raises(KeyError, orch.execute_pipeline, "nonexistent-id")
                print("  also catchable as KeyError (backward compat)")
            finally:
                orch.shutdown()

        def case_empty_input():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                exc = _expect_raises(InvalidTaskInputError, orch.create_task, "")
                print(f"  raised InvalidTaskInputError: {exc}")
            finally:
                orch.shutdown()

        def case_oversized_input():
            orch = Orchestrator(make_agents(make_mock_llm()), max_input_length=10)
            try:
                exc = _expect_raises(
                    InvalidTaskInputError,
                    orch.create_task,
                    "this input is definitely too long",
                )
                print(f"  raised InvalidTaskInputError: {exc}")
            finally:
                orch.shutdown()

        def case_unknown_task_type():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                exc = _expect_raises(
                    InvalidTaskInputError,
                    orch.create_task,
                    "Test input",
                    task_type="not_a_real_type",
                )
                print(f"  raised InvalidTaskInputError: {exc}")
            finally:
                orch.shutdown()

        def case_summarize_only():
            orch = Orchestrator(make_agents(make_mock_llm()))
            try:
                task_id = orch.create_task("Test input", task_type="summarize")
                results = orch.execute_pipeline(task_id)
                print(f"  results={results}")
                assert set(results.keys()) == {"summary"}
            finally:
                orch.shutdown()

        def case_extractor_bad_json():
            orch = Orchestrator(make_agents(make_mock_llm(text="not valid json")))
            try:
                task_id = orch.create_task("Test input", task_type="extract")
                results = orch.execute_pipeline(task_id)
                print(f"  results={results}")
                assert results["extraction"]["sentiment"] == "neutral"
            finally:
                orch.shutdown()

        def case_agent_failure():
            orch = Orchestrator(make_agents(make_mock_llm(side_effect=Exception("LLM error"))))
            try:
                task_id = orch.create_task("Test input")
                exc = _expect_raises(Exception, orch.execute_pipeline, task_id)
                print(f"  raised: {exc}")
                print(f"  task status: {orch.tasks[task_id]['status'].value}")
                print(f"  task errors: {orch.tasks[task_id]['errors']}")
                assert orch.tasks[task_id]["status"].value == "failed"
                assert orch.tasks[task_id]["errors"]
            finally:
                orch.shutdown()

        def case_concurrent_execution_blocked():
            def slow_inference(*args, **kwargs):
                time.sleep(0.3)
                return VALID_EXTRACTION_JSON

            orch = Orchestrator(make_agents(make_mock_llm(side_effect=slow_inference)))
            try:
                task_id = orch.create_task("Concurrency test")
                outcomes = {}

                def run():
                    try:
                        orch.execute_pipeline(task_id)
                        outcomes["first"] = "ok"
                        print("  background thread: execution completed OK")
                    except Exception as exc:  # noqa: BLE001
                        outcomes["first_error"] = str(exc)
                        print(f"  background thread errored: {exc}")

                t = threading.Thread(target=run)
                t.start()
                time.sleep(0.05)

                print("  main thread: attempting duplicate execute_pipeline() call...")
                exc = _expect_raises(TaskAlreadyRunningError, orch.execute_pipeline, task_id)
                print(f"  main thread raised TaskAlreadyRunningError: {exc}")

                t.join()
                print(f"  outcomes={outcomes}")
                assert outcomes.get("first") == "ok"
            finally:
                orch.shutdown()

        def case_missing_agent_at_creation():
            llm = make_mock_llm()
            orch = Orchestrator({"summarizer": make_agents(llm)["summarizer"]})
            try:
                exc = _expect_raises(
                    InvalidTaskInputError,
                    orch.create_task,
                    "Test input",
                    task_type="full_pipeline",
                )
                print(f"  raised InvalidTaskInputError: {exc}")
            finally:
                orch.shutdown()

        run_case("full_pipeline_execution", case_full_pipeline)
        run_case("task_status_tracking", case_status_tracking)
        run_case("reexecute_is_idempotent", case_idempotent_reexecute)
        run_case("unknown_task_raises", case_unknown_task)
        run_case("empty_input_rejected", case_empty_input)
        run_case("oversized_input_rejected", case_oversized_input)
        run_case("unknown_task_type_rejected", case_unknown_task_type)
        run_case("summarize_only_task_type", case_summarize_only)
        run_case("extractor_bad_json_recovers_gracefully", case_extractor_bad_json)
        run_case("agent_failure_marks_task_failed", case_agent_failure)
        run_case("concurrent_execution_of_same_task_is_blocked", case_concurrent_execution_blocked)
        run_case("task_not_found_for_missing_agent_at_creation", case_missing_agent_at_creation)

        print(f"\n{'=' * 70}")
        print(f"SUMMARY: {passed} passed, {failed} failed")
        print("=" * 70)
        return 1 if failed else 0

    raise SystemExit(_run_standalone())