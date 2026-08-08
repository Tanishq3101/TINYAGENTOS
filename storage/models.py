# path: tinyagentos/storage/models.py

"""
SQLAlchemy models for task execution, per-agent execution records, and
pipeline outputs.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Float, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


class TaskStatus(str, enum.Enum):
    """Lifecycle states for a task. Defined here because models.py is the
    first place that needs it — the original plan referenced this enum
    without ever defining it, which would raise ImportError at runtime."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskModel(Base):
    """Store task execution records."""

    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=_uuid, index=True)
    input_text = Column(Text, nullable=False)
    task_type = Column(String, nullable=False)
    # validate_strings=True: rejects an invalid raw string at the Python/ORM
    # level (e.g. status="bad-enum-value") instead of silently storing it.
    # create_constraint=True: also emits a DB-level CHECK constraint, since
    # SQLite has no native ENUM type and otherwise stores this as a bare
    # VARCHAR with no enforcement at all — belt and suspenders.
    status = Column(
        Enum(TaskStatus, validate_strings=True, create_constraint=True),
        nullable=False,
        default=TaskStatus.PENDING,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    execution_time_ms = Column(Float, nullable=True)


class AgentExecutionModel(Base):
    """Store individual agent execution details."""

    __tablename__ = "agent_executions"

    id = Column(String, primary_key=True, default=_uuid, index=True)
    task_id = Column(String, index=True, nullable=False)
    agent_name = Column(String, nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    end_time = Column(DateTime, nullable=True)
    execution_time_ms = Column(Float, nullable=True)
    status = Column(String, nullable=False)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


class OutputModel(Base):
    """Store final pipeline outputs."""

    __tablename__ = "outputs"

    id = Column(String, primary_key=True, default=_uuid, index=True)
    task_id = Column(String, index=True, nullable=False)
    summary = Column(Text, nullable=True)
    extracted_info = Column(Text, nullable=True)  # JSON string
    critic_score = Column(Float, nullable=True)
    critic_feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)