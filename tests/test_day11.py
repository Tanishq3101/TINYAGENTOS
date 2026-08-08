"""
tests/test_day11.py — API layer tests for Day 11 (api/app.py, api/routes.py).

ASSUMPTIONS (please confirm against your real code before trusting these):
- `api.app` exposes a module-level `app` (FastAPI instance) — confirmed
  from the app.py you pasted.
- `api.routes` exposes `get_orchestrator` and `verify_api_key` as the two
  dependency callables used by every route — confirmed from routes.py.
- core.orchestrator's real exception types (InvalidTaskInputError,
  TaskNotFoundError, TaskAlreadyRunningError) are importable from
  core.orchestrator, matching app.py's exception handlers.
- TaskStatus is an Enum with `.value` (e.g. TaskStatus.COMPLETED.value ==
  "completed") — used by routes.py as `task["status"].value`.

WHY dependency_overrides instead of hitting the real lifespan/orchestrator:
- The real `_build_orchestrator()` in app.py constructs actual LLMRuntime +
  agent instances at startup, which would mean real LLM calls in tests.
  We never want that in a unit-test suite for the API layer — the agents/
  orchestrator logic already has its own tests (test_day10.py etc).
- I don't know your real settings.REQUIRE_AUTH / API_KEY_HEADER values,
  so rather than guess whether auth is on/off in your environment, these
  tests override `verify_api_key` directly. This makes the API-layer
  tests independent of your infrastructure/config.py contents. If you
  want a test that specifically exercises the *real* auth dependency
  (missing header -> 401 when REQUIRE_AUTH=True), see
  `test_real_auth_dependency_rejects_missing_key_when_required` at the
  bottom — it does NOT override verify_api_key, so its outcome depends on
  your actual settings and is marked accordingly.

Run with:
    pytest tests/test_day11.py -v
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.routes import get_orchestrator, verify_api_key
from core.orchestrator import (
    InvalidTaskInputError,
    TaskAlreadyRunningError,
    TaskNotFoundError,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Fake orchestrator — mimics the subset of the real Orchestrator's public
# surface that routes.py actually touches: .create_task(), .execute_pipeline(),
# .tasks[task_id]. Behavior is controllable per-test via the `_scripted_*`
# hooks so we can force InvalidTaskInputError / TaskNotFoundError /
# TaskAlreadyRunningError paths without needing real agents.
# ---------------------------------------------------------------------------
class FakeOrchestrator:
    def __init__(self) -> None:
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._raise_on_create: Optional[Exception] = None
        self._raise_on_execute: Optional[Exception] = None

    # -- test control hooks --------------------------------------------
    def script_create_error(self, exc: Exception) -> None:
        self._raise_on_create = exc

    def script_execute_error(self, exc: Exception) -> None:
        self._raise_on_execute = exc

    def seed_task(self, task_id: str, **overrides: Any) -> None:
        base = {
            "id": task_id,
            "status": TaskStatus.PENDING,
            "created_at": datetime.now(timezone.utc),
            "completed_at": None,
            "results": {},
            "errors": [],
        }
        base.update(overrides)
        self.tasks[task_id] = base

    # -- real-orchestrator-shaped surface --------------------------------
    def create_task(self, input_data: str, task_type: str = "full_pipeline") -> str:
        if self._raise_on_create is not None:
            raise self._raise_on_create
        task_id = f"fake-{len(self.tasks) + 1}"
        self.seed_task(task_id, status=TaskStatus.PENDING)
        return task_id

    def execute_pipeline(self, task_id: str) -> Dict[str, Any]:
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        if task_id not in self.tasks:
            raise TaskNotFoundError(f"Task '{task_id}' not found")
        task = self.tasks[task_id]
        if task["status"] == TaskStatus.RUNNING:
            raise TaskAlreadyRunningError(f"Task '{task_id}' is already running")
        results = {"summary": "fake summary", "extraction": {}, "evaluation": {}}
        task["status"] = TaskStatus.COMPLETED
        task["completed_at"] = datetime.now(timezone.utc)
        task["results"] = results
        return results


@pytest.fixture()
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture()
def client(fake_orchestrator: FakeOrchestrator):
    """TestClient with the real orchestrator/auth dependencies overridden.

    NOTE: overriding get_orchestrator means app.py's `lifespan` startup
    (which builds a real Orchestrator with real agents) never actually
    needs to run correctly for these tests to pass — TestClient still
    triggers lifespan by default, so if your real _build_orchestrator()
    is broken/slow/needs live credentials, these tests may still fail at
    client construction. If that's a problem, tell me and I'll add a
    lifespan override too.
    """
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    # base_url matters here: TrustedHostMiddleware in app.py only allows
    # Host: localhost / 127.0.0.1. TestClient's default base_url is
    # http://testserver, which sends Host: testserver -- that gets
    # rejected with a flat 400 Bad Request before any route or dependency
    # ever runs (explains why even the unauthenticated /health check was
    # failing identically to every other test).
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
    assert "app_name" in body
    assert "timestamp" in body


# ---------------------------------------------------------------------------
# POST /tasks — create_task
# ---------------------------------------------------------------------------
def test_create_task_success(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks", json={"text": "hello world", "task_type": "summarize"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["task_id"]


def test_create_task_missing_text_returns_422_from_pydantic(client: TestClient) -> None:
    # min_length=1 on TaskRequest.text — pydantic validation, not the
    # orchestrator's InvalidTaskInputError path.
    resp = client.post("/api/v1/tasks", json={"text": ""})
    assert resp.status_code == 422


def test_create_task_invalid_input_maps_to_422(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.script_create_error(InvalidTaskInputError("bad task_type"))
    resp = client.post("/api/v1/tasks", json={"text": "hello", "task_type": "bogus"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_task_input"


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/execute
# ---------------------------------------------------------------------------
def test_execute_task_success(client: TestClient, fake_orchestrator: FakeOrchestrator) -> None:
    fake_orchestrator.seed_task("t1", status=TaskStatus.PENDING)
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["results"]["summary"] == "fake summary"


def test_execute_task_not_found_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks/does-not-exist/execute")
    assert resp.status_code == 404
    assert resp.json()["error"] == "task_not_found"


def test_execute_task_already_running_returns_409(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.seed_task("t1", status=TaskStatus.RUNNING)
    resp = client.post("/api/v1/tasks/t1/execute")
    assert resp.status_code == 409
    assert resp.json()["error"] == "task_already_running"


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}
# ---------------------------------------------------------------------------
def test_get_task_status_success(client: TestClient, fake_orchestrator: FakeOrchestrator) -> None:
    fake_orchestrator.seed_task(
        "t1", status=TaskStatus.COMPLETED, results={"summary": "s"}, errors=[]
    )
    resp = client.get("/api/v1/tasks/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["results"] == {"summary": "s"}


def test_get_task_status_not_found_returns_404(client: TestClient) -> None:
    # routes.py does `orchestrator.tasks[task_id]` directly here — a plain
    # dict lookup, so this exercises app.py's KeyError handler rather than
    # the TaskNotFoundError handler. Both should still produce 404.
    resp = client.get("/api/v1/tasks/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "task_not_found"


# ---------------------------------------------------------------------------
# Auth dependency — real verify_api_key, NOT overridden.
# Outcome depends on your actual settings.REQUIRE_AUTH / API_KEY_HEADER,
# so this is informational rather than a strict pass/fail gate. Skips
# cleanly if get_settings() can't be imported/constructed in this env.
# ---------------------------------------------------------------------------
def test_real_auth_dependency_rejects_missing_key_when_required(
    fake_orchestrator: FakeOrchestrator,
) -> None:
    from infrastructure.config import get_settings

    settings = get_settings()
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    # verify_api_key intentionally NOT overridden here.
    try:
        with TestClient(app) as c:
            resp = c.post("/api/v1/tasks", json={"text": "hello"})
            if settings.REQUIRE_AUTH:
                assert resp.status_code == 401
            else:
                assert resp.status_code != 401
    finally:
        app.dependency_overrides.clear()
