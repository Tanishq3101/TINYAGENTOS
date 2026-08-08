# api/schemas.py

"""
Request/response schemas for the TinyAgentOS API.

Unlike app.py/routes.py/middleware.py, this file has no dependency on
infrastructure.logging or infrastructure.config internals, so the plan's
original sketch needed no real adaptation here — just review for shape
correctness against your actual Orchestrator.tasks / results dict.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    """Task creation request body."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="Input text for processing",
    )
    task_type: str = Field(
        default="full_pipeline",
        description="One of: full_pipeline, summarize, extract, evaluate",
    )
    priority: int = Field(default=1, ge=1, le=10)


class TaskResponse(BaseModel):
    """Task status/result response."""

    task_id: str
    status: str
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None


class ExecutionResult(BaseModel):
    """Shape of a completed full_pipeline execution's results dict.

    ASSUMPTION (unverified against the real orchestrator.py): field names
    match what test_pipeline.py asserts on — results["summary"],
    results["extraction"] (dict), results["evaluation"] (dict). No test
    observed so far exposes a total execution_time_ms in the results dict
    itself (AgentMetrics has per-agent execution_time_ms, but that's a
    metrics.py concept, not necessarily surfaced here) — made Optional
    rather than assumed-required. Please confirm/correct against the real
    orchestrator once you diff this.
    """

    summary: Optional[str] = None
    extraction: Optional[Dict[str, Any]] = None
    evaluation: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[float] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    app_name: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Standard error body returned by the exception handlers in app.py."""

    error: str
    detail: str
