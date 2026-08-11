"""
tests/unit/test_stall_watchdog.py

Tests for infrastructure/stall_watchdog.py (Day 20).

Scope: the registry/threshold/flagging logic via check_once() (called
directly, not via the background thread) for determinism, plus
track_call()'s registration/cleanup and histogram-recording behavior.
The background thread itself (start/stop) gets a light smoke test only
-- timing-based thread assertions are inherently flaky and check_once()
already covers the actual detection logic it calls on each tick.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from infrastructure.stall_watchdog import StallWatchdog


# ---------------------------------------------------------------------------
# check_once — stall detection logic
# ---------------------------------------------------------------------------
class TestCheckOnce:
    def test_no_active_calls_records_nothing(self):
        tracker = MagicMock()
        watchdog = StallWatchdog(error_tracker=tracker, stall_threshold_seconds=10.0)

        watchdog.check_once()

        tracker.record_error.assert_not_called()

    def test_call_under_threshold_not_flagged(self):
        tracker = MagicMock()
        watchdog = StallWatchdog(error_tracker=tracker, stall_threshold_seconds=100.0)
        watchdog._register("call-1", "summarizer")

        watchdog.check_once()

        tracker.record_error.assert_not_called()

    def test_call_over_threshold_is_flagged(self):
        tracker = MagicMock()
        watchdog = StallWatchdog(error_tracker=tracker, stall_threshold_seconds=0.0)
        watchdog._register("call-1", "summarizer")
        # start_time defaults to time.time() at registration; with a
        # threshold of 0.0, any elapsed time at all trips it.
        time.sleep(0.01)

        watchdog.check_once()

        tracker.record_error.assert_called_once()
        _, kwargs = tracker.record_error.call_args
        assert kwargs["context"]["agent_name"] == "summarizer"
        assert kwargs["severity"] == "warning"
        assert kwargs["is_first_call"] is False

    def test_same_stalled_call_not_reflagged_on_repeated_checks(self):
        tracker = MagicMock()
        watchdog = StallWatchdog(error_tracker=tracker, stall_threshold_seconds=0.0)
        watchdog._register("call-1", "summarizer")
        time.sleep(0.01)

        watchdog.check_once()
        watchdog.check_once()
        watchdog.check_once()

        assert tracker.record_error.call_count == 1

    def test_no_error_tracker_does_not_raise(self):
        watchdog = StallWatchdog(error_tracker=None, stall_threshold_seconds=0.0)
        watchdog._register("call-1", "summarizer")
        time.sleep(0.01)

        watchdog.check_once()  # should not raise

    def test_unregistering_before_check_prevents_flag(self):
        tracker = MagicMock()
        watchdog = StallWatchdog(error_tracker=tracker, stall_threshold_seconds=0.0)
        watchdog._register("call-1", "summarizer")
        watchdog._unregister("call-1")
        time.sleep(0.01)

        watchdog.check_once()

        tracker.record_error.assert_not_called()


# ---------------------------------------------------------------------------
# track_call — context manager behavior
# ---------------------------------------------------------------------------
class TestTrackCall:
    def test_registers_and_unregisters_around_the_block(self):
        watchdog = StallWatchdog()

        with watchdog.track_call("extractor"):
            assert len(watchdog._active_calls) == 1

        assert len(watchdog._active_calls) == 0

    def test_unregisters_even_if_block_raises(self):
        watchdog = StallWatchdog()

        with pytest.raises(ValueError):
            with watchdog.track_call("extractor"):
                raise ValueError("generate() failed")

        assert len(watchdog._active_calls) == 0

    def test_records_duration_to_histogram_on_success(self, monkeypatch):
        import infrastructure.stall_watchdog as sw

        fake_record = MagicMock()
        monkeypatch.setattr(sw, "record_llm_call", fake_record)
        watchdog = StallWatchdog()

        with watchdog.track_call("critic"):
            pass

        fake_record.assert_called_once()
        args, kwargs = fake_record.call_args
        assert kwargs["agent_name"] == "critic"
        assert kwargs["duration_seconds"] >= 0.0

    def test_records_duration_even_on_failure(self, monkeypatch):
        import infrastructure.stall_watchdog as sw

        fake_record = MagicMock()
        monkeypatch.setattr(sw, "record_llm_call", fake_record)
        watchdog = StallWatchdog()

        with pytest.raises(ValueError):
            with watchdog.track_call("critic"):
                raise ValueError("boom")

        fake_record.assert_called_once()


# ---------------------------------------------------------------------------
# Lifecycle (start/stop) — light smoke test, not timing-precise
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_start_then_stop_does_not_raise(self):
        watchdog = StallWatchdog(check_interval_seconds=0.05)
        watchdog.start()
        time.sleep(0.12)
        watchdog.stop(timeout=1.0)
        assert watchdog._thread is None

    def test_starting_twice_is_a_noop(self):
        watchdog = StallWatchdog(check_interval_seconds=0.05)
        watchdog.start()
        first_thread = watchdog._thread
        watchdog.start()
        assert watchdog._thread is first_thread
        watchdog.stop()

    def test_context_manager_starts_and_stops(self):
        watchdog = StallWatchdog(check_interval_seconds=0.05)
        with watchdog:
            assert watchdog._thread is not None
            assert watchdog._thread.is_alive()
        assert watchdog._thread is None


# ---------------------------------------------------------------------------
# Module-level default watchdog + track_call() convenience wrapper
# ---------------------------------------------------------------------------
class TestModuleLevelDefault:
    def test_configure_default_watchdog_replaces_instance(self):
        import infrastructure.stall_watchdog as sw

        tracker = MagicMock()
        watchdog = sw.configure_default_watchdog(error_tracker=tracker, start=False)

        assert sw._default_watchdog is watchdog
        assert watchdog.error_tracker is tracker

    def test_track_call_convenience_uses_default_instance(self, monkeypatch):
        import infrastructure.stall_watchdog as sw

        fake_record = MagicMock()
        monkeypatch.setattr(sw, "record_llm_call", fake_record)
        sw.configure_default_watchdog(start=False)

        with sw.track_call("summarizer"):
            pass

        fake_record.assert_called_once()
