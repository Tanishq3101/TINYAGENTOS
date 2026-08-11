# core/tools/registry.py

from typing import Protocol, TypedDict

from core.tools.calculator import CalculatorTool
from core.tools.weather import WeatherTool  # ✅ import your new tool


class Tool(Protocol):
    """Structural type: anything with a `.run(tool_input) -> str` method
    counts as a Tool, without CalculatorTool/WeatherTool needing to
    inherit from a common base class."""

    def run(self, tool_input: str) -> str: ...


class ToolEntry(TypedDict):
    tool: Tool
    description: str


# Central tool registry
TOOLS: dict[str, ToolEntry] = {
    "calculator": {"tool": CalculatorTool(), "description": "Performs mathematical calculations"},
    "weather": {  # ✅ ADD HERE
        "tool": WeatherTool(),
        "description": "Gets current weather information for a city",
    },
}