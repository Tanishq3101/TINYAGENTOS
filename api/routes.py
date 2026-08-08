# api/routes.py

"""
API routes.

ADAPTED FROM PLAN — two real fixes:

1. The plan declares `orchestrator: Optional[Orchestrator] = None` at
   module level and never assigns it — every route would crash on a None
   orchestrator. Fixed by reading it off `request.app.state.orchestrator`
   (set once in app.py's lifespan startup) via the `get_orchestrator`
   dependency below. This also avoids a circular import between app.py
   and routes.py, since routes.py never needs to import `app` itself.

2. `verify_api_key` is a genuine placeholder, same limitation as the
   plan's version — it currently only checks that a header was supplied
   when settings.REQUIRE_AUTH is True, not that it's a *valid* key. There
   is no ApiKeyModel / key store built yet (storage/models.py only has
   TaskModel, AgentExecutionModel, OutputModel), so real verification via
   SecurityManager.verify_api_key(candidate, stored_hash) has nothing to
   compare against yet. TODO once a key store exists: look up the
   presented key's hash and call SecurityManager.verify_api_key on it
   instead of just checking presence.

ASSUMPTIONS to verify against your real core/orchestrator.py:
- orchestrator.tasks[task_id] is a dict with at least "status" (an enum
  with .value) and "errors" (confirmed from test_pipeline.py) — also
  assuming "created_at" / "completed_at" / "results" keys exist for the
  TaskResponse fields below. Confirm these before shipping; if any are
  missing/named differently, this route will KeyError or just omit them
  silently (using .get() below to be safe either way).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from starlette.requests import Request

from api.schemas import HealthResponse, TaskRequest, TaskResponse
from core.orchestrator import Orchestrator
from infrastructure.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/api/v1", tags=["tasks"])


def get_orchestrator(request: Request) -> Orchestrator:
    """Pull the orchestrator built once at startup off app.state."""
    return request.app.state.orchestrator


async def verify_api_key(
    x_api_key: str = Header(default=None, alias=settings.API_KEY_HEADER),
) -> str:
    """Bare-minimum auth gate — see module docstring for what's missing."""
    if settings.REQUIRE_AUTH and not x_api_key:
        raise HTTPException(
            status_code=401,
            detail=f"Missing {settings.API_KEY_HEADER} header",
        )
    return x_api_key or ""


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    request: TaskRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """Create a new task. Raises InvalidTaskInputError (-> 422 via the
    handler in app.py) for empty/oversized text or an unknown task_type."""
    task_id = orchestrator.create_task(request.text, task_type=request.task_type)

    return TaskResponse(task_id=task_id, status="pending")


@router.post("/tasks/{task_id}/execute", response_model=TaskResponse)
async def execute_task(
    task_id: str,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """Run the pipeline for a task. Raises TaskNotFoundError (-> 404) or
    TaskAlreadyRunningError (-> 409) via the handlers in app.py."""
    results = orchestrator.execute_pipeline(task_id)
    task = orchestrator.tasks[task_id]

    return TaskResponse(
        task_id=task_id,
        status=task["status"].value,
        created_at=task.get("created_at"),
        completed_at=task.get("completed_at"),
        results=results,
        errors=task.get("errors"),
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(
    task_id: str,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """Get current status/results for a task. Raises TaskNotFoundError
    (-> 404) via the handler in app.py if task_id is unknown."""
    task = orchestrator.tasks[task_id]

    return TaskResponse(
        task_id=task_id,
        status=task["status"].value,
        created_at=task.get("created_at"),
        completed_at=task.get("completed_at"),
        results=task.get("results"),
        errors=task.get("errors"),
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Unauthenticated health check — no api_key dependency on purpose."""
    return HealthResponse(
        status="healthy",
        app_name=settings.APP_NAME,
        timestamp=datetime.now(timezone.utc),
    )
