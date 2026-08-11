import ast
import operator

from core.tools.base import BaseTool

# Whitelisted operators only — arithmetic, nothing else.
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Invalid math expression")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Invalid math expression")


class CalculatorTool(BaseTool):
    """
    Simple calculator tool
    """

    name = "calculator"
    description = "Performs basic math calculations"

    def run(self, input_text: str):
        try:
            allowed_chars = "0123456789+-*/(). "

            if not all(c in allowed_chars for c in input_text):
                raise ValueError("Invalid characters in expression")

            tree = ast.parse(input_text, mode="eval")
            return _safe_eval(tree)

        except Exception:
            return "Invalid math expression"