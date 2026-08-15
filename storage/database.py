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

from infrastructure.config import get_settings
from infrastructure.logging import logger
from infrastructure.security import SecurityManager
from storage.models import ApiKeyModel, Base, TaskModel


class Database:
    """Database abstraction layer."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self._security: Optional[SecurityManager] = None
        self._encryption_warned = False

    def _get_security(self) -> Optional[SecurityManager]:
        """Lazily build a SecurityManager from settings.FERNET_KEY.

        Returns None (rather than raising) when FERNET_KEY isn't
        configured -- FERNET_KEY is optional by design (config.py), so a
        deployment that hasn't set one yet degrades to storing input_text
        as plaintext instead of crashing on every task save. A warning is
        logged once, not per-call, so this doesn't spam logs on every
        single task.
        """
        if self._security is not None:
            return self._security

        fernet_key = get_settings().FERNET_KEY
        if not fernet_key:
            if not self._encryption_warned:
                logger.warning(
                    "FERNET_KEY not configured -- task input_text will be stored "
                    "in plaintext. Set FERNET_KEY to enable encryption at rest."
                )
                self._encryption_warned = True
            return None

        self._security = SecurityManager(encryption_key=fernet_key)
        return self._security

    def _encrypt_input_text(self, value: str) -> str:
        security = self._get_security()
        return security.encrypt_sensitive_data(value) if security is not None else value

    def _decrypt_input_text(self, value: str) -> str:
        security = self._get_security()
        if security is None:
            return value
        try:
            return security.decrypt_sensitive_data(value)
        except ValueError:
            # Value was written before FERNET_KEY was configured (plaintext
            # in the DB already) -- return as-is rather than raising, so
            # enabling encryption doesn't break reads of pre-existing rows.
            return value

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
        """Save a task execution record.

        input_text is encrypted before storage if FERNET_KEY is
        configured (see _get_security). task_data is not mutated --
        a shallow copy is made so callers holding a reference to the
        original dict never see the ciphertext.
        """
        task_data = dict(task_data)
        if "input_text" in task_data and task_data["input_text"] is not None:
            task_data["input_text"] = self._encrypt_input_text(task_data["input_text"])

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
        """Retrieve a task by ID. input_text is decrypted transparently
        before the detached object is returned, if FERNET_KEY is
        configured -- callers never see ciphertext."""
        with self.session() as session:
            task = session.query(TaskModel).filter(TaskModel.id == task_id).first()
            if task is not None:
                session.expunge(task)
                if task.input_text is not None:
                    task.input_text = self._decrypt_input_text(task.input_text)
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