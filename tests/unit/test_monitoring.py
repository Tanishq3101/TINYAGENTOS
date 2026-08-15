"""
tests/unit/test_monitoring.py

Tests for infrastructure/monitoring.py (Day 20, post-trim).

Scope: the module now exposes exactly one live collector
(llm_call_latency) and one function (record_llm_call). The old
task_counter / active_tasks / pipeline_latency / memory_usage /
track_task_execution() / sample_memory_usage() surface was removed
from monitoring.py (see its module docstring) because nothing ever
called it and it duplicated infrastructure/prometheus_metrics.py's
real, live collectors (TASKS_TOTAL, ACTIVE_TASKS,
TASK_DURATION_SECONDS, AGENT_STEP_DURATION_SECONDS). Those names are
intentionally NOT tested here anymore -- coverage for them belongs to
prometheus_metrics.py / test_prometheus_metrics.py, not this file.

Runs correctly whether or not prometheus_client is actually installed
(monitoring.py falls back to a _NoOpMetric), mirroring the
psutil-optional pattern used elsewhere in this project.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import infrastructure.monitoring as monitoring


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

    def test_observes_duration_unchanged(self, monkeypatch):
        fake_hist = MagicMock()
        monkeypatch.setattr(monitoring, "llm_call_latency", fake_hist)

        monitoring.record_llm_call("extractor", 42.0)

        fake_hist.labels.assert_called_once_with(agent_name="extractor")
        fake_hist.labels.return_value.observe.assert_called_once_with(42.0)


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