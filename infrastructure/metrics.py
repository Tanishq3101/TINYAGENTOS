"""
Lightweight in-process metrics collection for agent/pipeline execution.

This is intentionally simple (no external deps) — it's the data structure
that Week 4's Prometheus exporter will read from, not a replacement for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class AgentMetrics:
    """Metrics for a single agent execution."""

    agent_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    execution_time_ms: Optional[float] = None
    tokens_processed: int = 0
    error: Optional[str] = None

    def finalize(self, error: Optional[str] = None) -> None:
        """Mark execution as complete and compute elapsed time."""
        self.end_time = datetime.now()
        self.execution_time_ms = (self.end_time - self.start_time).total_seconds() * 1000
        if error is not None:
            self.error = error


class MetricsCollector:
    """Centralized metrics collection and reporting for a single pipeline run."""

    def __init__(self) -> None:
        self.metrics: list[AgentMetrics] = []
        self.system_metrics: dict[str, Any] = {}

    def start_agent_metrics(self, agent_name: str) -> AgentMetrics:
        """Start tracking metrics for an agent. Caller must call .finalize() when done."""
        metrics = AgentMetrics(agent_name=agent_name, start_time=datetime.now())
        self.metrics.append(metrics)
        return metrics

    def get_pipeline_summary(self) -> dict[str, Any]:
        """Generate a summary of the whole pipeline run so far."""
        # Filtering and summing in the same generator (rather than building
        # a separate `completed` list first) lets mypy narrow
        # `execution_time_ms` to `float` within this expression -- summing
        # over a pre-filtered list left it typed `float | None` here, since
        # the None-check lived in a separate comprehension mypy couldn't
        # connect back to this one. Also a real runtime safety net: if a
        # None ever slipped through, sum() would previously have crashed
        # with TypeError at runtime instead of failing a type check.
        total_time = sum(
            m.execution_time_ms for m in self.metrics if m.execution_time_ms is not None
        )
        error_count = sum(1 for m in self.metrics if m.error)

        return {
            "total_execution_time_ms": total_time,
            "agent_count": len(self.metrics),
            "error_count": error_count,
            "agents": [
                {
                    "name": m.agent_name,
                    "execution_time_ms": m.execution_time_ms,
                    "tokens_processed": m.tokens_processed,
                    "error": m.error,
                }
                for m in self.metrics
            ],
        }

    def reset(self) -> None:
        """Clear collected metrics — call between pipeline runs if reusing an instance."""
        self.metrics.clear()
        self.system_metrics.clear()