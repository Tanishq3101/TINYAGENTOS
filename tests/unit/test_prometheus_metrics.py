"""
tests/unit/test_prometheus_metrics.py

Tests for infrastructure/prometheus_metrics.py.

Scope: this is the module that's actually wired into the app --
TASKS_TOTAL, ACTIVE_TASKS, TASK_DURATION_SECONDS, and
AGENT_STEP_DURATION_SECONDS are all updated directly from
core/orchestrator.py; AGENT_STEP_ERRORS_TOTAL and AUTH_FAILURES_TOTAL
from routes.py. These are real prometheus_client collectors (unlike
infrastructure/monitoring.py, which falls back to a _NoOpMetric when
prometheus_client isn't installed) -- prometheus_client is a hard
import at the top of this module, so these tests require it to be
installed.

Collectors are registered on the global default REGISTRY at import
time, and that registry is process-wide -- re-importing the module
across test runs would try to register the same metric names twice
and raise ValueError. So instead of constructing new collectors per
test, every test here reads/mutates the single already-imported
module-level collectors and resets the ones it touched afterward.
"""

from __future__ import annotations

import os

import pytest

import infrastructure.prometheus_metrics as pm


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _gauge_value(gauge) -> float:
    return gauge._value.get()


# ---------------------------------------------------------------------------
# TASKS_TOTAL
# ---------------------------------------------------------------------------
class TestTasksTotal:
    def test_increments_for_given_task_type_and_status(self):
        before = _counter_value(
            pm.TASKS_TOTAL, task_type="full_pipeline", status="completed"
        )
        pm.TASKS_TOTAL.labels(task_type="full_pipeline", status="completed").inc()
        after = _counter_value(
            pm.TASKS_TOTAL, task_type="full_pipeline", status="completed"
        )
        assert after == before + 1

    def test_completed_and_failed_are_independent_series(self):
        pm.TASKS_TOTAL.labels(task_type="unit_test_probe", status="completed").inc()
        completed = _counter_value(
            pm.TASKS_TOTAL, task_type="unit_test_probe", status="completed"
        )
        failed = _counter_value(
            pm.TASKS_TOTAL, task_type="unit_test_probe", status="failed"
        )
        assert completed >= 1
        assert failed == 0


# ---------------------------------------------------------------------------
# ACTIVE_TASKS
# ---------------------------------------------------------------------------
class TestActiveTasks:
    def test_inc_then_dec_returns_to_baseline(self):
        baseline = _gauge_value(pm.ACTIVE_TASKS)
        pm.ACTIVE_TASKS.inc()
        assert _gauge_value(pm.ACTIVE_TASKS) == baseline + 1
        pm.ACTIVE_TASKS.dec()
        assert _gauge_value(pm.ACTIVE_TASKS) == baseline


# ---------------------------------------------------------------------------
# TASK_DURATION_SECONDS / AGENT_STEP_DURATION_SECONDS
# ---------------------------------------------------------------------------
class TestDurationHistograms:
    def test_task_duration_observe_does_not_raise(self):
        pm.TASK_DURATION_SECONDS.labels(task_type="full_pipeline").observe(4.2)

    def test_agent_step_duration_observe_does_not_raise(self):
        pm.AGENT_STEP_DURATION_SECONDS.labels(agent_name="summarizer").observe(0.8)

    def test_task_duration_buckets_are_ascending(self):
        buckets = pm.TASK_DURATION_SECONDS._upper_bounds
        assert list(buckets) == sorted(buckets)

    def test_agent_step_duration_buckets_are_ascending(self):
        buckets = pm.AGENT_STEP_DURATION_SECONDS._upper_bounds
        assert list(buckets) == sorted(buckets)


# ---------------------------------------------------------------------------
# AGENT_STEP_ERRORS_TOTAL / AUTH_FAILURES_TOTAL
# ---------------------------------------------------------------------------
class TestErrorCounters:
    def test_agent_step_errors_increments_for_agent(self):
        before = _counter_value(pm.AGENT_STEP_ERRORS_TOTAL, agent_name="critic")
        pm.AGENT_STEP_ERRORS_TOTAL.labels(agent_name="critic").inc()
        after = _counter_value(pm.AGENT_STEP_ERRORS_TOTAL, agent_name="critic")
        assert after == before + 1

    def test_auth_failures_increments(self):
        before = pm.AUTH_FAILURES_TOTAL._value.get()
        pm.AUTH_FAILURES_TOTAL.inc()
        after = pm.AUTH_FAILURES_TOTAL._value.get()
        assert after == before + 1


# ---------------------------------------------------------------------------
# render_metrics / _get_registry
# ---------------------------------------------------------------------------
class TestRenderMetrics:
    def test_returns_bytes_and_content_type(self):
        body, content_type = pm.render_metrics()
        assert isinstance(body, bytes)
        assert content_type == pm.CONTENT_TYPE_LATEST

    def test_output_includes_known_metric_names(self):
        pm.TASKS_TOTAL.labels(task_type="render_probe", status="completed").inc()
        body, _ = pm.render_metrics()
        text = body.decode("utf-8")
        assert "tinyagentos_tasks_total" in text
        assert "tinyagentos_active_tasks" in text

    def test_uses_default_registry_when_no_multiproc_dir(self, monkeypatch):
        # See the note in test_uses_multiprocess_collector_when_dir_set:
        # _get_registry() branches on the module-level _MULTIPROC_DIR
        # cached at import time, not a live env lookup, so the
        # attribute (not just the env var) needs to be patched to
        # actually exercise both branches.
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        monkeypatch.setattr(pm, "_MULTIPROC_DIR", None)
        registry = pm._get_registry()
        assert registry is pm.REGISTRY

    def test_uses_multiprocess_collector_when_dir_set(self, monkeypatch, tmp_path):
        # _MULTIPROC_DIR is read from os.environ once at *module import
        # time* and cached (see prometheus_metrics.py's module body:
        # `_MULTIPROC_DIR = os.environ.get(...)`), and _get_registry()
        # branches on that cached module attribute, not on a live
        # os.environ lookup. So monkeypatch.setenv() alone has no
        # effect here -- the module is already imported by the time
        # this test runs, so the env var change is invisible to
        # _get_registry(). Patch the cached attribute directly instead
        # (setenv left in too, so the test still reflects realistic
        # process state / doesn't leak a real env var to other tests).
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
        monkeypatch.setattr(pm, "_MULTIPROC_DIR", str(tmp_path))
        called = {}

        class FakeMultiProcessCollector:
            def __init__(self, registry):
                called["registry"] = registry

        monkeypatch.setattr(
            pm.multiprocess, "MultiProcessCollector", FakeMultiProcessCollector
        )

        registry = pm._get_registry()
        assert registry is not pm.REGISTRY
        assert "registry" in called