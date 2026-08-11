# tests/unit/test_dependencies_failure_modes.py

"""
Coverage for the infrastructure-failure branches in api/dependencies.py's
verify_api_key() that the rest of the suite doesn't reach: a real DB
outage (-> 503, not 401) and a failure specifically in the best-effort
`touch_api_key_last_used` call (-> must not fail an otherwise-successful
auth).

These are deliberately unit-level rather than e2e. The e2e/integration
suites exercise the real SQLite-backed Database, so simulating "the
database is down" there would mean actually breaking the real store
mid-run -- slow, flaky, and disruptive to whatever else is using that
file. Here we replace get_database() and SecurityManager's methods with
doubles instead, and call verify_api_key() directly as a plain function
(it's callable outside of FastAPI's dependency-injection machinery --
the Header(None) default is just a default value when called this way).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import api.dependencies as deps


def _settings(require_auth: bool = True) -> SimpleNamespace:
    return SimpleNamespace(REQUIRE_AUTH=require_auth)


class TestDatabaseUnavailable:
    """verify_api_key() must surface a DB failure as 503, not 401 -- an
    outage should never be reported to a caller as 'your credentials are
    wrong,' per the function's own docstring."""

    def test_get_database_raising_returns_503_not_401(self, monkeypatch):
        monkeypatch.setattr(deps, "get_settings", lambda: _settings())
        monkeypatch.setattr(deps, "get_database", MagicMock(side_effect=RuntimeError("db down")))

        with pytest.raises(HTTPException) as exc_info:
            deps.verify_api_key(x_api_key="sk-doesnt-matter")

        assert exc_info.value.status_code == 503
        assert "unavailable" in exc_info.value.detail.lower()

    def test_query_raising_after_db_connects_also_returns_503(self, monkeypatch):
        """Same contract, but the failure happens one call deeper --
        get_database() itself succeeds; the lookup on it doesn't (e.g.
        connection dropped mid-query rather than at connect time)."""
        monkeypatch.setattr(deps, "get_settings", lambda: _settings())

        broken_db = MagicMock()
        broken_db.get_api_key_by_hash.side_effect = RuntimeError("connection reset")
        monkeypatch.setattr(deps, "get_database", lambda: broken_db)
        monkeypatch.setattr(deps.SecurityManager, "hash_api_key", staticmethod(lambda k: "hashed"))

        with pytest.raises(HTTPException) as exc_info:
            deps.verify_api_key(x_api_key="sk-doesnt-matter")

        assert exc_info.value.status_code == 503

    def test_503_does_not_fire_when_auth_is_disabled(self, monkeypatch):
        """REQUIRE_AUTH=False must short-circuit before the DB is ever
        touched -- a broken DB shouldn't matter if auth checking is off."""
        monkeypatch.setattr(deps, "get_settings", lambda: _settings(require_auth=False))
        monkeypatch.setattr(deps, "get_database", MagicMock(side_effect=RuntimeError("db down")))

        result = deps.verify_api_key(x_api_key=None)

        assert result == "no-auth"


class TestLastUsedTouchFailure:
    """touch_api_key_last_used() is explicitly documented as best-effort:
    a failure there must never turn an already-successful auth into a
    failed request."""

    @staticmethod
    def _valid_key_row(revoked: bool = False) -> SimpleNamespace:
        return SimpleNamespace(id="key-123", key_hash="irrelevant", revoked=revoked)

    def _patch_happy_path(self, monkeypatch, db):
        monkeypatch.setattr(deps, "get_settings", lambda: _settings())
        monkeypatch.setattr(deps, "get_database", lambda: db)
        monkeypatch.setattr(deps.SecurityManager, "hash_api_key", staticmethod(lambda k: "hashed"))
        monkeypatch.setattr(
            deps.SecurityManager, "verify_api_key", staticmethod(lambda raw, hashed: True)
        )

    def test_touch_failure_does_not_fail_an_otherwise_valid_key(self, monkeypatch):
        db = MagicMock()
        db.get_api_key_by_hash.return_value = self._valid_key_row()
        db.touch_api_key_last_used.side_effect = RuntimeError("write failed")
        self._patch_happy_path(monkeypatch, db)

        result = deps.verify_api_key(x_api_key="sk-real-key")

        assert result == "key-123"
        db.touch_api_key_last_used.assert_called_once_with("key-123")

    def test_touch_success_path_still_returns_key_id(self, monkeypatch):
        """Control case alongside the failure test above, confirming the
        happy path itself still works with these doubles in place."""
        db = MagicMock()
        db.get_api_key_by_hash.return_value = self._valid_key_row()
        self._patch_happy_path(monkeypatch, db)

        result = deps.verify_api_key(x_api_key="sk-real-key")

        assert result == "key-123"
        db.touch_api_key_last_used.assert_called_once_with("key-123")

    def test_revoked_key_is_still_rejected_even_if_touch_would_have_failed(self, monkeypatch):
        """Revocation must be checked -- and enforced -- before the
        best-effort touch ever runs; a broken touch path must not become
        a way to bypass a revoked key's 401."""
        db = MagicMock()
        db.get_api_key_by_hash.return_value = self._valid_key_row(revoked=True)
        db.touch_api_key_last_used.side_effect = RuntimeError("should never be called")
        self._patch_happy_path(monkeypatch, db)

        with pytest.raises(HTTPException) as exc_info:
            deps.verify_api_key(x_api_key="sk-revoked-key")

        assert exc_info.value.status_code == 401
        db.touch_api_key_last_used.assert_not_called()
