# path: tinyagentos/storage/models.py

"""
SQLAlchemy models for task execution, per-agent execution records, and
pipeline outputs.

MYPY FIX (was: "Variable ... Base is not valid as a type" / "Invalid base
class" on every model, plus a "Need type annotation" on
AgentExecutionModel.status):

declarative_base() returns a class built dynamically at runtime, so
without the (unconfigured) sqlalchemy2-stubs mypy plugin, mypy sees its
return value as untyped/Any and can't validate `class Foo(Base):` as a
real inheritance relationship -- hence "invalid base class" on every
single model. Switching to SQLAlchemy 2.0's typed declarative style
(DeclarativeBase + Mapped/mapped_column) gives mypy an actual class to
check against, and Mapped[str] etc. give the type checker (and every
caller) the correct *instance*-level type.

This also fixes the three api/dependencies.py "Column[str]" errors:
under the old Column(...) style, `api_key_row.key_hash` typed as
Column[str] even on an instance, which is what tripped
verify_api_key()/touch_api_key_last_used()'s str-typed arguments/return.
Mapped[str] resolves to plain `str` on an instance, matching what those
functions actually receive at runtime -- no changes needed in
dependencies.py itself.

Runtime behavior (table names, columns, defaults, nullability, the
Enum CHECK constraint) is unchanged -- this is a typing-only migration.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Typed declarative base. A real class (not a declarative_base()
    call result) so mypy can check inheritance and Mapped[...] column
    types against it."""


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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    # validate_strings=True: rejects an invalid raw string at the Python/ORM
    # level (e.g. status="bad-enum-value") instead of silently storing it.
    # create_constraint=True: also emits a DB-level CHECK constraint, since
    # SQLite has no native ENUM type and otherwise stores this as a bare
    # VARCHAR with no enforcement at all — belt and suspenders.
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, validate_strings=True, create_constraint=True),
        nullable=False,
        default=TaskStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_time_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AgentExecutionModel(Base):
    """Store individual agent execution details."""

    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid, index=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_time_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Explicitly annotated (this is the "Need type annotation for status"
    # error's origin) -- a plain String column, distinct from TaskModel's
    # TaskStatus enum column above.
    status: Mapped[str] = mapped_column(String, nullable=False)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ApiKeyModel(Base):
    """Store issued API keys.

    Only the SHA-256 hash of a key is ever persisted here
    (SecurityManager.hash_api_key) -- the raw key exists only at issuance
    time (see scripts/manage_api_keys.py) and is never written to this
    table or logged. This replaces the previous "any string starting
    with sk-" format check in api/dependencies.py.verify_api_key() with a
    real, revocable, per-caller credential.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid, index=True)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OutputModel(Base):
    """Store final pipeline outputs."""

    __tablename__ = "outputs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid, index=True)
    task_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    critic_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    critic_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
