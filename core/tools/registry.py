# core/tools/registry.py

from core.tools.calculator import CalculatorTool
from core.tools.weather import WeatherTool   # ✅ import your new tool

# Central tool registry
TOOLS = {
    "calculator": {
        "tool": CalculatorTool(),
        "description": "Performs mathematical calculations"
    },

    "weather": {   # ✅ ADD HERE
        "tool": WeatherTool(),
        "description": "Gets current weather information for a city"
    }
}