# tests/load/loadtest.py
#
# Day 21-23 deliverable (Integration Testing & Quality Assurance) --
# previously flagged as an unbuilt gap, built now against the CONFIRMED
# real API contract established in tests/e2e/test_complete_flow.py, not
# the plan's original draft, which was wrong on several details it
# shares with that draft's already-corrected mistakes:
#   - bare /health          -> real path is /api/v1/health
#   - unguarded task_id     -> a failed/slow create shouldn't crash the
#                              load test client, just count as a failure
#   - no priority field     -> included, matches TaskRequest's schema
#
# USAGE
# -----
#   locust -f tests/load/loadtest.py --host=http://localhost:8000
#
# Then open http://localhost:8089 to configure user count / spawn rate
# and start the run from the web UI. Or headless:
#   locust -f tests/load/loadtest.py --host=http://localhost:8000 \
#       --users 20 --spawn-rate 2 --run-time 5m --headless
#
# API KEY
# -------
# Same convention as tests/e2e/test_complete_flow.py and
# curl_e2e_tests.ps1: reads TINYAGENTOS_TEST_API_KEY, falls back to
# "sk-test" (which will 401 against a real DB-backed server unless
# that literal string happens to be an issued, unrevoked key -- see
# API_KEY_STORAGE_RUNBOOK.md to issue a real one).
#
# A NOTE ON WHAT THIS IS ACTUALLY LOAD-TESTING
# ---------------------------------------------
# full_pipeline tasks do real, unmocked LLM inference (per
# test_complete_flow.py's own docstring) -- each execute() call is
# several serialized generate() calls. Running many concurrent Locust
# users against full_pipeline will saturate CPU/inference throughput
# fast, by design; that's a legitimate thing to measure, but don't
# mistake "the server fell over at 10 concurrent users" for a bug if
# you're running this against a single CPU-bound inference instance --
# compare against tests/performance/test_benchmarks.py's own
# single-call latency numbers first to know what "expected" looks like
# before treating a load-test ceiling as a regression.

import os
import random

from locust import HttpUser, task, between

API_KEY = os.getenv("TINYAGENTOS_TEST_API_KEY", "sk-test")

SAMPLE_TEXTS = [
    "TinyAgentOS is a small-footprint agent runtime designed for CPU-bound local inference.",
    "A short piece of sample text for extraction and review.",
    "Load testing helps surface latency and throughput ceilings before real users find them.",
    "The quick brown fox jumps over the lazy dog, repeatedly, for benchmark padding purposes.",
]

TASK_TYPES = ["summarize", "extract", "full_pipeline"]


class TinyAgentOSUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    @task(3)
    def health_check(self):
        """Frequent, cheap, no-auth-required baseline traffic -- lets
        you see health-endpoint latency stay flat even while task
        traffic saturates the inference path, which is a useful signal
        that the process itself isn't wedged, just busy."""
        self.client.get("/api/v1/health", name="/api/v1/health")

    @task(1)
    def create_and_execute_summarize_or_extract(self):
        """Cheaper single-agent tasks -- higher volume, lower latency
        than full_pipeline. Weighted more heavily below via task(1) vs
        create_and_execute_full_pipeline's task(1) at 1/3 the frequency
        multiplier difference isn't dramatic on purpose: both paths are
        worth exercising under load, not just the expensive one."""
        task_type = random.choice(["summarize", "extract"])
        self._create_and_execute(task_type)

    @task(1)
    def create_and_execute_full_pipeline(self):
        """The expensive path -- three serialized real generate() calls
        per execution, per test_complete_flow.py's docstring. Expect
        this to dominate server-side load even at modest concurrency."""
        self._create_and_execute("full_pipeline")

    def _create_and_execute(self, task_type: str):
        text = random.choice(SAMPLE_TEXTS)

        with self.client.post(
            "/api/v1/tasks",
            json={"text": text, "task_type": task_type, "priority": random.randint(1, 10)},
            headers=self.headers,
            name="/api/v1/tasks [create]",
            catch_response=True,
        ) as create_response:
            if create_response.status_code != 200:
                create_response.failure(
                    f"create failed: {create_response.status_code} {create_response.text[:200]}"
                )
                return

            try:
                task_id = create_response.json()["task_id"]
            except (ValueError, KeyError) as exc:
                create_response.failure(f"no task_id in create response: {exc}")
                return

            create_response.success()

        with self.client.post(
            f"/api/v1/tasks/{task_id}/execute",
            headers=self.headers,
            name="/api/v1/tasks/[id]/execute",
            catch_response=True,
        ) as exec_response:
            if exec_response.status_code != 200:
                exec_response.failure(
                    f"execute failed: {exec_response.status_code} {exec_response.text[:200]}"
                )
                return

            body = exec_response.json()
            # Real contract: singular "result", status "success" --
            # not the plan draft's "results"/"completed".
            if body.get("status") != "success" or "result" not in body:
                exec_response.failure(f"unexpected response shape: {body}")
                return

            exec_response.success()
