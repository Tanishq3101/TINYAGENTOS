"""
tests/test_day11.py — API layer tests for Day 11 (api/app.py, api/routes.py).

Adapted to match the ACTUAL api/routes.py and api/schemas.py you're running
(not the original 30-day-plan draft). Key differences baked in here:

- No custom exception types (InvalidTaskInputError / TaskNotFoundError /
  TaskAlreadyRunningError). Your execute_task route catches plain
  ValueError and string-matches "not found" / "already running" in the
  message, falling back to 400 for any other ValueError message.
- Error responses use FastAPI's default body shape: {"detail": "..."}.
  NOT {"error": "..."}.
- POST /tasks returns 200 (no explicit status_code=201 on the route).
- create_task() failures are caught by a blanket `except Exception` and
  turned into a 500 "Failed to create task" — there's no 422 path for
  orchestrator-side validation errors, only for Pydantic-level validation
  (e.g. empty text).
- get_task_status() calls orchestrator.get_task(task_id), which is
  expected to return a dict or None (not a raise-on-missing lookup).
- health_check() returns only {"status", "timestamp"} — no "app_name".
- get_orchestrator() returns a module-level singleton via
  `from core.orchestrator import orchestrator`; we override the
  get_orchestrator dependency callable itself, so the real singleton
  and real core.orchestrator.Orchestrator class never need to load.

REMAINING ASSUMPTIONS (please confirm / tell me if wrong):
- core.orchestrator.Orchestrator is a real importable class (used only
  as a type hint in routes.py) that doesn't blow up on import.
- api/app.py still wires in TrustedHostMiddleware allowing only
  localhost/127.0.0.1 — kept the base_url="http://localhost" workaround
  from before. If you've since changed/removed that middleware, this is
  harmless either way.
- infrastructure.config.get_settings() exists and returns an object with
  .REQUIRE_AUTH — used only in the last, informational test.
- orchestrator.get_task(task_id) returns a plain dict (or None), with at
  least a "status" key that's already a string like "completed".

Run with:
    pytest tests/test_day11.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.routes import get_orchestrator, verify_api_key


# ---------------------------------------------------------------------------
# Fake orchestrator — mimics the subset of the real Orchestrator's public
# surface that routes.py actually touches:
#   .create_task(text=, task_type=, priority=) -> task_id: str
#   .execute_pipeline(task_id) -> dict           (raises ValueError)
#   .get_task(task_id) -> dict | None
# ---------------------------------------------------------------------------
class FakeOrchestrator:
    def __init__(self) -> None:
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self._raise_on_create: Optional[Exception] = None
        self._raise_on_execute: Optional[Exception] = None

    # -- test control hooks --------------------------------------------
    def script_create_error(self, exc: Exception) -> None:
        self._raise_on_create = exc

    def script_execute_error(self, exc: Exception) -> None:
        self._raise_on_execute = exc

    def seed_task(self, task_id: str, **overrides: Any) -> None:
        base = {
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "results": {},
            "errors": [],
        }
        base.update(overrides)
        self.tasks[task_id] = base

    # -- real-orchestrator-shaped surface --------------------------------
    def create_task(self, text: str, task_type: str = "full_pipeline", priority: int = 1) -> str:
        if self._raise_on_create is not None:
            raise self._raise_on_create
        task_id = f"fake-{len(self.tasks) + 1}"
        self.seed_task(task_id, status="pending")
        return task_id

    def execute_pipeline(self, task_id: str) -> Dict[str, Any]:
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        if task_id not in self.tasks:
            raise ValueError(f"Task '{task_id}' not found")
        task = self.tasks[task_id]
        if task["status"] == "running":
            raise ValueError(f"Task '{task_id}' is already running")
        results = {"summary": "fake summary", "extraction": {}, "evaluation": {}}
        task["status"] = "completed"
        task["results"] = results
        return results

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)


@pytest.fixture()
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture()
def client(fake_orchestrator: FakeOrchestrator):
    """TestClient with orchestrator/auth dependencies overridden.

    base_url="http://localhost" matters if app.py still has
    TrustedHostMiddleware(allowed_hosts=["localhost", "127.0.0.1"]) —
    TestClient's default base_url (http://testserver) would otherwise get
    a flat 400 before any route/dependency runs.
    """
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    with TestClient(app, base_url="http://localhost") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health check — unauthenticated, no orchestrator dependency
# ---------------------------------------------------------------------------
def test_health_check_returns_200(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "timestamp" in body


# ---------------------------------------------------------------------------
# POST /tasks — create_task
# ---------------------------------------------------------------------------
def test_create_task_success(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks", json={"text": "hello world", "task_type": "summarize"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert body["task_id"]
    assert body["message"] == "Task created successfully"


def test_create_task_missing_text_returns_422_from_pydantic(client: TestClient) -> None:
    # min_length=1 on TaskRequest.text — Pydantic validation runs before
    # the route body (and before auth, since it's a path-operation param).
    resp = client.post("/api/v1/tasks", json={"text": ""})
    assert resp.status_code == 422


def test_create_task_orchestrator_error_returns_500(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    # routes.py wraps orchestrator.create_task in a blanket except Exception
    # -> 500 "Failed to create task", regardless of what was raised.
    fake_orchestrator.script_create_error(RuntimeError("boom"))
    resp = client.post("/api/v1/tasks", json={"text": "hello", "task_type": "bogus"})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Failed to create task"


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/execute
# ---------------------------------------------------------------------------
def test_execute_task_success(client: TestClient, fake_orchestrator: FakeOrchestrator) -> None:
    fake_orchestrator.seed_task("t1", status="pending")
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["task_id"] == "t1"
    assert body["result"]["summary"] == "fake summary"


def test_execute_task_not_found_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks/does-not-exist/execute")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_execute_task_already_running_returns_409(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.seed_task("t1", status="running")
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


def test_execute_task_other_valueerror_returns_400(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.script_execute_error(ValueError("something else went wrong"))
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "something else went wrong"


def test_execute_task_unexpected_exception_returns_500(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.script_execute_error(RuntimeError("boom"))
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Task execution failed"


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}
# ---------------------------------------------------------------------------
def test_get_task_status_success(client: TestClient, fake_orchestrator: FakeOrchestrator) -> None:
    fake_orchestrator.seed_task("t1", status="completed", results={"summary": "s"}, errors=[])
    resp = client.get("/api/v1/tasks/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["results"] == {"summary": "s"}


def test_get_task_status_not_found_returns_404(client: TestClient) -> None:
    # orchestrator.get_task() returns None for a missing id -> explicit
    # 404 raised in the route itself, not a KeyError/exception path.
    resp = client.get("/api/v1/tasks/does-not-exist")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_get_task_status_unexpected_exception_returns_500(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    def boom(task_id: str):
        raise RuntimeError("db down")

    fake_orchestrator.get_task = boom  # type: ignore[method-assign]
    resp = client.get("/api/v1/tasks/t1")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Failed to get task status"


# ---------------------------------------------------------------------------
# Auth dependency — real verify_api_key, NOT overridden.
# Outcome depends on your actual settings.REQUIRE_AUTH, so this is
# informational rather than a strict gate.
# ---------------------------------------------------------------------------
def test_real_auth_dependency_rejects_missing_key_when_required(
    fake_orchestrator: FakeOrchestrator,
) -> None:
    from infrastructure.config import get_settings

    settings = get_settings()
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    # verify_api_key intentionally NOT overridden here.
    try:
        with TestClient(app, base_url="http://localhost") as c:
            resp = c.post("/api/v1/tasks", json={"text": "hello"})
            if settings.REQUIRE_AUTH:
                assert resp.status_code == 401
            else:
                assert resp.status_code != 401
    finally:
        app.dependency_overrides.clear()
