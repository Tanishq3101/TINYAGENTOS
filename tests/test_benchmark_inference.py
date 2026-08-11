"""
tests/test_benchmark_inference.py

Tests for scripts/benchmark_inference.py (Day 18-19).

Scope: these tests verify the benchmark SCRIPT's own logic -- stats math
(_percentile, _stats_from_durations_ms), RSS/psutil handling, prompt/call
wiring, CLI argument parsing, and JSON output. They do this by mocking
`core.llm_runtime.LLMRuntime` at the point scripts/benchmark_inference.py
imports it (`bi.LLMRuntime`), so the suite runs fast, deterministically,
and without llama-cpp-python or a GGUF model installed.

Explicitly OUT of scope: whether LLMRuntime.generate() itself is fast,
correct, or memory-efficient. That's what scripts/benchmark_inference.py
is *for* measuring against a real model -- this suite only checks that
the measuring tool itself is correct.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import scripts.benchmark_inference as bi


# ---------------------------------------------------------------------------
# Fixtures / test doubles
# ---------------------------------------------------------------------------
class FakeRuntime:
    """Stand-in for LLMRuntime. Satisfies generate()'s real call
    signature -- generate(self, prompt, max_tokens=None, temperature=None)
    -- without importing llama_cpp. Records every call for assertions."""

    def __init__(self, response: str = "fake response", delay: float = 0.0):
        self._response = response
        self._delay = delay
        self.calls = []

    def generate(self, prompt, max_tokens=None, temperature=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature})
        if self._delay:
            time.sleep(self._delay)
        return self._response


@pytest.fixture
def fake_runtime():
    return FakeRuntime()


@pytest.fixture(autouse=True)
def _isolate_psutil_flag(monkeypatch):
    """Every test explicitly sets bi._PSUTIL_AVAILABLE to whatever it
    needs rather than relying on whether psutil happens to be installed
    in the environment running the suite -- makes the tests deterministic
    either way. monkeypatch auto-restores the original value after each
    test regardless of which branch a test takes."""
    yield


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------
class TestPercentile:
    def test_empty_list_returns_zero(self):
        assert bi._percentile([], 95) == 0.0

    def test_single_value_returns_that_value(self):
        assert bi._percentile([42.0], 95) == 42.0

    def test_p0_returns_min(self):
        assert bi._percentile([1.0, 2.0, 3.0], 0) == 1.0

    def test_p100_returns_max(self):
        assert bi._percentile([1.0, 2.0, 3.0], 100) == 3.0

    def test_p50_of_five_values(self):
        assert bi._percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50) == 30.0


# ---------------------------------------------------------------------------
# _stats_from_durations_ms
# ---------------------------------------------------------------------------
class TestStatsFromDurations:
    def test_basic_stats(self):
        stats = bi._stats_from_durations_ms([10.0, 20.0, 30.0])
        assert stats.n == 3
        assert stats.mean_ms == 20.0
        assert stats.median_ms == 20.0
        assert stats.min_ms == 10.0
        assert stats.max_ms == 30.0

    def test_empty_input_does_not_raise(self):
        stats = bi._stats_from_durations_ms([])
        assert stats == bi.LatencyStats(
            n=0, mean_ms=0.0, median_ms=0.0, p95_ms=0.0, p99_ms=0.0, min_ms=0.0, max_ms=0.0
        )

    def test_single_value(self):
        stats = bi._stats_from_durations_ms([5.0])
        assert (stats.n, stats.mean_ms, stats.median_ms, stats.min_ms, stats.max_ms) == (
            1,
            5.0,
            5.0,
            5.0,
            5.0,
        )

    def test_unsorted_input_still_finds_correct_min_max(self):
        stats = bi._stats_from_durations_ms([30.0, 10.0, 20.0])
        assert stats.min_ms == 10.0
        assert stats.max_ms == 30.0


# ---------------------------------------------------------------------------
# _rss_mb
# ---------------------------------------------------------------------------
class TestRssMb:
    def test_returns_none_when_psutil_unavailable(self, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)
        assert bi._rss_mb() is None

    def test_returns_mb_from_psutil_when_available(self, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        fake_process = MagicMock()
        fake_process.memory_info.return_value = SimpleNamespace(rss=100 * 1024 * 1024)
        fake_psutil = MagicMock()
        fake_psutil.Process.return_value = fake_process
        monkeypatch.setattr(bi, "psutil", fake_psutil, raising=False)

        assert bi._rss_mb() == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# load_model_and_measure
# ---------------------------------------------------------------------------
class TestLoadModelAndMeasure:
    def test_constructs_runtime_via_LLMRuntime(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)

        runtime, stats = bi.load_model_and_measure()

        assert runtime is fake
        assert stats["load_seconds"] >= 0
        assert "model_footprint_mb" not in stats  # psutil unavailable -> omitted, not zero/null

    def test_reports_rss_delta_when_psutil_available(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        rss_values = iter([500.0, 800.0])
        monkeypatch.setattr(bi, "_rss_mb", lambda: next(rss_values))

        _, stats = bi.load_model_and_measure()

        assert stats["rss_before_mb"] == 500.0
        assert stats["rss_after_mb"] == 800.0
        assert stats["model_footprint_mb"] == 300.0


# ---------------------------------------------------------------------------
# benchmark_latency_by_prompt_size
# ---------------------------------------------------------------------------
class TestBenchmarkLatencyByPromptSize:
    def test_one_result_per_prompt_size_with_correct_n(self, fake_runtime):
        results = bi.benchmark_latency_by_prompt_size(fake_runtime, calls_per_size=3, max_tokens=64)

        assert set(results.keys()) == set(bi._PROMPTS.keys())
        for stats in results.values():
            assert stats["n"] == 3
        assert len(fake_runtime.calls) == 3 * len(bi._PROMPTS)

    def test_max_tokens_passed_through_to_generate(self, fake_runtime):
        bi.benchmark_latency_by_prompt_size(fake_runtime, calls_per_size=1, max_tokens=256)
        assert all(call["max_tokens"] == 256 for call in fake_runtime.calls)

    def test_each_prompt_size_uses_its_own_prompt_text(self, fake_runtime):
        bi.benchmark_latency_by_prompt_size(fake_runtime, calls_per_size=1, max_tokens=64)
        prompts_used = {call["prompt"] for call in fake_runtime.calls}
        assert prompts_used == set(bi._PROMPTS.values())


# ---------------------------------------------------------------------------
# benchmark_memory_growth
# ---------------------------------------------------------------------------
class TestBenchmarkMemoryGrowth:
    def test_returns_none_and_skips_calls_when_psutil_unavailable(self, fake_runtime, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)
        result = bi.benchmark_memory_growth(fake_runtime, calls=5)
        assert result is None
        # Short-circuits before calling generate() at all -- no point
        # running real inference calls if there's no way to measure them.
        assert fake_runtime.calls == []

    def test_growth_computed_correctly(self, fake_runtime, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        rss_sequence = iter([100.0, 105.0, 110.0])
        monkeypatch.setattr(bi, "_rss_mb", lambda: next(rss_sequence))

        result = bi.benchmark_memory_growth(fake_runtime, calls=3)

        assert result["n_calls"] == 3
        assert result["rss_mb_first"] == 100.0
        assert result["rss_mb_last"] == 110.0
        assert result["growth_mb"] == 10.0
        assert result["rss_mb_min"] == 100.0
        assert result["rss_mb_max"] == 110.0
        assert result["samples_mb"] == [100.0, 105.0, 110.0]
        assert len(fake_runtime.calls) == 3

    def test_uses_max_tokens_64_for_growth_calls(self, fake_runtime, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        monkeypatch.setattr(bi, "_rss_mb", lambda: 100.0)
        bi.benchmark_memory_growth(fake_runtime, calls=2)
        assert all(call["max_tokens"] == 64 for call in fake_runtime.calls)

    def test_custom_prompt_is_used_when_provided(self, fake_runtime, monkeypatch):
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        monkeypatch.setattr(bi, "_rss_mb", lambda: 100.0)
        bi.benchmark_memory_growth(fake_runtime, calls=1, prompt="custom prompt text")
        assert fake_runtime.calls[0]["prompt"] == "custom prompt text"


# ---------------------------------------------------------------------------
# run_all_benchmarks
# ---------------------------------------------------------------------------
class TestRunAllBenchmarks:
    def test_includes_memory_section_when_psutil_available_and_not_skipped(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        monkeypatch.setattr(bi, "_rss_mb", lambda: 100.0)

        results = bi.run_all_benchmarks(calls_per_size=2, max_tokens=32, memory_calls=2)

        assert "model_load" in results
        assert "latency_by_prompt_size" in results
        assert results.get("memory_growth") is not None

    def test_skip_memory_flag_omits_memory_section(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", True)
        monkeypatch.setattr(bi, "_rss_mb", lambda: 100.0)

        results = bi.run_all_benchmarks(
            calls_per_size=1, max_tokens=32, memory_calls=1, skip_memory=True
        )

        assert "memory_growth" not in results

    def test_missing_psutil_omits_memory_and_footprint_gracefully(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)

        results = bi.run_all_benchmarks(calls_per_size=1, max_tokens=32, memory_calls=1)

        assert "memory_growth" not in results
        assert "model_footprint_mb" not in results["model_load"]

    def test_latency_results_cover_every_prompt_size(self, monkeypatch):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)

        results = bi.run_all_benchmarks(calls_per_size=1, max_tokens=32, memory_calls=1)

        assert set(results["latency_by_prompt_size"].keys()) == set(bi._PROMPTS.keys())


# ---------------------------------------------------------------------------
# CLI / main()
# ---------------------------------------------------------------------------
class TestMain:
    def test_writes_valid_json_to_output_path(self, monkeypatch, tmp_path):
        fake = FakeRuntime()
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=fake))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)

        output_path = tmp_path / "results.json"
        monkeypatch.setattr(
            "sys.argv",
            [
                "benchmark_inference.py",
                "--calls",
                "1",
                "--memory-calls",
                "1",
                "--output",
                str(output_path),
            ],
        )

        bi.main()

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert "model_load" in data
        assert "latency_by_prompt_size" in data

    def test_skip_memory_flag_reaches_run_all_benchmarks(self, monkeypatch):
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=FakeRuntime()))
        captured = {}

        def fake_run_all_benchmarks(**kwargs):
            captured.update(kwargs)
            return {"model_load": {}, "latency_by_prompt_size": {}}

        monkeypatch.setattr(bi, "run_all_benchmarks", fake_run_all_benchmarks)
        monkeypatch.setattr("sys.argv", ["benchmark_inference.py", "--skip-memory"])

        bi.main()

        assert captured["skip_memory"] is True

    def test_default_args_match_documented_defaults(self, monkeypatch):
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=FakeRuntime()))
        captured = {}

        def fake_run_all_benchmarks(**kwargs):
            captured.update(kwargs)
            return {}

        monkeypatch.setattr(bi, "run_all_benchmarks", fake_run_all_benchmarks)
        monkeypatch.setattr("sys.argv", ["benchmark_inference.py"])

        bi.main()

        assert captured == {
            "calls_per_size": 10,
            "max_tokens": 128,
            "memory_calls": 20,
            "skip_memory": False,
        }

    def test_no_output_arg_does_not_write_a_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bi, "LLMRuntime", MagicMock(return_value=FakeRuntime()))
        monkeypatch.setattr(bi, "_PSUTIL_AVAILABLE", False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv", ["benchmark_inference.py", "--calls", "1", "--memory-calls", "1"]
        )

        bi.main()

        assert list(tmp_path.iterdir()) == []
