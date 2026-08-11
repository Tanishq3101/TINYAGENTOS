"""
Root conftest.py

Skips the early milestone tests that directly construct LLMRuntime()/
BaseAgent() and assert on real model output (tests/unit/test_day3.py
through test_day9.py, and test_day10.py's one true end-to-end test).
Those tests predate core/llm_runtime.py's TINYAGENT_SKIP_LLM_LOAD /
missing-MODEL_PATH graceful-degradation path -- see LLMRuntime.__init__
and ModelNotLoadedError -- so they have no skip logic of their own.

Rather than edit eight individual test files to each add their own
skipif, this collection hook does it centrally, in one place, and only
when it's actually needed: if LLMRuntime().is_loaded is True (a real
model IS available -- e.g. a future model-backed integration CI job, or
a local dev machine with the real .gguf downloaded), nothing is skipped
and these tests run and are verified normally, same as before this file
existed.

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
_REQUIRES_REAL_MODEL_TESTS = {
    "tests/unit/test_day10.py::test_real_orchestrator_end_to_end",
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
