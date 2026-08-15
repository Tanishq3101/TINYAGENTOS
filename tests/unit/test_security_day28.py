"""
Tests for the Day 28 security changes: rate limiting and CORS narrowing.

Drop into tests/test_security_day28.py.

Matched against your actual app.py / routes.py / config.py:
- CORS tests target GET /api/v1/health -- it's the one route with no
  auth and no rate limiting, so it isolates CORS behavior cleanly.
- Rate limit tests target POST /api/v1/tasks -- the cheapest of the
  four @limiter.limit(...) routes (create_task doesn't run inference,
  unlike execute_task).
- Auth, orchestrator, and DB are overridden via app.dependency_overrides
  so these tests don't need a real API key, a loaded model, or a live
  database. That means they exercise the limiter/CORS layer in
  isolation, not the full auth/orchestrator stack -- add separate
  integration tests for those if you don't already have them.

One thing I couldn't verify without api/schemas.py and
api/dependencies.py: the exact TaskRequest field constraints and what
verify_api_key actually returns/raises. The dependency_overrides below
sidestep both, but if TaskRequest has required fields beyond
text/task_type/priority (with validation, e.g. task_type must be one of
an enum), fix VALID_TASK_PAYLOAD accordingly -- a 422 there will look
like a rate-limit test failure and isn't.
"""

import time
import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.dependencies import verify_api_key, get_orchestrator, get_database
from api.limiter import limiter
from infrastructure.config import get_settings

settings = get_settings()

# TODO: confirm against api/schemas.py -- TaskRequest's real required
# fields / allowed task_type values.
VALID_TASK_PAYLOAD = {
    "text": "test input",
    "task_type": "summarizer",
    "priority": 1,
}


class _FakeOrchestrator:
    def create_task(self, input_data, task_type, priority):
        return "fake-task-id"

    def get_task(self, task_id):
        return {"status": "created", "created_at": None, "results": None, "errors": None}

    def execute_pipeline(self, task_id):
        return {"output": "fake result"}


class _FakeDatabase:
    def create_api_key(self, key_hash, label):
        class Row:
            id = "fake-row-id"

        return Row()

    def revoke_api_key(self, api_key_id):
        return None


@pytest.fixture
def client():
    app.dependency_overrides[verify_api_key] = lambda: "fake-api-key-id"
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_database] = lambda: _FakeDatabase()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Rate limiting -- POST /api/v1/tasks
# ---------------------------------------------------------------------------


