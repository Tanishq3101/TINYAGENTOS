"""
tests/unit/test_monitoring.py

Tests for infrastructure/monitoring.py (Day 20).

Scope: metric wiring and the non-Prometheus helper logic
(track_task_execution's status/duration bookkeeping, record_llm_call,
sample_memory_usage). Does NOT assert on Prometheus's own internals
(bucket math, exposition format) -- that's the library's job, not
ours. Runs correctly whether or not prometheus_client is actually
installed, mirroring the psutil-optional pattern in
tests/test_benchmark_inference.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import infrastructure.monitoring as monitoring


# ---------------------------------------------------------------------------
# track_task_execution
# ---------------------------------------------------------------------------
class TestTrackTaskExecution:
    def test_success_increments_success_counter(self, monkeypatch):
        calls = []
        fake_counter = MagicMock()
        fake_counter.labels.side_effect = lambda **kw: calls.append(kw) or fake_counter
        monkeypatch.setattr(monitoring, "task_counter", fake_counter)
        monkeypatch.setattr(monitoring, "active_tasks", MagicMock())
        monkeypatch.setattr(monitoring, "pipeline_latency", MagicMock())

        with monitoring.track_task_execution("task-1"):
            pass

        assert calls == [{"status": "success"}]
        fake_counter.inc.assert_called_once()

    def test_exception_increments_error_counter_and_reraises(self, monkeypatch):
        calls = []
        fake_counter = MagicMock()
        fake_counter.labels.side_effect = lambda **kw: calls.append(kw) or fake_counter
        monkeypatch.setattr(monitoring, "task_counter", fake_counter)
        monkeypatch.setattr(monitoring, "active_tasks", MagicMock())
        monkeypatch.setattr(monitoring, "pipeline_latency", MagicMock())

        with pytest.raises(ValueError):
            with monitoring.track_task_execution("task-1"):
                raise ValueError("boom")

        assert calls == [{"status": "error"}]

    def test_active_tasks_incremented_then_decremented(self, monkeypatch):
        fake_gauge = MagicMock()
        monkeypatch.setattr(monitoring, "active_tasks", fake_gauge)
        monkeypatch.setattr(monitoring, "task_counter", MagicMock())
        monkeypatch.setattr(monitoring, "pipeline_latency", MagicMock())

        with monitoring.track_task_execution("task-1"):
            fake_gauge.inc.assert_called_once()
            fake_gauge.dec.assert_not_called()

        fake_gauge.dec.assert_called_once()

    def test_pipeline_latency_observed_with_nonnegative_duration(self, monkeypatch):
        fake_hist = MagicMock()
        monkeypatch.setattr(monitoring, "pipeline_latency", fake_hist)
        monkeypatch.setattr(monitoring, "active_tasks", MagicMock())
        monkeypatch.setattr(monitoring, "task_counter", MagicMock())

        with monitoring.track_task_execution("task-1"):
            pass

        assert fake_hist.observe.call_count == 1
        (duration,), _ = fake_hist.observe.call_args
        assert duration >= 0.0


# ---------------------------------------------------------------------------
# record_llm_call
# ---------------------------------------------------------------------------
class TestRecordLlmCall:
    def test_observes_on_correct_agent_label(self, monkeypatch):
        fake_hist = MagicMock()
        monkeypatch.setattr(monitoring, "llm_call_latency", fake_hist)

        monitoring.record_llm_call("summarizer", 1.23)

        fake_hist.labels.assert_called_once_with(agent_name="summarizer")
        fake_hist.labels.return_value.observe.assert_called_once_with(1.23)


# ---------------------------------------------------------------------------
# sample_memory_usage
# ---------------------------------------------------------------------------
class TestSampleMemoryUsage:
    def test_returns_none_when_psutil_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("no psutil")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert monitoring.sample_memory_usage() is None

    def test_sets_gauge_and_returns_value_when_psutil_available(self, monkeypatch):
        fake_gauge = MagicMock()
        monkeypatch.setattr(monitoring, "memory_usage", fake_gauge)

        fake_process = MagicMock()
        fake_process.memory_info.return_value.rss = 200 * 1024 * 1024
        fake_psutil_module = MagicMock()
        fake_psutil_module.Process.return_value = fake_process

        import sys

        monkeypatch.setitem(sys.modules, "psutil", fake_psutil_module)

        result = monitoring.sample_memory_usage()

        assert result == pytest.approx(200.0)
        fake_gauge.set.assert_called_once_with(pytest.approx(200.0))


# ---------------------------------------------------------------------------
# Bucket sanity — regression guard against reverting to the
# under-ranged placeholder buckets from the original plan template.
# ---------------------------------------------------------------------------
class TestLatencyBucketsCoverObservedRange:
    def test_buckets_extend_past_the_p99_outlier_from_optimization_md(self):
        # OPTIMIZATION.md: long-prompt p99 was 36188 ms (36.2s), called
        # out as a one-off spike. Buckets should still have resolution
        # above it, not just an open-ended +Inf catch-all.
        assert max(monitoring._LATENCY_BUCKETS) > 36.2

    def test_buckets_have_resolution_below_observed_medians(self):
        # Medians in OPTIMIZATION.md ranged ~4.8s-8.0s -- buckets should
        # bracket that range, not jump straight from 1s to 10s+.
        buckets = monitoring._LATENCY_BUCKETS
        assert any(4.0 <= b <= 9.0 for b in buckets)
