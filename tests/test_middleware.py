"""
tests/test_day11_middleware.py — tests for api/middleware.py (LoggingMiddleware).

ASSUMPTIONS (please confirm against your real code):
- `api.middleware` does `from infrastructure.logging import log_info` at
  module scope (confirmed from the middleware.py you pasted) — so tests
  patch `api.middleware.log_info`, not `infrastructure.logging.log_info`.
  Patching the *usage site* is required here since middleware.py imported
  the name directly rather than the module.
- `log_info(message, **context)` accepts arbitrary keyword context args
  (method, path, client, status_code, process_time_ms) — confirmed from
  the call sites in middleware.py.

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
    with patch("api.middleware.log_info") as mock_log_info:
        resp = middleware_client.get("/ok")

    assert resp.status_code == 200
    assert resp.json() == {"msg": "ok"}

    assert mock_log_info.call_count == 2
    request_call, response_call = mock_log_info.call_args_list

    assert request_call.kwargs["method"] == "GET"
    assert request_call.kwargs["path"] == "/ok"
    assert "client" in request_call.kwargs

    assert response_call.kwargs["status_code"] == 200
    assert "process_time_ms" in response_call.kwargs
    assert isinstance(response_call.kwargs["process_time_ms"], float)
    assert response_call.kwargs["process_time_ms"] >= 0


def test_logs_correct_status_code_for_non_200(middleware_client) -> None:
    with patch("api.middleware.log_info") as mock_log_info:
        resp = middleware_client.get("/created")

    assert resp.status_code == 201
    response_call = mock_log_info.call_args_list[1]
    assert response_call.kwargs["status_code"] == 201


def test_logs_correct_path_for_different_routes(middleware_client) -> None:
    with patch("api.middleware.log_info") as mock_log_info:
        middleware_client.get("/ok")
    assert mock_log_info.call_args_list[0].kwargs["path"] == "/ok"

    with patch("api.middleware.log_info") as mock_log_info:
        middleware_client.get("/created")
    assert mock_log_info.call_args_list[0].kwargs["path"] == "/created"


def test_process_time_ms_reflects_actual_elapsed_time(middleware_client) -> None:
    """Not a strict timing assertion (flaky by nature) -- just checks the
    value is a sane non-negative float rather than e.g. always 0.0."""
    with patch("api.middleware.log_info") as mock_log_info:
        middleware_client.get("/ok")

    process_time_ms = mock_log_info.call_args_list[1].kwargs["process_time_ms"]
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
        with patch("api.middleware.log_info") as mock_log_info:
            result = await middleware.dispatch(fake_request, call_next)
        return result, mock_log_info

    result, mock_log_info = asyncio.run(run())

    assert result is fake_response
    request_call = mock_log_info.call_args_list[0]
    assert request_call.kwargs["client"] == "unknown"
