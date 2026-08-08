# api/app.py

"""
FastAPI application entrypoint.

ADAPTED FROM PLAN — three real mismatches fixed:

1. `from infrastructure.logging import StructuredLogger` doesn't exist.
   Real module exposes a module-level `logger` + log_info/log_error/etc.
   helpers instead. Using those directly.

2. `from infrastructure.config import settings` doesn't exist as a bare
   importable name. Real pattern is `get_settings()` (lru_cache'd). Using
   that instead.

3. The plan's routes.py declares `orchestrator: Optional[Orchestrator] =
   None` at module level and never actually assigns it anywhere — every
   request would hit `orchestrator.create_task(...)` on a None object and
   crash. Fixed here by building the orchestrator once in the lifespan
   startup hook and storing it on `app.state.orchestrator`, then reading
   it via a request-scoped dependency in routes.py.

ASSUMPTIONS to verify against your real core/orchestrator.py:
- Orchestrator.__init__(agents: dict, max_input_length: int = ...) — this
  matches every call site seen in test_pipeline.py.
- Orchestrator.shutdown() exists and is safe to call once at app shutdown.
- Exception types InvalidTaskInputError, TaskNotFoundError,
  TaskAlreadyRunningError all live in core.orchestrator (confirmed from
  test_pipeline.py's imports). StepExecutionError also appeared in your
  pasted test output but I haven't seen it imported explicitly anywhere —
  confirm its import path before relying on the handler below.
- Settings has no APP_VERSION field (checked infrastructure/config.py
  directly) — using a hardcoded string for now. Add APP_VERSION to
  Settings if you want this to be real config instead.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from agents.base import AgentConfig
from agents.critic import CriticAgent
from agents.extractor import ExtractorAgent
from agents.summarizer import SummarizerAgent
from api.middleware import LoggingMiddleware
from core.llm_runtime import LLMRuntime
from core.orchestrator import (
    InvalidTaskInputError,
    Orchestrator,
    TaskAlreadyRunningError,
    TaskNotFoundError,
)
from infrastructure.config import get_settings
from infrastructure.logging import log_info

settings = get_settings()

# Hardcoded — Settings has no APP_VERSION field today. See note above.
APP_VERSION = "0.1.0"


def _build_orchestrator() -> Orchestrator:
    """Instantiate the real LLM runtime, wire up the three agents, and
    hand back a ready-to-use Orchestrator. Called once at startup."""
    llm = LLMRuntime()  # singleton — safe to call again elsewhere, returns same instance
    agents = {
        "summarizer": SummarizerAgent(AgentConfig(name="summarizer", description=""), llm),
        "extractor": ExtractorAgent(AgentConfig(name="extractor", description=""), llm),
        "critic": CriticAgent(AgentConfig(name="critic", description=""), llm),
    }
    return Orchestrator(agents)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log_info("TinyAgentOS starting up")
    app.state.orchestrator = _build_orchestrator()
    yield
    # Shutdown
    log_info("TinyAgentOS shutting down")
    app.state.orchestrator.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    version=APP_VERSION,
    description="Resource-aware multi-agent AI framework",
    lifespan=lifespan,
)

# --- Middleware --------------------------------------------------------
# NOTE: allowed_hosts / allow_origins below are dev-only placeholders,
# same as the plan's original values. Tighten these before any real
# deployment — TrustedHostMiddleware and CORS should both be
# environment-driven (e.g. new Settings fields), not hardcoded.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)


# --- Exception handlers -------------------------------------------------
# Translate the orchestrator's domain exceptions into proper HTTP status
# codes instead of letting them fall through to a generic 500.


@app.exception_handler(InvalidTaskInputError)
async def handle_invalid_input(request: Request, exc: InvalidTaskInputError):
    return JSONResponse(
        status_code=422, content={"error": "invalid_task_input", "detail": str(exc)}
    )


@app.exception_handler(TaskNotFoundError)
async def handle_task_not_found(request: Request, exc: TaskNotFoundError):
    return JSONResponse(status_code=404, content={"error": "task_not_found", "detail": str(exc)})


@app.exception_handler(TaskAlreadyRunningError)
async def handle_task_already_running(request: Request, exc: TaskAlreadyRunningError):
    return JSONResponse(
        status_code=409, content={"error": "task_already_running", "detail": str(exc)}
    )


@app.exception_handler(KeyError)
async def handle_missing_task(request: Request, exc: KeyError):
    # Routes that do `orchestrator.tasks[task_id]` directly (get_task_status,
    # execute_task) raise a plain KeyError on a missing id, not
    # TaskNotFoundError — this catches that case too. TaskNotFoundError is a
    # KeyError subclass per test_pipeline.py, so this won't shadow the more
    # specific handler above; it's a fallback for the raw-dict-lookup path.
    return JSONResponse(status_code=404, content={"error": "task_not_found", "detail": str(exc)})


# --- Routes --------------------------------------------------------------
# Imported after `app` exists so api/routes.py can do `from api.app import app`
# without a circular import at module-load time.
from api.routes import router  # noqa: E402

app.include_router(router)
