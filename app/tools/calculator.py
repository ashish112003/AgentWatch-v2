"""
app/tools/calculator.py
────────────────────────
LangChain Calculator Tool.

Design goals:
  • SAFE — never calls eval() or exec() on raw user input.
    Uses Python's ast module to parse and walk an explicit whitelist
    of allowed AST node types, so only arithmetic is ever executed.
  • INFORMATIVE — returns structured results with both the expression
    and its evaluated value, making it easy for the LLM to explain
    the result in natural language.
  • OBSERVABLE — raises a descriptive ValueError on invalid input so
    the execution service can distinguish "governance block" from
    "tool error" in the audit log.

Why NOT use eval()?
  eval("__import__('os').system('rm -rf /')") is a real attack.
  Agent prompts come from user input, which means the tool input
  comes from user input (via the LLM).  We never trust it blindly.

Allowed AST nodes (whitelist):
  Module, Expr, BinOp        — expression structure
  Add, Sub, Mul, Div,        — basic arithmetic
  FloorDiv, Mod, Pow         — extended arithmetic
  USub, UAdd                 — unary minus/plus
  UnaryOp, BinOp             — operator wrappers
  Constant                   — numeric literals
  Call (math functions only) — sqrt, abs, round, floor, ceil
"""

import ast
import math
import operator
from typing import Any

from langchain_core.tools import tool


# ── Safe AST evaluator ────────────────────────────────────────────────────────

# Mapping from AST operator node types → Python operator functions.
# Only arithmetic operators are allowed; bitwise, logical, comparison
# operators are NOT included, preventing boolean/injection tricks.
_ALLOWED_OPERATORS: dict[type, Any] = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,     # Python 3.12: ast.Mult (not ast.Mul)
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}

# Math functions the LLM is allowed to call by name.
# Keeping this explicit prevents accessing other built-ins through
# the function-call AST path.
_ALLOWED_FUNCTIONS: dict[str, Any] = {
    "sqrt":  math.sqrt,
    "abs":   abs,
    "round": round,
    "floor": math.floor,
    "ceil":  math.ceil,
    "log":   math.log,
    "log10": math.log10,
    "sin":   math.sin,
    "cos":   math.cos,
    "tan":   math.tan,
    "pi":    math.pi,   # accessed as pi() — handled below
    "e":     math.e,    # accessed as e()  — handled below
}

# Mathematical constants exposed as zero-argument "calls" so the LLM
# can write pi() or e() in expressions.
_MATH_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e":  math.e,
}


def _safe_eval(node: ast.AST) -> float:
    """
    Recursively evaluate an AST node using the operator whitelist.

    This is the core of the safe evaluator.  It walks the AST tree
    produced by ast.parse() and computes the numeric result without
    ever calling eval() or exec().

    Args:
        node: An AST node (top-level or recursive sub-expression).

    Returns:
        The numeric result of the expression.

    Raises:
        ValueError: The expression contains unsupported syntax.
        ZeroDivisionError: Division by zero.
    """
    # Numeric literal: 42, 3.14, etc.
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")
        return float(node.value)

    # Binary operation: a + b, a * b, etc.
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Operator '{op_type.__name__}' is not allowed.")
        left  = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return _ALLOWED_OPERATORS[op_type](left, right)

    # Unary operation: -x, +x
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unary operator '{op_type.__name__}' is not allowed.")
        operand = _safe_eval(node.operand)
        return _ALLOWED_OPERATORS[op_type](operand)

    # Function call: sqrt(9), abs(-5), etc.
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls like sqrt(x) are supported.")
        func_name = node.func.id

        # Handle zero-argument math constants: pi(), e()
        if func_name in _MATH_CONSTANTS and not node.args:
            return _MATH_CONSTANTS[func_name]

        if func_name not in _ALLOWED_FUNCTIONS:
            raise ValueError(
                f"Function '{func_name}' is not allowed. "
                f"Allowed: {list(_ALLOWED_FUNCTIONS.keys())}"
            )
        func = _ALLOWED_FUNCTIONS[func_name]
        args = [_safe_eval(arg) for arg in node.args]
        return func(*args)

    # Expression wrapper (top-level node in some parse results)
    if isinstance(node, ast.Expr):
        return _safe_eval(node.value)

    raise ValueError(
        f"Unsupported expression type: {type(node).__name__}. "
        "Only arithmetic expressions are supported."
    )


def _evaluate_expression(expression: str) -> str:
    """
    Parse and evaluate a mathematical expression string safely.

    Args:
        expression: A math expression like "2 + 2", "sqrt(16) * 3", "100 / (2 + 3)".

    Returns:
        A formatted result string, e.g. "2 + 2 = 4.0".

    Raises:
        ValueError: Expression is invalid or uses disallowed syntax.
    """
    expression = expression.strip()
    if not expression:
        raise ValueError("Expression cannot be empty.")

    # ast.parse() returns a Module node; the expression is in body[0].
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc

    result = _safe_eval(tree.body)

    # Format the result — strip trailing .0 for clean integers.
    if result == int(result) and abs(result) < 1e15:
        formatted = str(int(result))
    else:
        formatted = f"{result:.6g}"   # up to 6 significant figures

    return f"{expression} = {formatted}"


# ── LangChain Tool ────────────────────────────────────────────────────────────

@tool
def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression and return the result.

    Supports: +, -, *, /, // (floor div), % (modulo), ** (power),
    and functions: sqrt, abs, round, floor, ceil, log, log10,
    sin, cos, tan, pi(), e().

    Examples:
        "2 + 2"             → "2 + 2 = 4"
        "sqrt(144)"         → "sqrt(144) = 12"
        "100 / (2 + 3)"     → "100 / (2 + 3) = 20"
        "(2 ** 10) - 1"     → "(2 ** 10) - 1 = 1023"

    Args:
        expression: A mathematical expression string to evaluate.

    Returns:
        A string showing the expression and its computed result.
    """
    try:
        return _evaluate_expression(expression)
    except ZeroDivisionError:
        return f"Error: Division by zero in expression '{expression}'"
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error evaluating '{expression}': {exc}"