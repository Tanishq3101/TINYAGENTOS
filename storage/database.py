# path: tinyagentos/storage/database.py

"""
Database abstraction layer.

The plan's original get_session()/get_task_by_id() opened a session and
never closed it — every call leaked a connection. Fixed here with a
context-manager session (`with db.session() as session:`), which closes
(and rolls back on error) automatically.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from storage.models import Base, TaskModel


class Database:
    """Database abstraction layer."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

    def init_db(self) -> None:
        """Create all tables. Safe to call repeatedly — no-op if they exist."""
        Base.metadata.create_all(bind=self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Context-managed session — commits on success, rolls back and
        re-raises on error, always closes.

        Usage:
            with db.session() as session:
                session.add(some_model)
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def save_task_execution(self, task_data: dict) -> TaskModel:
        """Save a task execution record."""
        with self.session() as session:
            task = TaskModel(**task_data)
            session.add(task)
            session.flush()  # populate generated fields (id, defaults) before commit
            session.refresh(task)
            # Detach a plain copy of the values the caller needs — the ORM
            # object itself becomes unusable once the session closes.
            session.expunge(task)
            return task

    def get_task_by_id(self, task_id: str) -> Optional[TaskModel]:
        """Retrieve a task by ID."""
        with self.session() as session:
            task = session.query(TaskModel).filter(TaskModel.id == task_id).first()
            if task is not None:
                session.expunge(task)
            return task