"""
API routes for TinyAgentOS.

Implements:
- Task creation and execution
- Task status queries
- Health checks
- Authentication and validation
"""

from fastapi import APIRouter, Depends, HTTPException, Header, status
from typing import Optional
from datetime import datetime

from api.schemas import TaskRequest, TaskResponse, ExecutionResult
from core.orchestrator import Orchestrator
from infrastructure.logging import logger
from infrastructure.config import get_settings

# ========================================
# Dependency Functions
# ========================================


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """
    Verify API key from request headers.

    Raises:
        HTTPException: 401 if key is missing or invalid
    """
    settings = get_settings()

    if not settings.REQUIRE_AUTH:
        return "no-auth"

    # Check if header is present
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate format
    if not x_api_key.startswith("sk-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return x_api_key


def get_orchestrator() -> Orchestrator:
    """Get orchestrator instance."""
    # In production, this would be dependency injection
    from core.orchestrator import orchestrator

    return orchestrator


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
        task_id = orchestrator.create_task(
            text=request.text,
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
        result = orchestrator.execute_pipeline(task_id)

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
        task = orchestrator.get_task(task_id)

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
