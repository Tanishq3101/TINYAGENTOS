"""
tests/e2e/test_complete_flow.py

Day 21-23 deliverable (Integration Testing & Quality Assurance).

CONTRACT: CONFIRMED, NOT GUESSED (revised from an earlier draft)
------------------------------------------------------------------
An earlier version of this file followed the 30-day plan's Day 11-15
template verbatim, which turned out wrong on several real details.
tests/integration/test_deployment_smoke.py (real, already in this repo,
exercised against a live container) confirms the actual contract:

    | Plan template assumed  | Actually is                          |
    |-------------------------|--------------------------------------|
    | GET /health              | GET /api/v1/health (bare /health 404s)|
    | status == "completed"    | status == "success"                   |
    | body["results"]          | body["result"]   (singular)           |
    | bad task_type -> 400/422 | bad task_type -> 500 (current, not    |
    |                          | necessarily ideal -- see note below)  |

This file uses FastAPI's TestClient (in-process, no real container/
Docker needed) -- complementary to test_deployment_smoke.py, which
hits a live container over real HTTP with `requests`. Keep both: this
one is fast enough to run on every commit; that one only after
`docker compose up`.

API KEY
-------
Same convention as test_deployment_smoke.py: read from
TINYAGENTOS_TEST_API_KEY, falling back to "sk-test" for whatever
seeds that value in CI/Docker. See that file's docstring for the full
rationale on not hardcoding a credential in committed test source.

KNOWN CURRENT-BEHAVIOR CAVEAT
------------------------------------
test_unsupported_task_type_is_rejected below asserts 500, matching
test_deployment_smoke.py's documented current behavior (routes.py's
create_task doesn't catch InvalidTaskInputError separately from
generic exceptions). This is flagged, not fixed, in both files --
proper REST semantics would return 400/422 for client input errors.
If you fix routes.py to catch InvalidTaskInputError specifically,
update BOTH this assertion and the matching one in
test_deployment_smoke.py together, or they'll silently drift apart.

WHAT THIS DOES NOT COVER
-----------------------------
No live LLM calls are mocked -- api.app.app is expected to wire in the
real Orchestrator/LLMRuntime singleton, so these tests exercise the
full HTTP -> orchestrator -> agents -> LLMRuntime path for real, which
means they are SLOW (a real full_pipeline run is 3 serialized
generate() calls -- see EXECUTE_TIMEOUT_SECONDS in
test_deployment_smoke.py for the measured latency envelope). Keep
this suite small and deliberately thin -- fast, mocked-LLM tests
belong in tests/unit/, not here.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


class APIContract:
    """Single source of truth for endpoint paths / header names /
    response-shape keys, confirmed against test_deployment_smoke.py.
    If routes.py's contract changes, update here once instead of
    hunting through every test."""

    TASKS = "/api/v1/tasks"
    TASK_DETAIL = "/api/v1/tasks/{task_id}"
    TASK_EXECUTE = "/api/v1/tasks/{task_id}/execute"
    HEALTH = "/api/v1/health"
    API_KEY_HEADER = "X-API-Key"
    TEST_API_KEY = os.getenv("TINYAGENTOS_TEST_API_KEY", "sk-test")
    STATUS_SUCCESS = "success"
    RESULT_KEY = "result"  # singular, not "results"


@pytest.fixture(scope="module")
def client():
    from api.app import app  # deferred import: only needed once api/app.py exists

    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {APIContract.API_KEY_HEADER: APIContract.TEST_API_KEY}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_check_does_not_require_auth(self, client):
        response = client.get(APIContract.HEALTH)
        assert response.status_code == 200
        body = response.json()
        assert body.get("status") == "healthy"
        assert "timestamp" in body

    def test_bare_health_path_404s(self, client):
        """Regression guard matching test_deployment_smoke.py's
        test_health_endpoint_wrong_path_404s -- the router prefix is
        /api/v1, not bare /health."""
        response = client.get("/health")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Auth enforcement (config.py: REQUIRE_AUTH defaults True)
# ---------------------------------------------------------------------------
class TestAuthEnforcement:
    def test_task_creation_without_api_key_is_rejected(self, client):
        response = client.post(
            APIContract.TASKS,
            json={"text": "Sample text", "task_type": "full_pipeline"},
        )
        assert response.status_code == 401

    def test_task_creation_with_malformed_api_key_is_rejected(self, client):
        response = client.post(
            APIContract.TASKS,
            json={"text": "Sample text", "task_type": "full_pipeline"},
            headers={APIContract.API_KEY_HEADER: "not-the-right-prefix"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Full create -> execute -> poll workflow
# ---------------------------------------------------------------------------
class TestE2EFlow:
    def test_complete_workflow(self, client, auth_headers):
        create_response = client.post(
            APIContract.TASKS,
            json={
                "text": "TinyAgentOS is a small-footprint agent runtime "
                "designed for CPU-bound local inference.",
                "task_type": "full_pipeline",
                "priority": 1,
            },
            headers=auth_headers,
        )
        assert create_response.status_code == 200
        task_id = create_response.json()["task_id"]

        execute_response = client.post(
            APIContract.TASK_EXECUTE.format(task_id=task_id),
            headers=auth_headers,
        )
        assert execute_response.status_code == 200
        body = execute_response.json()
        assert body["status"] == APIContract.STATUS_SUCCESS
        assert APIContract.RESULT_KEY in body

    def test_full_pipeline_result_has_summary_extraction_and_evaluation(self, client, auth_headers):
        create_response = client.post(
            APIContract.TASKS,
            json={
                "text": "A short piece of sample text for extraction and review.",
                "task_type": "full_pipeline",
                "priority": 1,
            },
            headers=auth_headers,
        )
        task_id = create_response.json()["task_id"]
        exec_response = client.post(
            APIContract.TASK_EXECUTE.format(task_id=task_id), headers=auth_headers
        )

        result = exec_response.json()[APIContract.RESULT_KEY]

        # These three keys are the orchestrator's _run_full_pipeline output --
        # summarizer + extractor run concurrently, critic runs after both.
        assert "summary" in result and isinstance(result["summary"], str)
        assert "extraction" in result
        for key in ("key_points", "entities", "sentiment", "topics"):
            assert key in result["extraction"]
        assert "evaluation" in result
        evaluation = result["evaluation"]
        assert "score" in evaluation
        assert evaluation["score"] is None or 0 <= evaluation["score"] <= 10

    def test_reexecuting_a_completed_task_is_idempotent(self, client, auth_headers):
        """orchestrator.execute_pipeline() returns cached results for an
        already-COMPLETED task rather than re-running inference."""
        create_response = client.post(
            APIContract.TASKS,
            json={"text": "Idempotency check text.", "task_type": "summarize", "priority": 1},
            headers=auth_headers,
        )
        task_id = create_response.json()["task_id"]

        first = client.post(
            APIContract.TASK_EXECUTE.format(task_id=task_id), headers=auth_headers
        ).json()
        second = client.post(
            APIContract.TASK_EXECUTE.format(task_id=task_id), headers=auth_headers
        ).json()

        assert first == second


# ---------------------------------------------------------------------------
# Input validation (orchestrator.create_task's real validation rules)
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_empty_text_is_rejected(self, client, auth_headers):
        response = client.post(
            APIContract.TASKS,
            json={"text": "   ", "task_type": "full_pipeline", "priority": 1},
            headers=auth_headers,
        )
        assert response.status_code in (400, 422, 500)  # see module docstring caveat

    def test_unsupported_task_type_is_rejected(self, client, auth_headers):
        """Matches test_deployment_smoke.py's test_invalid_task_type_is_rejected:
        this currently returns 500, not 400/422 -- see module docstring."""
        response = client.post(
            APIContract.TASKS,
            json={"text": "Some text", "task_type": "not_a_real_task_type", "priority": 1},
            headers=auth_headers,
        )
        assert response.status_code == 500

    def test_unknown_task_id_returns_404(self, client, auth_headers):
        response = client.get(
            APIContract.TASK_DETAIL.format(task_id="00000000-0000-0000-0000-000000000000"),
            headers=auth_headers,
        )
        assert response.status_code == 404
