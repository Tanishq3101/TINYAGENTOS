"""
tests/unit/test_orchestrator_management.py — Day 13 coverage for
core/orchestrator.py public methods not exercised by tests/test_day10.py
or tests/integration/test_pipeline.py: cancel_task, delete_task,
list_tasks, get_metrics_summary, cleanup_expired_tasks (TTL + overflow
eviction), and the resource-check failure path in execute_pipeline().

ASSUMPTIONS (please confirm against your real code):
- Agent.execute() contract confirmed from agents/base.py: returns
  {"status": "success"/"error", "output": ..., "metrics": {...}}.
- infrastructure.resource_monitor does not exist in this project (it is
  absent from every coverage report you've pasted so far, unlike every
  other infrastructure/*.py module which all show up). That means
  core.orchestrator.ResourceMonitor is None at import time, and
  Orchestrator._enable_resource_checks is always False regardless of the
  enable_resource_checks constructor arg (see orchestrator.py's
  `enable_resource_checks and ResourceMonitor is not None`). The two
  resource-check tests below patch core.orchestrator.ResourceMonitor
  directly and force `orchestrator._enable_resource_checks = True` after
  construction, since that's the only way to reach this branch without a
  real resource_monitor module. If infrastructure/resource_monitor.py
  gets added later, these tests should keep passing unchanged.
"""

from __future__ import annotations

import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from core.orchestrator import (
    Orchestrator,
    OrchestratorError,
    TaskNotFoundError,
    TaskStatus,
)


class FakeAgent:
    """Minimal stand-in satisfying Agent.execute()'s contract -- no LLM,
    no real agents/base.py dependency, just the dict shape orchestrator.py
    actually checks for."""

    def __init__(self, output: Any = "fake output") -> None:
        self.output = output

    def execute(self, input_data: str, **kwargs: Any) -> Dict[str, Any]:
        return {"status": "success", "output": self.output, "metrics": {}}


def _build_orchestrator(**kwargs: Any) -> Orchestrator:
    agents = {
        "summarizer": FakeAgent("a summary"),
        "extractor": FakeAgent(
            '{"key_points": [], "entities": {}, "sentiment": "neutral", "topics": []}'
        ),
        "critic": FakeAgent({"score": 8}),
    }
    kwargs.setdefault("enable_resource_checks", False)
    return Orchestrator(agents, logger=None, **kwargs)


@pytest.fixture()
def orchestrator():
    orch = _build_orchestrator()
    yield orch
    orch.shutdown()


# ---------------------------------------------------------------------------
# cancel_task
# ---------------------------------------------------------------------------
def test_cancel_pending_task_succeeds(orchestrator: Orchestrator) -> None:
    task_id = orchestrator.create_task("hello", task_type="summarize")
    assert orchestrator.cancel_task(task_id) is True
    task = orchestrator.get_task(task_id)
    assert task["status"] == TaskStatus.CANCELLED


def test_cancel_completed_task_returns_false_and_leaves_status_unchanged(
    orchestrator: Orchestrator,
) -> None:
    task_id = orchestrator.create_task("hello", task_type="summarize")
    orchestrator.execute_pipeline(task_id)
    assert orchestrator.cancel_task(task_id) is False
    task = orchestrator.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED


def test_cancel_unknown_task_raises(orchestrator: Orchestrator) -> None:
    with pytest.raises(TaskNotFoundError):
        orchestrator.cancel_task("does-not-exist")


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------
def test_delete_existing_task_returns_true_and_removes_it(orchestrator: Orchestrator) -> None:
    task_id = orchestrator.create_task("hello", task_type="summarize")
    assert orchestrator.delete_task(task_id) is True
    assert orchestrator.get_task(task_id) is None


def test_delete_unknown_task_returns_false(orchestrator: Orchestrator) -> None:
    assert orchestrator.delete_task("does-not-exist") is False


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------
def test_list_tasks_returns_all_by_default(orchestrator: Orchestrator) -> None:
    id1 = orchestrator.create_task("one", task_type="summarize")
    id2 = orchestrator.create_task("two", task_type="summarize")
    ids = {t["id"] for t in orchestrator.list_tasks()}
    assert {id1, id2} <= ids


def test_list_tasks_filters_by_status(orchestrator: Orchestrator) -> None:
    pending_id = orchestrator.create_task("stays pending", task_type="summarize")
    completed_id = orchestrator.create_task("gets completed", task_type="summarize")
    orchestrator.execute_pipeline(completed_id)

    pending_tasks = orchestrator.list_tasks(status=TaskStatus.PENDING)
    completed_tasks = orchestrator.list_tasks(status=TaskStatus.COMPLETED)

    assert {t["id"] for t in pending_tasks} == {pending_id}
    assert {t["id"] for t in completed_tasks} == {completed_id}


def test_list_tasks_returns_copies_not_live_references(orchestrator: Orchestrator) -> None:
    task_id = orchestrator.create_task("hello", task_type="summarize")
    listed = orchestrator.list_tasks()[0]
    listed["status"] = "tampered"
    # Mutating the returned dict must not affect internal state.
    assert orchestrator.get_task(task_id)["status"] == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# get_metrics_summary
