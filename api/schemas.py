# api/schemas.py

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

# Use correct Pydantic v2 syntax


class TaskRequest(BaseModel):
    """Task creation request."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=100000,
    )
    task_type: str = Field(default="full_pipeline")
    priority: int = Field(default=1, ge=1, le=10)

    model_config = {
        "json_schema_extra": {
            "example": {"text": "What is 45 times 12?", "task_type": "full_pipeline", "priority": 5}
        }
    }


class TaskResponse(BaseModel):
    """Task response."""

    task_id: str
    status: str
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    results: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None


class ExecutionResult(BaseModel):
    """Result of a task execution."""

    summary: Optional[str] = None
    extraction: Optional[Dict[str, Any]] = None
    evaluation: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[float] = None
