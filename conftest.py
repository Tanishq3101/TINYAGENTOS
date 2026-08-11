"""
Root conftest.py

Skips tests that require a real loaded LLM model to pass, across two
categories:

1. Whole files (tests/unit/test_day3.py through test_day9.py) that
   directly construct LLMRuntime()/BaseAgent() and assert on real model
   output. Those tests predate core/llm_runtime.py's
   TINYAGENT_SKIP_LLM_LOAD / missing-MODEL_PATH graceful-degradation
   path -- see LLMRuntime.__init__ and ModelNotLoadedError -- so they
   have no skip logic of their own.
2. Individual tests, in otherwise-mixed files, that call .../execute
   against full_pipeline or summarize and so need real inference:
   test_day10.py's one true end-to-end test, plus specific tests in
   tests/e2e/test_complete_flow.py and
   tests/integration/test_deployment_smoke.py -- see the comment above
   _REQUIRES_REAL_MODEL_TESTS below for the full list and why each one
   is there.

Rather than edit each test file to add its own skipif, this collection
hook does it centrally, in one place, and only when it's actually
needed: if LLMRuntime().is_loaded is True (a real model IS available --
e.g. a future model-backed integration CI job, or a local dev machine
with the real .gguf downloaded), nothing is skipped and these tests run
and are verified normally, same as before this file existed.

Constructing LLMRuntime() here is cheap and side-effect-free either
way: it's a singleton (see core/llm_runtime.py's __new__), so this just
returns/creates the same instance api/app.py's lifespan (or pytest's
own test collection) would construct anyway -- it does not download or
duplicate anything.
"""

import pytest

from core.llm_runtime import LLMRuntime

# Whole files that only make sense against a real model.
_REQUIRES_REAL_MODEL_FILES = {
    "tests/unit/test_day3.py",
    "tests/unit/test_day4.py",
    "tests/unit/test_day5.py",
    "tests/unit/test_day6.py",
    "tests/unit/test_day7.py",
    "tests/unit/test_day8.py",
    "tests/unit/test_day9.py",
}

# test_day10.py is otherwise a real, already-mocked orchestrator suite --
# only this one test in it needs a real model, not the whole file.
#
# tests/e2e/test_complete_flow.py and tests/integration/test_deployment_smoke.py
# are each otherwise-mixed files (health checks, auth rejection, container
# inspection, create-without-execute all correctly run without a model) --
# only the specific tests below call .../execute against full_pipeline or
# summarize, so only those need to be skipped, not the whole file.
# test_reexecuting_a_completed_task_is_idempotent is included even though it
# doesn't assert on real model output: both calls just get the same
# ModelNotLoadedError response body, which is a false-positive pass, not a
# real idempotency check.
#
# NOTE on test_deployment_smoke.py specifically: unlike test_complete_flow.py
# (which shares this pytest process's LLMRuntime singleton via
# TestClient(app)), test_deployment_smoke.py makes real HTTP calls to a
# separately-running Docker container (see BASE_URL in that file). The
# LLMRuntime().is_loaded check above reflects THIS process's environment,
# not the container's -- the skip decision here is only correct if
# TINYAGENT_SKIP_LLM_LOAD (and MODEL_PATH) are set identically for the
# pytest process and the container. If those two ever diverge (e.g. running
# this file locally against a container that has a real model loaded, with
# the env var unset in your shell), this skip logic will silently make the
# wrong call. Worth revisiting if that ever causes a false skip/run.
_REQUIRES_REAL_MODEL_TESTS = {
    "tests/unit/test_day10.py::test_real_orchestrator_end_to_end",
    "tests/e2e/test_complete_flow.py::TestE2EFlow::test_complete_workflow",
    "tests/e2e/test_complete_flow.py::TestE2EFlow::test_full_pipeline_result_has_summary_extraction_and_evaluation",
    "tests/e2e/test_complete_flow.py::TestE2EFlow::test_reexecuting_a_completed_task_is_idempotent",
    "tests/integration/test_deployment_smoke.py::test_summarize_only_pipeline",
    "tests/integration/test_deployment_smoke.py::test_full_pipeline_exercises_all_three_agents",
    "tests/integration/test_deployment_smoke.py::test_full_pipeline_evaluation_lists_are_clean_phrases",
}

_SKIP_REASON = (
    "requires a real loaded LLM model (TINYAGENT_SKIP_LLM_LOAD=1 was set, "
    "or MODEL_PATH did not point at a real .gguf) -- see "
    "core/llm_runtime.py's LLMRuntime.__init__ and ModelNotLoadedError"
)


def pytest_collection_modifyitems(items: list) -> None:
    if LLMRuntime().is_loaded:
        return  # real model available -- run everything as normal

    skip_marker = pytest.mark.skip(reason=_SKIP_REASON)

    for item in items:
        # item.location[0] is the test's file path relative to rootdir,
        # e.g. "tests/unit/test_day3.py" -- matches how pytest already
        # reports these paths in its own output, so no path-manipulation
        # needed to compare against the sets above.
        rel_path = item.location[0].replace("\\", "/")
        nodeid = item.nodeid.replace("\\", "/")

        if rel_path in _REQUIRES_REAL_MODEL_FILES or nodeid in _REQUIRES_REAL_MODEL_TESTS:
            item.add_marker(skip_marker)