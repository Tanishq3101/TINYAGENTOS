"""
API routes for TinyAgentOS.

PATCHED: wrapped the three blocking orchestrator calls
(create_task / execute_pipeline / get_task) with
fastapi.concurrency.run_in_threadpool.

Root cause of the integration-test timeouts: every route here is
`async def`, but orchestrator.create_task(...) / execute_pipeline(...) /
get_task(...) are plain synchronous calls -- execute_pipeline in
particular runs real LLM inference (now further serialized by
llm_runtime.py's _inference_lock). A synchronous, multi-second call
inside an `async def` blocks FastAPI's single event loop for its full
duration -- no other request, including /health, can be served until it
returns. That's why even the trivial health check was timing out
whenever a full_pipeline execution was in flight.

run_in_threadpool offloads each blocking call to a worker thread and
awaits the result, freeing the event loop to keep serving other requests
concurrently. This does NOT change any response shape, status code, or
error-handling logic below -- only where the orchestrator call actually
runs.

Implements:
- Task creation and execution
- Task status queries
- Health checks
- Authentication and validation
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from datetime import datetime

from api.dependencies import verify_api_key, get_orchestrator
from api.schemas import TaskRequest, TaskResponse, ExecutionResult
from core.orchestrator import Orchestrator
from infrastructure.logging import logger

# ========================================
# Dependency Functions
# ========================================
#
# verify_api_key() and get_orchestrator() now live in api/dependencies.py
# as the single implementation -- this file previously carried its own
# near-identical copy of verify_api_key() (the one actually wired via
# Depends() below), while api/dependencies.py's copy sat unused. See
# docs/SECURITY.md's "API Key Authentication" section for why that
# duplication existed and docs/API.md for the current auth behavior.

# ========================================
# Router Setup
# ========================================

router = APIRouter(prefix="/api/v1", tags=["tasks"])


# ========================================
# ENDPOINTS
# ========================================


@router.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    Returns:
        dict: Service health status
    """
    logger.info("Health check requested")
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    request: TaskRequest,  # ✅ PYDANTIC VALIDATION HAPPENS FIRST
    api_key: str = Depends(verify_api_key),  # ✅ THEN auth is checked
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> TaskResponse:
    """
    Create a new task.

    Args:
        request: Task creation request
        api_key: Verified API key
        orchestrator: Orchestrator instance

    Returns:
        TaskResponse: Created task details

    Raises:
        HTTPException: 400 if validation fails, 401 if auth fails
    """
    logger.info(f"Creating task with text length: {len(request.text)}")

    try:
        # PATCHED: offloaded to a worker thread so this doesn't block the
        # event loop. create_task() itself is normally fast (validation +
        # dict bookkeeping, no LLM call), but it's still a synchronous
        # call and can briefly hold Orchestrator's internal lock while
        # another task's cleanup runs -- cheap insurance either way.
        task_id = await run_in_threadpool(
            orchestrator.create_task,
            input_data=request.text,
            task_type=request.task_type,
            priority=request.priority,
        )

        return TaskResponse(
            task_id=task_id,
            status="created",
            message="Task created successfully",
        )

    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create task",
        )


@router.post("/tasks/{task_id}/execute")
async def execute_task(
    task_id: str,
    api_key: str = Depends(verify_api_key),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    """
    Execute a task.

    Args:
        task_id: ID of task to execute
        api_key: Verified API key
        orchestrator: Orchestrator instance

    Returns:
        dict: Execution results

    Raises:
        HTTPException: 404 if task not found, 409 if already running
    """
    logger.info(f"Executing task: {task_id}")

    try:
        # PATCHED: this is the important one. execute_pipeline() runs real
        # LLM inference (seconds to tens of seconds) -- without
        # run_in_threadpool this held the event loop hostage for the
        # entire pipeline run, which is why /health and every other
        # concurrent request timed out during test_full_pipeline_*.
        result = await run_in_threadpool(orchestrator.execute_pipeline, task_id)

        return {
            "status": "success",
            "task_id": task_id,
            "result": result,
        }

    except ValueError as e:
        if "not found" in str(e):
            logger.warning(f"Task not found: {task_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )
        elif "already running" in str(e):
            logger.warning(f"Task already running: {task_id}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id} is already running",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task execution failed",
        )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(
    task_id: str,
    api_key: str = Depends(verify_api_key),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> TaskResponse:
    """
    Get task status.

    Args:
        task_id: ID of task
        api_key: Verified API key
        orchestrator: Orchestrator instance

    Returns:
        TaskResponse: Task status and details

    Raises:
        HTTPException: 404 if task not found
    """
    logger.info(f"Getting task status: {task_id}")

    try:
        # PATCHED: same reasoning as create_task -- cheap call normally,
        # but offloaded for consistency and to avoid holding the event
        # loop if Orchestrator's internal lock is briefly contended by a
        # concurrent execute_pipeline() call.
        task = await run_in_threadpool(orchestrator.get_task, task_id)

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )

        return TaskResponse(
            task_id=task_id,
            status=task.get("status", "unknown"),
            created_at=task.get("created_at"),
            results=task.get("results"),
            errors=task.get("errors"),
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get task status",
        )