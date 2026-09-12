from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ipir.common import LiteralValue, NodeReference
from app.ipir.enums import ExpressionOperator
from app.ipir.expressions import Expression


def test_expression_construction() -> None:
    expr = Expression(
        operator=ExpressionOperator.MULTIPLY,
        operands=[
            NodeReference(ref="base_rate"),
            NodeReference(ref="territory_factor"),
            LiteralValue(value=Decimal("1.05")),
        ],
    )
    assert expr.operator == ExpressionOperator.MULTIPLY
    assert len(expr.operands) == 3


def test_divide_expression_validation() -> None:
    # Valid divide (2 operands)
    div_valid = Expression(
        operator=ExpressionOperator.DIVIDE,
        operands=[NodeReference(ref="numerator"), NodeReference(ref="denominator")],
    )
    assert div_valid.operator == ExpressionOperator.DIVIDE

    # Invalid divide (3 operands)
    with pytest.raises(ValidationError, match="DIVIDE operator requires exactly 2 operands"):
        Expression(
            operator=ExpressionOperator.DIVIDE,
            operands=[
                NodeReference(ref="a"),
                NodeReference(ref="b"),
                NodeReference(ref="c"),
            ],
        )


def test_round_expression_validation() -> None:
    # Valid round (1 operand)
    r1 = Expression(
        operator=ExpressionOperator.ROUND,
        operands=[NodeReference(ref="amount")],
    )
    assert len(r1.operands) == 1

    # Valid round (2 operands)
    r2 = Expression(
        operator=ExpressionOperator.ROUND,
        operands=[NodeReference(ref="amount"), LiteralValue(value=2)],
    )
    assert len(r2.operands) == 2

    # Invalid round (3 operands)
    with pytest.raises(ValidationError, match="ROUND operator requires 1 or 2 operands"):
        Expression(
            operator=ExpressionOperator.ROUND,
            operands=[
                NodeReference(ref="a"),
                LiteralValue(value=2),
                LiteralValue(value=3),
            ],
        )
