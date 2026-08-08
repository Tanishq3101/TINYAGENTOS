from core.tools.base import BaseTool


class CalculatorTool(BaseTool):
    """
    Simple calculator tool
    """

    name = "calculator"
    description = "Performs basic math calculations"

    def run(self, input_text: str):
        try:
            # ⚠️ SAFE EVAL (basic protection)
            allowed_chars = "0123456789+-*/(). "

            if not all(c in allowed_chars for c in input_text):
                raise ValueError("Invalid characters in expression")

            return eval(input_text)

        except Exception:
            return "Invalid math expression"