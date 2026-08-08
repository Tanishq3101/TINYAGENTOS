from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """
    Abstract base class for all tools
    """

    name: str = "base_tool"
    description: str = "Base tool description"

    @abstractmethod
    def run(self, input_text: str) -> Any:
        """
        Execute the tool logic

        Args:
            input_text (str): Input provided to the tool

        Returns:
            Any: Output from the tool
        """
        pass

    def __call__(self, input_text: str) -> Any:
        """
        Allows tool to be called like a function
        """
        return self.run(input_text)

    def info(self) -> dict:
        """
        Returns tool metadata
        """
        return {"name": self.name, "description": self.description}
