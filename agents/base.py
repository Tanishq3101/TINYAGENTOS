from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel

from infrastructure.logging import logger


class AgentConfig(BaseModel):
    """Configuration for agents"""
    name: str
    description: str
    max_tokens: int = 512
    temperature: float = 0.7
    retry_count: int = 3
    timeout_seconds: int = 60


@dataclass
class AgentMetrics:
    """Lightweight execution metrics for a single agent run.

    Kept self-contained here (rather than importing an
    infrastructure.metrics.AgentMetrics) since that module's presence
    in this project hasn't been confirmed. Swap this out if/when a
    shared metrics module exists.
    """
    agent_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    execution_time_ms: Optional[float] = None
    error: Optional[str] = None

    def finalize(self) -> None:
        self.end_time = datetime.now()
        self.execution_time_ms = (
            self.end_time - self.start_time
        ).total_seconds() * 1000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
        }


class Agent(ABC):
    """Abstract base class for all specialized agents (Summarizer,
    Extractor, Critic, etc). Handles the common execute/error/metrics
    scaffolding so subclasses only implement build_prompt and
    _execute_task.
    """

    def __init__(self, config: AgentConfig, llm_runtime):
        self.config = config
        self.llm = llm_runtime
        self.metrics: Optional[AgentMetrics] = None

    def execute(self, input_data: str, **kwargs) -> Dict[str, Any]:
        """Execute agent task with error handling and metrics."""
        self.metrics = AgentMetrics(
            agent_name=self.config.name,
            start_time=datetime.now(),
        )

        if not isinstance(input_data, str) or not input_data.strip():
            self.metrics.error = "Empty or invalid input_data"
            self.metrics.finalize()
            logger.warning(f"Agent {self.config.name} received empty input")
            return {
                "status": "error",
                "error": self.metrics.error,
                "metrics": self.metrics.to_dict(),
            }

        try:
            logger.info(
                f"Agent {self.config.name} started | "
                f"input_length={len(input_data)}"
            )

            result = self._execute_task(input_data, **kwargs)

            self.metrics.finalize()
            logger.info(
                f"Agent {self.config.name} completed | "
                f"execution_time_ms={self.metrics.execution_time_ms:.1f}"
            )

            return {
                "status": "success",
                "output": result,
                "metrics": self.metrics.to_dict(),
            }

        except Exception as e:
            self.metrics.error = str(e)
            self.metrics.finalize()
            logger.error(f"Agent {self.config.name} failed | error={e}")

            return {
                "status": "error",
                "error": str(e),
                "metrics": self.metrics.to_dict(),
            }

    @abstractmethod
    def _execute_task(self, input_data: str, **kwargs) -> Any:
        """Implement agent-specific task logic. Should call
        self.llm.generate(...) and return the processed result."""
        raise NotImplementedError

    @abstractmethod
    def build_prompt(self, input_data: str, **kwargs) -> str:
        """Build the prompt sent to the LLM for this agent's task."""
        raise NotImplementedError