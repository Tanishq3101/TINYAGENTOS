"""
Input validation schemas for anything crossing the API boundary.

Every field the outside world can send us gets validated and sanitized here —
this is the layer that stops oversized payloads, null-byte injection, and
invalid enum values from ever reaching agent/orchestrator code.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class TaskType(str, Enum):
    FULL_PIPELINE = "full_pipeline"
    SUMMARIZE = "summarize"
    EXTRACT = "extract"
    EVALUATE = "evaluate"


class TaskInput(BaseModel):
    """Validated task input schema — the only shape allowed into the orchestrator."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Input text for processing",
    )
    task_type: TaskType = Field(
        default=TaskType.FULL_PIPELINE,
        description="Type of task to run",
    )
    priority: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Task priority (1-10)",
    )

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        """Strip null bytes and collapse whitespace before anything downstream sees it."""
        v = v.replace("\x00", "")
        v = " ".join(v.split())
        if not v:
            raise ValueError("text cannot be empty after sanitization")
        return v


class APIKeyInput(BaseModel):
    """Validated shape for API key creation requests."""

    label: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("label")
    @classmethod
    def sanitize_label(cls, v: str) -> str:
        v = v.replace("\x00", "").strip()
        if not v:
            raise ValueError("label cannot be empty")
        return v


class PaginationParams(BaseModel):
    """Shared pagination validation — bounds page size so nobody can request
    a million rows in one call."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
