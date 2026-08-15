"""
tests/integration/test_deployment_smoke.py

Day 14-15 deployment smoke tests.

The 30-day plan has no test deliverable for Day 14-15 (Containerization &
Deployment) -- Day 13's suite covers app logic in isolation, but nothing
in the plan verifies the actual built container works end-to-end. This
file closes that gap.

Unlike Day 13's tests, these run against a LIVE, already-running
container -- they use `requests` over HTTP, not FastAPI's TestClient,
because the whole point is to catch things that only break once the
app is actually containerized (wrong healthcheck path, missing env
vars, parameter mismatches between layers that only surface on a real
request -- see: the `text=` vs `input_data=` bug this file's tests
would have caught immediately instead of needing a manual curl call).

Prerequisites:
    docker compose -f docker/docker-compose.yml --env-file .env up --build
    (wait for `docker inspect --format="{{.State.Health.Status}}"
    docker-tinyagentos-1` to report "healthy" before running this file --
    see test_container_is_healthy, which asserts that itself, but the
    LLM-dependent tests below it will just hang/timeout if you run this
    too early rather than giving you a clean failure)

API KEY (CHANGED)
------------------
Was previously a hardcoded `API_KEY = "sk-test"` module constant.
Changed to read from the TINYAGENTOS_TEST_API_KEY env var, falling
back to "sk-test" only so this file keeps working unmodified against
whatever container-startup seeding step provisions that value for the
Docker/CI path (not shown in this session -- check docker/ or
api/app.py's startup hook if you need to confirm what seeds it).

Never hardcode a real credential in a committed test file, even a
low-stakes dev-only one -- the env-var-with-fallback pattern here means:
  - CI/Docker path: unaffected, still gets "sk-test" with zero config
  - Local/other envs: override with your own issued key, no source edit
    required:
        export TINYAGENTOS_TEST_API_KEY="sk-<your real issued key>"

Run:
    pytest tests/integration/test_deployment_smoke.py -v -s

Note the `-s`: several tests print timing info (model load / inference
latency) that's useful to eyeball on every run, not just on failure.
"""

import json
import os
import subprocess
import time

import pytest
import requests

BASE_URL = "http://localhost:8000"
API_KEY = os.getenv("TINYAGENTOS_TEST_API_KEY", "sk-test")
HEADERS = {"X-API-Key": API_KEY}
CONTAINER_NAME = "docker-tinyagentos-1"

# Generous -- CI/slower machines may see container-internal model load
# take longer than the ~26s measured locally in dev. This is a test
# timeout, not the Docker HEALTHCHECK start-period (that's set
# separately in the Dockerfile and asserted against in
# test_healthcheck_start_period_is_configured below).
#
# RAISED 90 -> 180 (Day 18-19): scripts/benchmark_inference.py measured
# real per-call generate() latency with p99 up to ~19.7s and high
# variance (short-prompt max 13.9s, medium 16.3s, long 19.7s across
# n=30 -- see docs/inference_benchmark_results.json). full_pipeline
# makes THREE serialized real inference calls per request (summarizer +
# extractor queued on llm_runtime.py's _inference_lock, then critic
# after) -- observed 48.3s on a passing run, but with per-call p99 that
# high, three calls landing anywhere near their tail can plausibly
# exceed 90s by chance alone, with no bug involved. 180s keeps this a
# real hang/deadlock detector -- a genuine bug still fails loudly --
# while giving three real-inference calls enough headroom that normal
# latency variance doesn't produce a false failure. Re-measure and
# adjust if the underlying model/hardware changes.
EXECUTE_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# Container-level checks (docker inspect, not HTTP)
# ---------------------------------------------------------------------------


