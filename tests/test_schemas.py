"""
tests/test_day11_schemas.py — tests for api/schemas.py.

ASSUMPTIONS (please confirm):
- Works against either pydantic v1 or v2 — deliberately avoids
  version-specific APIs (.dict() vs .model_dump(), etc.) and only checks
  plain attribute values plus `pydantic.ValidationError`, which is the
  stable cross-version error type for both.
- TaskRequest.task_type has no enum/Literal constraint in the schema
  itself (it's a plain `str` with a description, per the schemas.py you
  pasted) — actual task_type validity is enforced later by
  Orchestrator.create_task() via SUPPORTED_TASK_TYPES, not at the
  pydantic layer. So `TaskRequest(text="x", task_type="anything")`
  is expected to construct successfully here; that's intentional, not a
  gap in these tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.schemas import (
    ErrorResponse,
    ExecutionResult,
    HealthResponse,
    TaskRequest,
    TaskResponse,
)


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
    # See module docstring -- validity of task_type is enforced later by
    # Orchestrator.create_task(), not at this schema layer.
    req = TaskRequest(text="hello", task_type="not_a_real_type")
    assert req.task_type == "not_a_real_type"


# ---------------------------------------------------------------------------
# TaskResponse
# ---------------------------------------------------------------------------
def test_task_response_minimal_valid() -> None:
    resp = TaskResponse(task_id="abc-123", status="pending")
    assert resp.task_id == "abc-123"
    assert resp.status == "pending"
    assert resp.created_at is None
    assert resp.completed_at is None
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
        created_at=now,
        completed_at=now,
        results={"summary": "s"},
        errors=[],
    )
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


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------
def test_health_response_valid() -> None:
    now = datetime.now(timezone.utc)
    health = HealthResponse(status="healthy", app_name="TinyAgentOS", timestamp=now)
    assert health.status == "healthy"
    assert health.app_name == "TinyAgentOS"
    assert health.timestamp == now


def test_health_response_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HealthResponse(status="healthy", app_name="TinyAgentOS")  # missing timestamp


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------
def test_error_response_valid() -> None:
    err = ErrorResponse(error="task_not_found", detail="Task 'x' not found")
    assert err.error == "task_not_found"
    assert err.detail == "Task 'x' not found"


def test_error_response_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(error="task_not_found")  # missing detail