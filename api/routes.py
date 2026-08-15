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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from datetime import datetime

from starlette.responses import Response

from api.dependencies import verify_api_key, get_orchestrator, get_database
from api.limiter import limiter
from api.schemas import TaskRequest, TaskResponse
from core.orchestrator import Orchestrator
from core.llm_runtime import LLMRuntime, ModelNotLoadedError
from infrastructure.config import get_settings
from infrastructure.logging import logger
from infrastructure.security import SecurityManager
from infrastructure.prometheus_metrics import render_metrics
from storage.database import Database

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

# Separate, unprefixed router for /metrics. Prometheus's default scrape
# convention is a bare /metrics path (not /api/v1/metrics), and the
# endpoint is intentionally unauthenticated -- verify_api_key would
# require every Prometheus scrape config to carry an API key, and
# scrape targets are normally reached only from inside the deployment
# network, not exposed the way the task API is. If that's not true for
# your deployment, put this behind a reverse-proxy allowlist or add
# Depends(verify_api_key) here.
#
# Must be included separately in api/app.py:
#   from api.routes import router, metrics_router
#   app.include_router(router)
#   app.include_router(metrics_router)
metrics_router = APIRouter(tags=["observability"])


@metrics_router.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint. Returns current counters/histograms
    in Prometheus text exposition format (see infrastructure/prometheus_metrics.py
    for what's actually instrumented -- task counts/duration, per-agent
    step duration/errors, active task gauge)."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# ========================================
# ENDPOINTS
# ========================================


@router.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    PATCHED: now reports whether the LLM model is actually loaded, via
    LLMRuntime.is_loaded (see core/llm_runtime.py -- __init__ skips
    loading, rather than crashing, when MODEL_PATH is missing or
    TINYAGENT_SKIP_LLM_LOAD=1 is set, e.g. in CI smoke tests). Calling
    LLMRuntime() here is cheap: it's a singleton and api/app.py's
    lifespan already constructs it once at boot (via
    core.orchestrator.orchestrator) -- this just reads the existing
    instance, it does not re-run model loading.

    `status` stays "healthy" either way -- the API process itself is up
    and serving requests, which is what this field has always meant.
    `model_loaded` is the new, separate signal for "can this instance
    actually run inference right now", which callers should check
    before hitting /tasks/{task_id}/execute if they want to fail fast
    instead of getting a 503 from that endpoint.

    Returns:
        dict: Service health status
    """
    logger.info("Health check requested")
    return {
        "status": "healthy",
        "model_loaded": LLMRuntime().is_loaded,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.post("/tasks", response_model=TaskResponse)
@limiter.limit(f"{get_settings().RATE_LIMIT_PER_MINUTE}/minute")
async def create_task(
    request: Request,  # required by slowapi's @limiter.limit -- must be named "request"
    task_request: TaskRequest,  # ✅ PYDANTIC VALIDATION HAPPENS FIRST
    api_key: str = Depends(verify_api_key),  # ✅ THEN auth is checked
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> TaskResponse:
    """
    Create a new task.

    Args:
        task_request: Task creation request
        api_key: Verified API key
        orchestrator: Orchestrator instance

    Returns:
        TaskResponse: Created task details

    Raises:
        HTTPException: 400 if validation fails, 401 if auth fails
    """
    logger.info(f"Creating task with text length: {len(task_request.text)}")

    try:
        # PATCHED: offloaded to a worker thread so this doesn't block the
        # event loop. create_task() itself is normally fast (validation +
        # dict bookkeeping, no LLM call), but it's still a synchronous
        # call and can briefly hold Orchestrator's internal lock while
        # another task's cleanup runs -- cheap insurance either way.
        task_id = await run_in_threadpool(
            orchestrator.create_task,
            input_data=task_request.text,
            task_type=task_request.task_type,
            priority=task_request.priority,
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
@limiter.limit(f"{get_settings().RATE_LIMIT_PER_MINUTE}/minute")
async def execute_task(
    request: Request,  # required by slowapi's @limiter.limit
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

    # PATCHED: must be caught before the generic `except Exception` below
    # -- otherwise a missing/skipped model (see core/llm_runtime.py) would
    # surface here as an opaque 500 "Task execution failed" instead of a
    # clean, specific 503 that tells the caller *why* (and that retrying
    # right now won't help until a model is actually loaded).
    except ModelNotLoadedError as e:
        logger.warning(f"Task execution failed -- model not loaded: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )

    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task execution failed",
        )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
@limiter.limit(f"{get_settings().RATE_LIMIT_PER_MINUTE}/minute")
async def get_task_status(
    request: Request,  # required by slowapi's @limiter.limit
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


@router.post("/api-keys/rotate")
@limiter.limit(f"{get_settings().RATE_LIMIT_PER_MINUTE}/minute")
async def rotate_api_key(
    request: Request,  # required by slowapi's @limiter.limit
    api_key_id: str = Depends(verify_api_key),
    db: Database = Depends(get_database),
) -> dict:
    """
    Rotate the calling API key: issue a new key and revoke the one used
    to authenticate this request.

    Depends(verify_api_key) already resolves to the matched ApiKeyModel
    row's id (never the raw key -- see api/dependencies.py), so no
    re-hashing or re-lookup is needed here to know which row to revoke.

    Returns:
        dict: the new raw API key (shown exactly once -- store it now)
        and its row id.

    Raises:
        HTTPException: 401 if auth fails (via verify_api_key), 503 if the
        key store is unreachable.
    """
    if api_key_id == "no-auth":
        # REQUIRE_AUTH is False -- there is no real key record to rotate.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key rotation is unavailable when REQUIRE_AUTH is disabled",
        )

    new_raw_key = SecurityManager.generate_api_key()
    new_key_hash = SecurityManager.hash_api_key(new_raw_key)

    try:
        new_row = await run_in_threadpool(db.create_api_key, new_key_hash, label="rotated")
        await run_in_threadpool(db.revoke_api_key, api_key_id)
    except Exception as e:
        logger.error(f"API key rotation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to rotate API key",
        )

    logger.info(f"API key rotated: {api_key_id} -> {new_row.id}")
    return {
        "api_key": new_raw_key,
        "key_id": new_row.id,
        "message": "Store this key now -- it will not be shown again.",
    }