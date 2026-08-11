"""
tests/test_day11.py — API layer tests for Day 11 (api/app.py, api/routes.py).

FIXED: FakeOrchestrator.create_task() previously declared its first
parameter as `text`, but the real routes.py calls
`orchestrator.create_task(input_data=request.text, task_type=...,
priority=...)` -- matching the real Orchestrator.create_task(self,
input_data: str, task_type=..., *, priority=...) signature exactly.
Since Python keyword arguments must match by name, the mismatched fake
threw "unexpected keyword argument 'input_data'" on every call. Renamed
the fake's parameter to input_data to match. This was a test bug, not
an app bug -- routes.py's call was already correct against the real
Orchestrator.

REMAINING ASSUMPTION (please confirm): api/schemas.py's TaskResponse has
a "message" field -- test_create_task_success asserts
body["message"] == "Task created successfully" per what routes.py's
create_task actually returns. I don't have current schemas.py in hand
to confirm this field exists; if TaskResponse doesn't declare "message",
FastAPI's response_model will silently drop it and this assertion will
fail with a KeyError, not the input_data error this file was previously
failing on. If that happens next, paste schemas.py.

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


class FakeOrchestrator:
    def __init__(self) -> None:
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self._raise_on_create: Optional[Exception] = None
        self._raise_on_execute: Optional[Exception] = None

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

    # FIXED: was `text`, now matches real Orchestrator.create_task's
    # first positional/keyword parameter name, input_data.
    def create_task(
        self, input_data: str, task_type: str = "full_pipeline", priority: int = 1
    ) -> str:
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
    import core.orchestrator as orchestrator_module

    # api/app.py's lifespan does `from core.orchestrator import orchestrator`
    # on startup, which triggers core/orchestrator.py's PEP 562 __getattr__
    # and builds a real LLMRuntime() (real GGUF load) unless the singleton
    # is already set. dependency_overrides only intercepts
    # Depends(get_orchestrator) at request time, so it can't stop that
    # startup-time import -- pre-seed the module global instead.
    orchestrator_module._orchestrator_singleton = fake_orchestrator

    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    with TestClient(app, base_url="http://localhost") as c:
        yield c
    app.dependency_overrides.clear()
    orchestrator_module._orchestrator_singleton = None


def test_health_check_returns_200(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "timestamp" in body


def test_create_task_success(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks", json={"text": "hello world", "task_type": "summarize"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert body["task_id"]
    assert body["message"] == "Task created successfully"


def test_create_task_missing_text_returns_422_from_pydantic(client: TestClient) -> None:
    resp = client.post("/api/v1/tasks", json={"text": ""})
    assert resp.status_code == 422


def test_create_task_orchestrator_error_returns_500(
    client: TestClient, fake_orchestrator: FakeOrchestrator
) -> None:
    fake_orchestrator.script_create_error(RuntimeError("boom"))
    resp = client.post("/api/v1/tasks", json={"text": "hello", "task_type": "bogus"})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Failed to create task"


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


def test_get_task_status_success(client: TestClient, fake_orchestrator: FakeOrchestrator) -> None:
    fake_orchestrator.seed_task("t1", status="completed", results={"summary": "s"}, errors=[])
    resp = client.get("/api/v1/tasks/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["results"] == {"summary": "s"}


def test_get_task_status_not_found_returns_404(client: TestClient) -> None:
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


def test_real_auth_dependency_rejects_missing_key_when_required(
    fake_orchestrator: FakeOrchestrator,
) -> None:
    from infrastructure.config import get_settings
    import core.orchestrator as orchestrator_module

    settings = get_settings()
    orchestrator_module._orchestrator_singleton = fake_orchestrator
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator
    try:
        with TestClient(app, base_url="http://localhost") as c:
            resp = c.post("/api/v1/tasks", json={"text": "hello"})
            if settings.REQUIRE_AUTH:
                assert resp.status_code == 401
            else:
                assert resp.status_code != 401
    finally:
        app.dependency_overrides.clear()
        orchestrator_module._orchestrator_singleton = None