class TestRateLimiting:
    ENDPOINT = "/api/v1/tasks"

    @pytest.fixture(autouse=True)
    def reset_rate_limiter(self):
        """
        api/limiter.py's `limiter` is a module-level singleton with
        slowapi's default in-memory storage, imported once and shared
        for the entire pytest process -- not just across tests in this
        class, but across the whole session. get_remote_address always
        resolves to the same fixed value for TestClient requests, so
        ANY test anywhere in the suite that POSTs /api/v1/tasks through
        the real app (e.g. tests/e2e/test_complete_flow.py,
        tests/integration/test_deployment_smoke.py) shares this exact
        bucket. Without resetting here, tests in this class inherit
        whatever budget earlier tests/files already spent, making
        "request N/60 succeeds" assertions depend on suite-wide
        ordering instead of this test's own behavior.

        Reset before AND after each test: before, so this test starts
        from a clean budget regardless of what ran earlier in the
        session; after, so this class doesn't leave a partially-spent
        bucket behind for whatever runs next.
        """
        limiter.reset()
        yield
        limiter.reset()

    def test_requests_within_limit_succeed(self, client):
        limit = settings.RATE_LIMIT_PER_MINUTE
        for i in range(limit):
            resp = client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)
            assert resp.status_code != 429, (
                f"Request {i + 1}/{limit} was rate-limited early "
                f"(got {resp.status_code}: {resp.text})"
            )

    def test_request_over_limit_is_rejected(self, client):
        limit = settings.RATE_LIMIT_PER_MINUTE
        for _ in range(limit):
            client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)

        resp = client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)
        assert resp.status_code == 429
        # slowapi's default _rate_limit_exceeded_handler (used as-is in
        # app.py, not customized) returns {"error": "Rate limit exceeded: ..."}
        body = resp.json()
        assert "error" in body

    @pytest.mark.slow
    def test_limit_resets_after_window(self, client):
        """
        Slow (sleeps past the 60s window) -- run explicitly, exclude
        from normal CI with `-m "not slow"`.
        """
        limit = settings.RATE_LIMIT_PER_MINUTE
        for _ in range(limit):
            client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)

        blocked = client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)
        assert blocked.status_code == 429

        time.sleep(61)

        resp = client.post(self.ENDPOINT, json=VALID_TASK_PAYLOAD)
        assert resp.status_code != 429

    def test_health_endpoint_is_not_rate_limited(self, client):
        """
        /api/v1/health has no @limiter.limit(...) decorator in
        routes.py -- confirms it stays exempt (e.g. so orchestration/
        monitoring can poll it freely).
        """
        limit = settings.RATE_LIMIT_PER_MINUTE
        for _ in range(limit + 5):
            resp = client.get("/api/v1/health")
            assert resp.status_code != 429

    def test_key_func_limits_by_client_ip_not_api_key(self, client):
        """
        limiter's key_func is get_remote_address (per api/limiter.py /
        app.py's comment) -- it keys by IP, not by API key. Two
        different API keys from the same TestClient (same source IP)
        should therefore SHARE a bucket. This is a documentation test:
        if it starts failing, the key_func changed and the docstring
        in app.py is now stale.
        """
        limit = settings.RATE_LIMIT_PER_MINUTE
        for _ in range(limit):
            client.post(
                self.ENDPOINT,
                json=VALID_TASK_PAYLOAD,
                headers={settings.API_KEY_HEADER: "key-a"},
            )

        still_blocked = client.post(
            self.ENDPOINT,
            json=VALID_TASK_PAYLOAD,
            headers={settings.API_KEY_HEADER: "key-b"},
        )
        assert still_blocked.status_code == 429


# ---------------------------------------------------------------------------
# CORS narrowing -- GET /api/v1/health
# ---------------------------------------------------------------------------


class TestCORS:
    ENDPOINT = "/api/v1/health"
    ALLOWED_ORIGIN = "http://localhost:3000"  # matches app.py's allow_origins

    def test_preflight_allows_configured_method_and_header(self, client):
        resp = client.options(
            self.ENDPOINT,
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert resp.status_code in (200, 204)
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert "POST" in allow_methods
        assert "x-api-key" in allow_headers.lower()

    def test_preflight_rejects_disallowed_method(self, client):
        resp = client.options(
            self.ENDPOINT,
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "DELETE",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "DELETE" not in allow_methods

    def test_preflight_rejects_disallowed_header(self, client):
        resp = client.options(
            self.ENDPOINT,
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Custom-Unapproved-Header",
            },
        )
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert "x-custom-unapproved-header" not in allow_headers.lower()

    def test_wildcard_not_reintroduced(self, client):
        """
        Regression guard: app.py currently hardcodes
        allow_methods=["GET", "POST"] / allow_headers=["X-API-Key",
        "Content-Type"]. Fails loudly if someone widens either back to
        "*" later.
        """
        resp = client.options(
            self.ENDPOINT,
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert allow_methods.strip() != "*"
        assert allow_headers.strip() != "*"

    def test_disallowed_origin_gets_no_cors_headers(self, client):
        """
        allow_origins is pinned to http://localhost:3000 only -- an
        untrusted origin should get no Access-Control-Allow-Origin back.
        """
        resp = client.options(
            self.ENDPOINT,
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin != "https://evil.example.com"