# ---------------------------------------------------------------------------
def test_get_metrics_summary_counts_by_status(orchestrator: Orchestrator) -> None:
    pending_id = orchestrator.create_task("stays pending", task_type="summarize")
    completed_id = orchestrator.create_task("gets completed", task_type="summarize")
    orchestrator.execute_pipeline(completed_id)

    summary = orchestrator.get_metrics_summary()

    assert summary["total_tasks"] == 2
    assert summary["by_status"]["pending"] == 1
    assert summary["by_status"]["completed"] == 1
    # The counts above could pass even if these two tasks got mixed up
    # (e.g. both landed in "pending" and the completed one was never
    # actually run) -- confirming each id's own status directly is what
    # pending_id/completed_id are for, not just triggering a count.
    assert orchestrator.get_task(pending_id)["status"] == TaskStatus.PENDING
    assert orchestrator.get_task(completed_id)["status"] == TaskStatus.COMPLETED


def test_get_metrics_summary_empty_orchestrator() -> None:
    orch = _build_orchestrator()
    try:
        assert orch.get_metrics_summary() == {"total_tasks": 0, "by_status": {}}
    finally:
        orch.shutdown()


# ---------------------------------------------------------------------------
# cleanup_expired_tasks -- TTL eviction
# ---------------------------------------------------------------------------
def test_cleanup_evicts_terminal_tasks_past_ttl() -> None:
    orch = _build_orchestrator(task_ttl_seconds=0)  # anything terminal is instantly eligible
    try:
        task_id = orch.create_task("hello", task_type="summarize")
        orch.execute_pipeline(task_id)  # now COMPLETED
        time.sleep(0.01)  # ensure "now - updated_at" is strictly > 0

        evicted = orch.cleanup_expired_tasks()

        assert evicted == 1
        assert orch.get_task(task_id) is None
    finally:
        orch.shutdown()


def test_cleanup_does_not_evict_pending_tasks_even_past_ttl() -> None:
    orch = _build_orchestrator(task_ttl_seconds=0)
    try:
        task_id = orch.create_task("never executed", task_type="summarize")
        time.sleep(0.01)

        evicted = orch.cleanup_expired_tasks()

        assert evicted == 0
        assert orch.get_task(task_id) is not None
    finally:
        orch.shutdown()


# ---------------------------------------------------------------------------
# cleanup_expired_tasks -- overflow eviction (max_stored_tasks cap)
# ---------------------------------------------------------------------------
def test_cleanup_evicts_oldest_terminal_tasks_when_over_capacity() -> None:
    # Long TTL so eviction is driven purely by the size cap, not staleness.
    orch = _build_orchestrator(task_ttl_seconds=3600, max_stored_tasks=2)
    try:
        task_ids = []
        for i in range(3):
            tid = orch.create_task(f"task {i}", task_type="summarize")
            orch.execute_pipeline(tid)
            task_ids.append(tid)
            time.sleep(0.01)  # keep updated_at ordering deterministic

        evicted = orch.cleanup_expired_tasks()

        assert evicted == 1
        remaining = {t["id"] for t in orch.list_tasks()}
        assert len(remaining) == 2
        # The oldest task (created/completed first) should be the one evicted.
        assert task_ids[0] not in remaining
        assert task_ids[1] in remaining
        assert task_ids[2] in remaining
    finally:
        orch.shutdown()


# ---------------------------------------------------------------------------
# Resource-check failure path in execute_pipeline()
# See module docstring for why these patch core.orchestrator.ResourceMonitor
# and force the instance flag on directly.
# ---------------------------------------------------------------------------
def test_resource_check_failure_marks_task_failed_and_raises(orchestrator: Orchestrator) -> None:
    task_id = orchestrator.create_task("hello", task_type="summarize")

    fake_monitor = MagicMock()
    fake_monitor.check_resource_availability.return_value = False

    with patch("core.orchestrator.ResourceMonitor", fake_monitor):
        orchestrator._enable_resource_checks = True
        try:
            with pytest.raises(OrchestratorError):
                orchestrator.execute_pipeline(task_id)
        finally:
            orchestrator._enable_resource_checks = False

    task = orchestrator.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "insufficient_resources" in task["errors"]


def test_resource_check_raising_unexpected_exception_does_not_block_pipeline(
    orchestrator: Orchestrator,
) -> None:
    """If the resource monitor itself misbehaves (raises something other
    than OrchestratorError), execute_pipeline should log a warning and
    proceed anyway rather than failing the task outright."""
    task_id = orchestrator.create_task("hello", task_type="summarize")

    fake_monitor = MagicMock()
    fake_monitor.check_resource_availability.side_effect = RuntimeError("monitor broke")

    with patch("core.orchestrator.ResourceMonitor", fake_monitor):
        orchestrator._enable_resource_checks = True
        try:
            result = orchestrator.execute_pipeline(task_id)
        finally:
            orchestrator._enable_resource_checks = False

    assert result["summary"] == "a summary"
    task = orchestrator.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
