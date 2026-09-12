from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from app.engines.oracle.condition_evaluator import resolve_value
from app.engines.oracle.errors import ExpressionEvaluationError
from app.engines.oracle.rounding import round_decimal
from app.ipir.common import LiteralValue, NodeReference
from app.ipir.enums import ExpressionOperator, RoundingMode
from app.ipir.expressions import Expression


def _to_decimal(val: Any) -> Decimal:
    """Helper to convert any numeric value safely to Decimal."""
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise ExpressionEvaluationError(f"Cannot convert operand '{val}' to Decimal: {e}") from e


def evaluate_expression(
    expression: Expression | NodeReference | LiteralValue, context: dict[str, Any]
) -> Decimal | int | str | bool:
    """Recursively evaluates an AST expression node using exact Decimal arithmetic.

    Args:
        expression: Target Expression, NodeReference, or LiteralValue.
        context: Current evaluation context.

    Returns:
        Evaluated value (Decimal for numeric calculations).
    """
    if isinstance(expression, (NodeReference, LiteralValue)):
        resolved = resolve_value(expression, context)
        if isinstance(resolved, (int, float)):
            return Decimal(str(resolved))
        return resolved

    if not isinstance(expression, Expression):
        raise ExpressionEvaluationError(f"Invalid expression AST node: {type(expression)}")

    op = expression.operator
    eval_operands = [evaluate_expression(operand, context) for operand in expression.operands]

    if op == ExpressionOperator.ADD:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        result = sum(dec_operands[1:], dec_operands[0])
        return result

    elif op == ExpressionOperator.SUBTRACT:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        result = dec_operands[0]
        for val in dec_operands[1:]:
            result -= val
        return result

    elif op == ExpressionOperator.MULTIPLY:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        result = Decimal("1")
        for val in dec_operands:
            result *= val
        return result

    elif op == ExpressionOperator.DIVIDE:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        if dec_operands[1] == Decimal("0"):
            raise ExpressionEvaluationError("Division by zero in pricing calculation")
        try:
            return dec_operands[0] / dec_operands[1]
        except DivisionByZero as e:
            raise ExpressionEvaluationError("Division by zero in pricing calculation") from e

    elif op == ExpressionOperator.MIN:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        return min(dec_operands)

    elif op == ExpressionOperator.MAX:
        dec_operands = [_to_decimal(v) for v in eval_operands]
        return max(dec_operands)

    elif op == ExpressionOperator.ROUND:
        dec_val = _to_decimal(eval_operands[0])
        precision = int(eval_operands[1]) if len(eval_operands) > 1 else 2
        return round_decimal(dec_val, precision, RoundingMode.HALF_UP)

    else:
        raise ExpressionEvaluationError(f"Unsupported expression operator: {op}")
