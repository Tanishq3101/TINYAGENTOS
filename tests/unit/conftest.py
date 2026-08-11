# tests/conftest.py

"""
Pytest configuration and fixtures.
"""

import pytest
from fastapi.testclient import TestClient
from api.app import app


class FakeOrchestrator:
    """Fake orchestrator for testing."""

    def __init__(self):
        self.tasks = {}

    def create_task(self, text: str, task_type: str = "full_pipeline", priority: int = 1) -> str:
        """Create a task."""
        task_id = f"task-{len(self.tasks)}"
        self.tasks[task_id] = {
            "id": task_id,
            "text": text,
            "task_type": task_type,
            "priority": priority,
            "status": "created",
            "results": None,
            "errors": None,
        }
        return task_id

    def execute_pipeline(self, task_id: str) -> dict:
        """Execute a task."""
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not found")

        self.tasks[task_id]["status"] = "completed"
        self.tasks[task_id]["results"] = {
            "summary": "Test summary",
            "extraction": {"key": "value"},
            "evaluation": {"score": 8},
        }
        return self.tasks[task_id]["results"]

    def get_task(self, task_id: str) -> dict:
        """Get task by ID."""
        return self.tasks.get(task_id)


@pytest.fixture
def fake_orchestrator():
    """Provide fake orchestrator."""
    return FakeOrchestrator()


@pytest.fixture
def client(fake_orchestrator):
    """Provide test client with fake orchestrator."""
    from api.routes import get_orchestrator
    import core.orchestrator as orchestrator_module

    # api/app.py's lifespan does `from core.orchestrator import orchestrator`
    # on startup, which triggers core/orchestrator.py's PEP 562 __getattr__.
    # That only builds a real LLMRuntime() (which loads a real GGUF file)
    # if _orchestrator_singleton is still unset. Pre-seed it with the fake
    # here, before TestClient(app) runs the lifespan, so the real build
    # never fires. dependency_overrides alone doesn't help here since it
    # only intercepts Depends(get_orchestrator) at request time, not the
    # direct module import that runs at startup.
    orchestrator_module._orchestrator_singleton = fake_orchestrator

    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    orchestrator_module._orchestrator_singleton = None