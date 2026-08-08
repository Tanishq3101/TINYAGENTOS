"""
tests/test_middleware.py — tests for api/middleware.py (LoggingMiddleware).

Adapted to match the ACTUAL api/middleware.py you're running:

- middleware.py does `from infrastructure.logging import logger` and calls
  `logger.info(message, extra={...})` — a method call on an imported
  object, not a standalone `log_info(**kwargs)` function. So we patch
  `api.middleware.logger` (the object middleware.py holds a reference to)
  and inspect `logger.info.call_args_list`, reading structured fields out
  of the `extra=` kwarg dict rather than off the call's kwargs directly.
- Two `logger.info()` calls per request: one for the incoming request
  (message contains "Request: <method> <path>", extra has method/path/
  client), one for the outgoing response (message contains "Response:
  <status>", extra has status_code/process_time_ms).
- `client` in extra is `request.client.host` if request.client is set,
  else the string "unknown".

Two testing strategies used here:
1. Full integration through a real (tiny) FastAPI app + TestClient — this
   is the one that matters, since it exercises the middleware exactly as
   Starlette invokes it in production (dispatch() as part of the real
   ASGI middleware stack), not a hand-built Request.
2. A direct unit-level call to `dispatch()` for the `request.client is
   None -> "unknown"` branch, since TestClient always populates a fake
   client address and can't easily produce a None client through the
   normal HTTP path. This uses `asyncio.run()` directly rather than
   pytest-asyncio, so it needs no extra pytest plugin/config.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import LoggingMiddleware


def _build_test_app() -> FastAPI:
    """Minimal app with only LoggingMiddleware attached -- deliberately
    does not import api.app, so these tests don't depend on a real
    Orchestrator/lifespan being constructible."""
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/ok")
    async def ok():
        return {"msg": "ok"}

    @app.get("/created", status_code=201)
    async def created():
        return {"msg": "created"}

    @app.get("/boom")
    async def boom():
        raise ValueError("simulated route failure")

    return app


@pytest.fixture()
def middleware_client():
    with TestClient(_build_test_app(), raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Integration: real ASGI request through the middleware
# ---------------------------------------------------------------------------
def test_logs_request_and_response_and_passes_response_through(middleware_client) -> None:
    with patch("api.middleware.logger") as mock_logger:
        resp = middleware_client.get("/ok")

    assert resp.status_code == 200
    assert resp.json() == {"msg": "ok"}

    assert mock_logger.info.call_count == 2
    request_call, response_call = mock_logger.info.call_args_list

    request_extra = request_call.kwargs["extra"]
    assert request_extra["method"] == "GET"
    assert request_extra["path"] == "/ok"
    assert "client" in request_extra

    response_extra = response_call.kwargs["extra"]
    assert response_extra["status_code"] == 200
    assert "process_time_ms" in response_extra
    assert isinstance(response_extra["process_time_ms"], float)
    assert response_extra["process_time_ms"] >= 0


def test_logs_correct_status_code_for_non_200(middleware_client) -> None:
    with patch("api.middleware.logger") as mock_logger:
        resp = middleware_client.get("/created")

    assert resp.status_code == 201
    response_call = mock_logger.info.call_args_list[1]
    assert response_call.kwargs["extra"]["status_code"] == 201


def test_logs_correct_path_for_different_routes(middleware_client) -> None:
    with patch("api.middleware.logger") as mock_logger:
        middleware_client.get("/ok")
    assert mock_logger.info.call_args_list[0].kwargs["extra"]["path"] == "/ok"

    with patch("api.middleware.logger") as mock_logger:
        middleware_client.get("/created")
    assert mock_logger.info.call_args_list[0].kwargs["extra"]["path"] == "/created"


def test_process_time_ms_reflects_actual_elapsed_time(middleware_client) -> None:
    """Not a strict timing assertion (flaky by nature) -- just checks the
    value is a sane non-negative float rather than e.g. always 0.0."""
    with patch("api.middleware.logger") as mock_logger:
        middleware_client.get("/ok")

    process_time_ms = mock_logger.info.call_args_list[1].kwargs["extra"]["process_time_ms"]
    assert isinstance(process_time_ms, float)
    assert 0 <= process_time_ms < 5000  # generous upper bound, just sanity


# ---------------------------------------------------------------------------
# Unit-level: request.client is None -> "unknown"
# TestClient always populates a fake client, so this branch is exercised
# directly against dispatch() with a hand-built fake request instead.
# ---------------------------------------------------------------------------
class _FakeURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeRequest:
    def __init__(self, method: str, path: str, client=None) -> None:
        self.method = method
        self.url = _FakeURL(path)
        self.client = client  # None simulates request.client being unset


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_dispatch_logs_unknown_when_request_client_is_none() -> None:
    middleware = LoggingMiddleware(app=MagicMock())
    fake_request = _FakeRequest(method="GET", path="/no-client", client=None)
    fake_response = _FakeResponse(status_code=200)

    async def call_next(_request):
        return fake_response

    async def run():
        with patch("api.middleware.logger") as mock_logger:
            result = await middleware.dispatch(fake_request, call_next)
        return result, mock_logger

    result, mock_logger = asyncio.run(run())

    assert result is fake_response
    request_call = mock_logger.info.call_args_list[0]
    assert request_call.kwargs["extra"]["client"] == "unknown"