def _docker_inspect(fmt: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", fmt, CONTAINER_NAME],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.fail(
            f"docker inspect failed -- is the container running? "
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def test_container_is_running_and_healthy():
    """Fails fast with a clear message if the container isn't up yet,
    instead of every HTTP test below timing out individually."""
    status = _docker_inspect("{{.State.Health.Status}}")
    assert status == "healthy", (
        f"Container health status is {status!r}, not 'healthy'. "
        f"Run `docker compose up --build` and wait before running these tests."
    )


def test_container_restart_count_is_zero():
    """A nonzero restart count means the app crashed at some point after
    boot -- worth catching even if it's currently up and healthy now."""
    count = _docker_inspect("{{.RestartCount}}")
    assert count == "0", (
        f"Container has restarted {count} time(s) -- check logs for a "
        f"crash (docker compose logs) even though it's healthy now."
    )


def test_healthcheck_start_period_is_configured():
    """Regression guard for the specific mistake made earlier this
    session: setting --start-period lower than actual model load time,
    which would make Docker mark the container unhealthy while it's
    still legitimately loading the GGUF model.

    Uses --format='{{json ...}}' + json.loads rather than the plain Go
    template string form -- on some Docker versions/platforms the plain
    template prints a human-readable duration ("45s") instead of raw
    nanoseconds, which int() can't parse. The JSON output reliably gives
    nanoseconds as a plain integer regardless of that formatting quirk.
    """
    raw = _docker_inspect("{{json .Config.Healthcheck.StartPeriod}}")
    start_period_ns = json.loads(raw)
    start_period_s = start_period_ns / 1_000_000_000
    # 20s floor -- below this is almost certainly too tight given the
    # ~26s measured load time in this container on this machine.
    assert start_period_s >= 20, (
        f"HEALTHCHECK start-period is {start_period_s:.0f}s, which is "
        f"suspiciously low given measured model load time (~26s locally). "
        f"Re-measure and confirm before lowering this."
    )


# ---------------------------------------------------------------------------
# HTTP-level checks
# ---------------------------------------------------------------------------


def test_health_endpoint_path_and_shape():
    """Regression guard for the doc's boilerplate using /health instead
    of the real router prefix, /api/v1/health."""
    resp = requests.get(f"{BASE_URL}/api/v1/health", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "healthy"
    assert "timestamp" in body


def test_health_endpoint_wrong_path_404s():
    """If this ever starts returning 200, something changed the router
    prefix and every other path-dependent config (Dockerfile HEALTHCHECK,
    k8s probes) needs updating too."""
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    assert resp.status_code == 404


def test_missing_api_key_is_rejected():
    resp = requests.post(
        f"{BASE_URL}/api/v1/tasks",
        json={"text": "test", "task_type": "summarize", "priority": 1},
        timeout=5,
    )
    assert resp.status_code == 401


def test_malformed_api_key_is_rejected():
    resp = requests.post(
        f"{BASE_URL}/api/v1/tasks",
        headers={"X-API-Key": "not-the-right-prefix"},
        json={"text": "test", "task_type": "summarize", "priority": 1},
        timeout=5,
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Full request/response cycle -- these are the ones that actually
# exercise the orchestrator singleton + real LLM inference, and would
# have caught the `text=` / `input_data=` parameter mismatch bug
# immediately instead of requiring a manual curl round-trip to find.
# ---------------------------------------------------------------------------


def _create_and_execute(task_type: str, text: str) -> dict:
    create_resp = requests.post(
        f"{BASE_URL}/api/v1/tasks",
        headers=HEADERS,
        json={"text": text, "task_type": task_type, "priority": 1},
        timeout=10,
    )
    assert create_resp.status_code == 200, (
        f"Task creation failed ({create_resp.status_code}): {create_resp.text}\n"
        f"If this is a generic 500 with 'Failed to create task', check "
        f"container logs -- routes.py and orchestrator.py's create_task "
        f"parameter names may be mismatched again (text= vs input_data=)."
    )
    task_id = create_resp.json()["task_id"]

    t0 = time.time()
    exec_resp = requests.post(
        f"{BASE_URL}/api/v1/tasks/{task_id}/execute",
        headers=HEADERS,
        timeout=EXECUTE_TIMEOUT_SECONDS,
    )
    elapsed = time.time() - t0
    print(f"\n[{task_type}] execute took {elapsed:.1f}s")

    assert (
        exec_resp.status_code == 200
    ), f"Task execution failed ({exec_resp.status_code}): {exec_resp.text}"
    return exec_resp.json()


def test_summarize_only_pipeline():
    body = _create_and_execute(
        "summarize",
        "The quick brown fox jumps over the lazy dog. "
        "This is a test sentence for the summarizer agent.",
    )
    assert body["status"] == "success"
    summary = body["result"]["summary"]
    assert isinstance(summary, str) and len(summary) > 0
    print(f"Summary: {summary}")


@pytest.mark.slow
def test_full_pipeline_exercises_all_three_agents():
    """The important one: this is the only test path that runs
    summarizer + extractor concurrently (through the orchestrator's
    thread pool) and then critic -- exercising llm_runtime.py's
    _inference_lock under real concurrent load, not just in theory.

    Marked slow: this is real, unmocked, serialized inference (three
    generate() calls) against the single shared LLMRuntime lock. Run
    in the same session as 280+ other tests, it inherits whatever lock
    contention those left behind -- see pyproject.toml's marker
    registration comment for the 2026-08-15 flake this caused. Run
    separately via `pytest -m slow` against a freshly-started
    container, not as part of the default `pytest -m "not slow"` loop.
    """
    body = _create_and_execute(
        "full_pipeline",
        "The quick brown fox jumps over the lazy dog. This is a test "
        "sentence used to evaluate multiple AI agents working together.",
    )
    assert body["status"] == "success"
    result = body["result"]

    assert "summary" in result and isinstance(result["summary"], str)

    assert "extraction" in result
    extraction = result["extraction"]
    for key in ("key_points", "entities", "sentiment", "topics"):
        assert key in extraction, f"extraction missing expected key: {key}"

    assert "evaluation" in result
    evaluation = result["evaluation"]
    assert "score" in evaluation
    assert evaluation["score"] is None or 0 <= evaluation["score"] <= 10
    for key in ("strengths", "weaknesses", "recommendations"):
        assert isinstance(
            evaluation.get(key), list
        ), f"evaluation.{key} should be a list -- got {type(evaluation.get(key))}"

    print(f"Score: {evaluation['score']}")
    print(f"Strengths: {evaluation['strengths']}")


def test_invalid_task_type_is_rejected():
    resp = requests.post(
        f"{BASE_URL}/api/v1/tasks",
        headers=HEADERS,
        json={"text": "test", "task_type": "not_a_real_type", "priority": 1},
        timeout=5,
    )
    # InvalidTaskInputError isn't caught separately in routes.py's
    # create_task -- it falls into the generic `except Exception` and
    # returns 500, not 400/422. Documenting the CURRENT behavior here,
    # not necessarily the ideal behavior -- worth revisiting: a 4xx would
    # be more correct REST semantics for client-side input validation
    # failures. Flagged again in docs/DAY21_23_INTEGRATION_GUIDE.md.
    assert resp.status_code == 500


@pytest.mark.slow
def test_full_pipeline_evaluation_lists_are_clean_phrases():
    """Regression guard for the critic prompt/parsing fix -- if this
    starts failing, the model drifted back to writing full sentences
    with 'and' joiners instead of comma-separated phrases, or the
    _split_list cleanup regressed.

    Marked slow -- see test_full_pipeline_exercises_all_three_agents's
    docstring above for why."""
    body = _create_and_execute(
        "full_pipeline",
        "Artificial intelligence is transforming how software gets built, "
        "tested, and deployed across the industry.",
    )
    evaluation = body["result"]["evaluation"]
    for key in ("strengths", "weaknesses", "recommendations"):
        for item in evaluation[key]:
            assert not item.lower().startswith(
                "and "
            ), f"evaluation.{key} item still has a leading 'and ': {item!r}"
