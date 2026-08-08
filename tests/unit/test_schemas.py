"""
tests/test_schemas.py — tests for api/schemas.py.

Adapted to match the ACTUAL api/schemas.py you're running, which defines
only three models: TaskRequest, TaskResponse, ExecutionResult.

Removed vs. the original draft:
- ErrorResponse, HealthResponse — these classes don't exist in
  api/schemas.py, and nothing in api/routes.py or api/app.py constructs
  error/health responses via a Pydantic model. Errors go through
  FastAPI's default HTTPException(detail=...) body shape, and
  health_check() returns a plain dict {"status", "timestamp"} with no
  app_name field. If you later introduce these models and wire routes.py
  to use them, tell me and I'll add matching tests back.
- TaskResponse.completed_at — not a field on your actual TaskResponse
  (only created_at exists, plus message/results/errors). Removed the
  assertion on it.

Works against either pydantic v1 or v2 — deliberately avoids
version-specific APIs (.dict() vs .model_dump(), etc.) and only checks
plain attribute values plus `pydantic.ValidationError`, which is the
stable cross-version error type for both.

TaskRequest.task_type has no enum/Literal constraint in the schema itself
(it's a plain `str`) — actual task_type validity is presumably enforced
later by Orchestrator.create_task(), not at the pydantic layer. So
`TaskRequest(text="x", task_type="anything")` is expected to construct
successfully; that's intentional, not a gap in these tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.schemas import ExecutionResult, TaskRequest, TaskResponse


# ---------------------------------------------------------------------------
# TaskRequest
# ---------------------------------------------------------------------------
def test_task_request_minimal_valid() -> None:
    req = TaskRequest(text="hello world")
    assert req.text == "hello world"
    assert req.task_type == "full_pipeline"  # default
    assert req.priority == 1  # default


def test_task_request_all_fields() -> None:
    req = TaskRequest(text="hello", task_type="summarize", priority=5)
    assert req.task_type == "summarize"
    assert req.priority == 5


def test_task_request_empty_text_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(text="")


def test_task_request_missing_text_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskRequest()


def test_task_request_text_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(text="a" * 100_001)


def test_task_request_text_at_max_length_allowed() -> None:
    req = TaskRequest(text="a" * 100_000)
    assert len(req.text) == 100_000


@pytest.mark.parametrize("priority", [1, 5, 10])
def test_task_request_priority_within_bounds_allowed(priority: int) -> None:
    req = TaskRequest(text="hello", priority=priority)
    assert req.priority == priority


@pytest.mark.parametrize("priority", [0, -1, 11, 100])
def test_task_request_priority_out_of_bounds_rejected(priority: int) -> None:
    with pytest.raises(ValidationError):
        TaskRequest(text="hello", priority=priority)


def test_task_request_task_type_is_unconstrained_string() -> None:
    # See module docstring -- validity of task_type is enforced later,
    # not at this schema layer.
    req = TaskRequest(text="hello", task_type="not_a_real_type")
    assert req.task_type == "not_a_real_type"


# ---------------------------------------------------------------------------
# TaskResponse
# ---------------------------------------------------------------------------
def test_task_response_minimal_valid() -> None:
    resp = TaskResponse(task_id="abc-123", status="pending")
    assert resp.task_id == "abc-123"
    assert resp.status == "pending"
    assert resp.message is None
    assert resp.created_at is None
    assert resp.results is None
    assert resp.errors is None


def test_task_response_missing_required_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskResponse(status="pending")  # missing task_id
    with pytest.raises(ValidationError):
        TaskResponse(task_id="abc-123")  # missing status


def test_task_response_full_valid() -> None:
    now = datetime.now(timezone.utc)
    resp = TaskResponse(
        task_id="abc-123",
        status="completed",
        message="Task created successfully",
        created_at=now,
        results={"summary": "s"},
        errors=[],
    )
    assert resp.message == "Task created successfully"
    assert resp.results == {"summary": "s"}
    assert resp.errors == []


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------
def test_execution_result_all_fields_optional() -> None:
    result = ExecutionResult()
    assert result.summary is None
    assert result.extraction is None
    assert result.evaluation is None
    assert result.execution_time_ms is None


def test_execution_result_full_valid() -> None:
    result = ExecutionResult(
        summary="a summary",
        extraction={"key_points": []},
        evaluation={"score": 9},
        execution_time_ms=123.4,
    )
    assert result.summary == "a summary"
    assert result.evaluation["score"] == 9
    assert result.execution_time_ms == 123.4
