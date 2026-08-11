import ast
import operator
from typing import Callable, Dict, Type, Union

from core.tools.base import BaseTool

Number = Union[int, float]

# Whitelisted operators only — arithmetic, nothing else.
_BIN_OPS: Dict[Type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS: Dict[Type[ast.unaryop], Callable[[Number], Number]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> Number:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Invalid math expression")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        operand = _safe_eval(node.operand)
        return _UNARY_OPS[type(node.op)](operand)
    raise ValueError("Invalid math expression")


class CalculatorTool(BaseTool):
    """
    Simple calculator tool
    """

    name = "calculator"
    description = "Performs basic math calculations"

    def run(self, tool_input: str) -> str:
        try:
            allowed_chars = "0123456789+-*/(). "

            if not all(c in allowed_chars for c in tool_input):
                raise ValueError("Invalid characters in expression")

            tree = ast.parse(tool_input, mode="eval")
            result = _safe_eval(tree)
            return str(result)

        except Exception:
            return "Invalid math expression"
