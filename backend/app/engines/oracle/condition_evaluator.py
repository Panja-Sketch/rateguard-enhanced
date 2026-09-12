from decimal import Decimal
from typing import Any

from app.engines.oracle.errors import ConditionEvaluationError, ReferenceResolutionError
from app.ipir.common import LiteralValue, NodeReference
from app.ipir.enums import ComparisonOperator, LogicalOperator
from app.ipir.rules import ComparisonCondition, LogicalCondition


def resolve_value(operand: Any, context: dict[str, Any]) -> Any:
    """Resolves an operand (NodeReference, LiteralValue, or Expression) to its primitive value."""
    if isinstance(operand, NodeReference):
        if operand.ref not in context:
            raise ReferenceResolutionError(
                f"Cannot resolve reference '{operand.ref}' from evaluation context"
            )
        return context[operand.ref]

    if isinstance(operand, LiteralValue):
        return operand.value

    # Lazy import to avoid circular dependencies with expression_evaluator
    from app.engines.oracle.expression_evaluator import evaluate_expression
    from app.ipir.expressions import Expression

    if isinstance(operand, Expression):
        return evaluate_expression(operand, context)

    return operand


def evaluate_comparison(condition: ComparisonCondition, context: dict[str, Any]) -> bool:
    """Evaluates a relational comparison condition against the context."""
    left_val = resolve_value(condition.left, context)
    right_val = resolve_value(condition.right, context)

    # Convert numeric types to Decimal if comparing numbers (excluding booleans)
    if not isinstance(left_val, bool) and not isinstance(right_val, bool):
        if isinstance(left_val, (int, float, Decimal)) and isinstance(
            right_val, (int, float, Decimal)
        ):
            left_val = Decimal(str(left_val))
            right_val = Decimal(str(right_val))

    op = condition.operator

    try:
        if op == ComparisonOperator.EQ:
            return left_val == right_val
        elif op == ComparisonOperator.NE:
            return left_val != right_val
        elif op == ComparisonOperator.GT:
            return left_val > right_val
        elif op == ComparisonOperator.GTE:
            return left_val >= right_val
        elif op == ComparisonOperator.LT:
            return left_val < right_val
        elif op == ComparisonOperator.LTE:
            return left_val <= right_val
        else:
            raise ConditionEvaluationError(f"Unsupported comparison operator: {op}")
    except TypeError as e:
        raise ConditionEvaluationError(
            f"Comparison failed between {left_val} ({type(left_val).__name__}) "
            f"and {right_val} ({type(right_val).__name__}) using {op}: {e}"
        ) from e


def evaluate_condition(
    condition: ComparisonCondition | LogicalCondition, context: dict[str, Any]
) -> bool:
    """Evaluates a comparison or logical condition against the evaluation context."""
    if isinstance(condition, ComparisonCondition):
        return evaluate_comparison(condition, context)

    if isinstance(condition, LogicalCondition):
        if condition.operator == LogicalOperator.AND:
            return all(evaluate_condition(c, context) for c in condition.conditions)
        elif condition.operator == LogicalOperator.OR:
            return any(evaluate_condition(c, context) for c in condition.conditions)
        else:
            raise ConditionEvaluationError(f"Unsupported logical operator: {condition.operator}")

    raise ConditionEvaluationError(f"Unknown condition type: {type(condition)}")
