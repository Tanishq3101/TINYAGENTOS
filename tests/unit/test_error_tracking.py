"""
tests/unit/test_error_tracking.py

Tests for infrastructure/error_tracking.py (Day 20).

Scope: record_error()'s field construction, the is_first_call
tagging, the reserved-log-key collision guard, severity->logger-method
dispatch, and the two summary views. A stub logger (not the real
stdlib Logger) is used throughout so these tests assert on *what
ErrorTracker sends the logger*, not on logging's own internals.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from infrastructure.error_tracking import (
    ErrorTracker,
    _LOGGING_RESERVED_KEYS,
    _capture_system_context,
)


def _make_tracker():
    # spec=logging.Logger matters here: a bare MagicMock() auto-creates
    # *any* attribute you access (e.g. stub_logger.not_a_real_level),
    # which would make ErrorTracker's getattr(self.logger, severity)
    # "succeed" for made-up severities too and mask the real
    # fallback-to-.error() behavior. Constraining to the real Logger
    # API means unknown attributes raise AttributeError, same as a
    # real logging.Logger, so getattr(..., None) actually returns None
    # when it should.
    stub_logger = MagicMock(spec=logging.Logger)
    return ErrorTracker(logger=stub_logger), stub_logger


class TestRecordError:
    def test_returns_record_with_expected_fields(self):
        tracker, _ = _make_tracker()

        record = tracker.record_error(ValueError("boom"))

        assert record["type"] == "ValueError"
        assert record["message"] == "boom"
        assert record["severity"] == "error"
        assert "timestamp" in record
        assert "traceback" in record
        assert record["context"] == {}
        assert "is_first_call" not in record

    def test_appends_to_internal_errors_list(self):
        tracker, _ = _make_tracker()

        tracker.record_error(ValueError("one"))
        tracker.record_error(ValueError("two"))

        assert len(tracker.errors) == 2

    def test_context_is_stored_as_a_copy(self):
        tracker, _ = _make_tracker()
        context = {"agent_name": "summarizer"}

        record = tracker.record_error(ValueError("boom"), context=context)
        context["agent_name"] = "mutated"

        assert record["context"]["agent_name"] == "summarizer"

    def test_is_first_call_true_is_included(self):
        tracker, _ = _make_tracker()

        record = tracker.record_error(ValueError("boom"), is_first_call=True)

        assert record["is_first_call"] is True

    def test_is_first_call_false_is_included_not_omitted(self):
        # False is a meaningful value, not "unset" -- must not be
        # dropped the way None is.
        tracker, _ = _make_tracker()

        record = tracker.record_error(ValueError("boom"), is_first_call=False)

        assert record["is_first_call"] is False

    def test_is_first_call_none_is_omitted(self):
        tracker, _ = _make_tracker()

        record = tracker.record_error(ValueError("boom"), is_first_call=None)

        assert "is_first_call" not in record


class TestLoggerDispatch:
    def test_warning_severity_calls_logger_warning(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"), severity="warning")

        stub_logger.warning.assert_called_once()
        stub_logger.error.assert_not_called()

    def test_error_severity_calls_logger_error(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"), severity="error")

        stub_logger.error.assert_called_once()

    def test_unrecognized_severity_falls_back_to_error(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"), severity="not_a_real_level")

        stub_logger.error.assert_called_once()

    def test_message_passed_to_logger_includes_type_and_message(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"))

        (message,), _ = stub_logger.error.call_args
        assert "ValueError" in message
        assert "boom" in message

    def test_extra_dict_contains_prefixed_keys_not_raw_names(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"), context={"call_id": "abc"})

        _, kwargs = stub_logger.error.call_args
        extra = kwargs["extra"]
        assert "error_type" in extra
        assert "error_context" in extra
        assert "error_system" in extra
        # Raw stdlib-reserved names must never appear as extra keys.
        assert not (_LOGGING_RESERVED_KEYS & extra.keys())

    def test_extra_dict_includes_is_first_call_when_present(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"), is_first_call=True)

        _, kwargs = stub_logger.error.call_args
        assert kwargs["extra"]["is_first_call"] is True

    def test_extra_dict_omits_is_first_call_when_absent(self):
        tracker, stub_logger = _make_tracker()

        tracker.record_error(ValueError("boom"))

        _, kwargs = stub_logger.error.call_args
        assert "is_first_call" not in kwargs["extra"]

    def test_works_against_a_real_stdlib_logger_without_raising(self):
        # Regression guard for the exact bug this module was rewritten
        # to fix: the old version called logger.log_with_context(...),
        # which raises AttributeError against a plain logging.Logger.
        import logging

        real_logger = logging.getLogger("test_error_tracking_real_logger")
        tracker = ErrorTracker(logger=real_logger)

        # Must not raise.
        tracker.record_error(ValueError("boom"), severity="warning")


class TestGetErrorSummary:
    def test_empty_tracker_returns_zero_counts(self):
        tracker, _ = _make_tracker()

        summary = tracker.get_error_summary()

        assert summary["total_errors"] == 0
        assert summary["error_types"] == []
        assert summary["latest_errors"] == []

    def test_counts_and_types_reflect_recorded_errors(self):
        tracker, _ = _make_tracker()
        tracker.record_error(ValueError("a"))
        tracker.record_error(TypeError("b"))
        tracker.record_error(ValueError("c"))

        summary = tracker.get_error_summary()

        assert summary["total_errors"] == 3
        assert set(summary["error_types"]) == {"ValueError", "TypeError"}

    def test_latest_errors_capped_at_ten(self):
        tracker, _ = _make_tracker()
        for i in range(15):
            tracker.record_error(ValueError(str(i)))

        summary = tracker.get_error_summary()

        assert len(summary["latest_errors"]) == 10
        # Most recent ones, in original order.
        assert summary["latest_errors"][-1]["message"] == "14"


class TestGetStallSummary:
    def test_excludes_non_timeout_errors(self):
        tracker, _ = _make_tracker()
        tracker.record_error(ValueError("not a stall"))

        summary = tracker.get_stall_summary()

        assert summary["total_stalls"] == 0

    def test_excludes_first_call_timeouts(self):
        tracker, _ = _make_tracker()
        tracker.record_error(TimeoutError("slow first call"), is_first_call=True)

        summary = tracker.get_stall_summary()

        assert summary["total_stalls"] == 0

    def test_includes_non_first_call_timeouts(self):
        tracker, _ = _make_tracker()
        tracker.record_error(TimeoutError("genuine stall"), is_first_call=False)

        summary = tracker.get_stall_summary()

        assert summary["total_stalls"] == 1
        assert summary["stalls"][0]["message"] == "genuine stall"

    def test_includes_timeout_with_no_is_first_call_set(self):
        tracker, _ = _make_tracker()
        tracker.record_error(TimeoutError("unlabeled stall"))

        summary = tracker.get_stall_summary()

        assert summary["total_stalls"] == 1


class TestCaptureSystemContext:
    def test_returns_dict_without_raising(self):
        # Whether or not psutil is installed in this environment, this
        # must never raise -- error recording can't itself fail.
        context = _capture_system_context()

        assert isinstance(context, dict)

    def test_missing_psutil_returns_empty_dict(self, monkeypatch):
        import infrastructure.error_tracking as et

        monkeypatch.setattr(et, "_PSUTIL_AVAILABLE", False)

        assert _capture_system_context() == {}
