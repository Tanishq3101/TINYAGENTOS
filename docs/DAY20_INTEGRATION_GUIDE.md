# Day 20 Integration Guide

How the new files land in the existing tree, and exactly what to change
(and not change) in files this session didn't have access to.

## New files (drop in as-is, no prior-day file touched)

```
infrastructure/monitoring.py        # metrics: counters/histograms/gauges
infrastructure/error_tracking.py    # ErrorTracker, extended w/ is_first_call + system context
infrastructure/stall_watchdog.py    # background thread + track_call() context manager
tests/unit/test_monitoring.py
tests/unit/test_error_tracking.py
tests/unit/test_stall_watchdog.py
```

All three modules degrade gracefully if `prometheus_client` isn't
installed yet (same pattern `scripts/benchmark_inference.py` already
uses for `psutil`) — verified by running the test suite both with and
without the dependency present. Nothing here imports from
`core/llm_runtime.py`, `core/orchestrator.py`, or `agents/*.py`, so
none of those can be broken by adding these files.

## requirements.txt

Add one line (exact contents of the file weren't available in this
session, so this isn't applied automatically):

```
prometheus-client==0.19.0
```

`psutil` is already required from Day 18-19 — no change needed there.

## config/production.yaml (or wherever `Settings` reads from)

Add, don't edit existing keys:

```yaml
alerting:
  llm_call_p95_threshold_seconds: 15
  llm_call_hard_timeout_seconds: 60
  stall_watchdog_interval_seconds: 5
```

If `infrastructure/config.py`'s `Settings` class matches the plan
template's shape, add matching fields:

```python
STALL_THRESHOLD_SECONDS: float = 25.0
STALL_WATCHDOG_INTERVAL_SECONDS: float = 5.0
```

`infrastructure/stall_watchdog.py` currently hardcodes its defaults
(`DEFAULT_STALL_THRESHOLD_SECONDS = 25.0`) so it works standalone
without a config dependency. Once you confirm the real `Settings`
class, wire it through at startup via `configure_default_watchdog()`
(see below) rather than editing the module's defaults directly.

## Application startup — wire the watchdog to the error tracker

Wherever the app currently constructs `Orchestrator`/agents at startup
(likely `api/app.py` or a similar entrypoint — not available in this
session), add:

```python
from infrastructure.error_tracking import ErrorTracker
from infrastructure.stall_watchdog import configure_default_watchdog
from infrastructure.logging import StructuredLogger
from infrastructure.config import settings  # once confirmed

logger = StructuredLogger(__name__)
error_tracker = ErrorTracker(logger)

configure_default_watchdog(
    error_tracker=error_tracker,
    stall_threshold_seconds=getattr(settings, "STALL_THRESHOLD_SECONDS", 25.0),
    check_interval_seconds=getattr(settings, "STALL_WATCHDOG_INTERVAL_SECONDS", 5.0),
)
```

`getattr(..., default)` used deliberately so this doesn't hard-fail if
`Settings` doesn't have those fields yet — falls back to the module's
built-in defaults.

## agents/*.py — the one change that needs the real files

This is the only piece that couldn't be done blind. In each of
`agents/summarizer.py`, `agents/extractor.py`, `agents/critic.py`,
wrap the existing `generate()` call site — do not change anything
else about `execute()`, its inputs, or its return contract:

```python
from infrastructure.stall_watchdog import track_call

class SummarizerAgent(Agent):  # or whatever the real base/class is
    def execute(self, input_data, **kwargs):
        # ... existing code before the generate() call, unchanged ...

        with track_call(agent_name="summarizer"):  # <- only new line
            output = self.llm_runtime.generate(prompt, max_tokens=..., temperature=...)

        # ... existing code after the generate() call, unchanged ...
```

Use the matching `agent_name` string per file (`"summarizer"`,
`"extractor"`, `"critic"`) — that's the label `llm_call_latency` and
stall records key on.

**Do not** add this inside `core/llm_runtime.py`. That file is locked
from the Day 14 threading fix; wrapping at the agent call site instead
keeps this entirely inside Day 20's scope and touches nothing the
lock protects.

## Why this doesn't break Days 1-19

| Day range | What exists | Touched by this? |
|---|---|---|
| 1-2 | repo structure, requirements.txt | Only an addition (`prometheus-client` line) |
| 3-4 | `infrastructure/logging.py`, `config.py`, `security.py` | Not touched; `ErrorTracker` only *depends on* the logger's existing interface |
| 6-10 | `core/llm_runtime.py`, `agents/base.py`, agents | `llm_runtime.py` untouched; agents get one `with` line added around an existing call, contract unchanged |
| 11-15 | `core/orchestrator.py`, API layer, tests | Not touched |
| 18-19 | `scripts/*.py`, `tests/test_benchmark_inference.py` | Not touched — verified by running that suite alongside the new Day 20 tests (69/69 passing) |

Net: three new files, three one-line additions inside existing
`execute()` methods, one new requirements line, a few new (not
edited) config keys.
