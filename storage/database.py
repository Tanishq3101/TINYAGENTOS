# path: tinyagentos/storage/database.py

"""
Database abstraction layer.

The plan's original get_session()/get_task_by_id() opened a session and
never closed it — every call leaked a connection. Fixed here with a
context-manager session (`with db.session() as session:`), which closes
(and rolls back on error) automatically.
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from storage.models import ApiKeyModel, Base, TaskModel


class Database:
    """Database abstraction layer."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

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

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------
    # Only ever stores/looks up a hash (SecurityManager.hash_api_key output).
    # Callers must never pass a raw key to any of these methods.

    def create_api_key(self, key_hash: str, label: Optional[str] = None) -> ApiKeyModel:
        """Persist a new API key record. Caller is responsible for hashing
        the raw key (SecurityManager.hash_api_key) before calling this --
        only the hash is ever stored, matching TaskModel's pattern of
        never persisting anything sensitive it doesn't have to."""
        with self.session() as session:
            api_key = ApiKeyModel(key_hash=key_hash, label=label)
            session.add(api_key)
            session.flush()
            session.refresh(api_key)
            session.expunge(api_key)
            return api_key

    def get_api_key_by_hash(self, key_hash: str) -> Optional[ApiKeyModel]:
        """Look up an API key record by its hash.

        Hashing here is a deterministic SHA-256 (not a salted/slow hash
        like bcrypt), so an equality lookup on the hash column is the
        standard, safe pattern for hashed-token auth -- the same approach
        GitHub/Stripe-style API tokens use. This lookup only narrows to
        the candidate row; the actual accept/reject decision still goes
        through SecurityManager.verify_api_key()'s constant-time compare
        in api/dependencies.py, so a mismatched candidate never gets
        accepted just because *some* row's hash happened to match a
        substring or similar artifact of the lookup itself.
        """
        with self.session() as session:
            api_key = session.query(ApiKeyModel).filter(ApiKeyModel.key_hash == key_hash).first()
            if api_key is not None:
                session.expunge(api_key)
            return api_key

    def revoke_api_key(self, api_key_id: str) -> bool:
        """Mark an API key as revoked. Returns True if a matching row was
        found and updated, False if no key with that id exists."""
        with self.session() as session:
            updated = (
                session.query(ApiKeyModel)
                .filter(ApiKeyModel.id == api_key_id)
                .update({"revoked": True})
            )
            return bool(updated)

    def touch_api_key_last_used(self, api_key_id: str) -> None:
        """Best-effort update of last_used_at on successful auth. Callers
        should treat failures here as non-fatal -- auth has already
        succeeded by the time this is called, and a metrics-style update
        failing should never turn a successful request into a failed one."""
        with self.session() as session:
            session.query(ApiKeyModel).filter(ApiKeyModel.id == api_key_id).update(
                {"last_used_at": datetime.utcnow()}
            )

    def list_api_keys(self) -> List[ApiKeyModel]:
        """Return all API key records (metadata only -- key_hash is present
        but the raw key never is, since it's never stored anywhere)."""
        with self.session() as session:
            keys = session.query(ApiKeyModel).all()
            for key in keys:
                session.expunge(key)
            return keys
