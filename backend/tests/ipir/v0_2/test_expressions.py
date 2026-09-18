import pytest
from pydantic import ValidationError

from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ComparisonConditionV2,
    ConditionalExpression,
    LiteralExpression,
    NaryExpression,
    ReferenceExpression,
    RoundExpression,
    TableLookupExpression,
    UnaryExpression,
)


def test_literal_expression_round_trip():
    expr = LiteralExpression(value="1.40")
    assert expr.kind == "LITERAL"
    assert expr.value == "1.40"


def test_reference_expression_rejects_v1_style_id():
    with pytest.raises(ValidationError):
        ReferenceExpression(ref="Base-Rate")  # uppercase/hyphen not allowed under v0.2 pattern


def test_reference_expression_accepts_v2_id():
    assert ReferenceExpression(ref="base_rate").ref == "base_rate"


def test_binary_expression_divide_defaults_to_reject():
    expr = BinaryExpression(operator="DIVIDE", left=LiteralExpression(value=1), right=LiteralExpression(value=2))
    assert expr.on_zero_behavior == "REJECT"


def test_binary_expression_divide_rejects_unsupported_zero_behavior():
    with pytest.raises(ValidationError):
        BinaryExpression(
            operator="DIVIDE",
            left=LiteralExpression(value=1),
            right=LiteralExpression(value=2),
            on_zero_behavior="ZERO",
        )


def test_nary_expression_requires_at_least_two_operands():
    with pytest.raises(ValidationError):
        NaryExpression(operator="ADD", operands=[LiteralExpression(value=1)])


def test_nary_expression_accepts_three_operands():
    expr = NaryExpression(
        operator="ADD",
        operands=[LiteralExpression(value=1), LiteralExpression(value=2), LiteralExpression(value=3)],
    )
    assert len(expr.operands) == 3


def test_unary_expression_only_supports_negate():
    with pytest.raises(ValidationError):
        UnaryExpression(operator="ABS", operand=LiteralExpression(value=1))


def test_round_expression_requires_scale_and_mode():
    with pytest.raises(ValidationError):
        RoundExpression(operand=LiteralExpression(value="1.005"))  # missing scale/rounding_mode


def test_round_expression_valid():
    expr = RoundExpression(operand=LiteralExpression(value="1.005"), scale=2, rounding_mode="HALF_UP")
    assert expr.scale == 2


def test_table_lookup_expression_validates_id():
    with pytest.raises(ValidationError):
        TableLookupExpression(table_id="Roof-Age-Table")


def test_conditional_expression_discriminated_union_parses_from_dict():
    from pydantic import TypeAdapter

    from app.ipir.v0_2.expressions import ExpressionV2

    payload = {
        "kind": "CONDITIONAL",
        "condition": {
            "left": {"kind": "REFERENCE", "ref": "roof_age"},
            "operator": "GTE",
            "right": {"kind": "LITERAL", "value": 20},
        },
        "when_true": {"kind": "LITERAL", "value": "1.40"},
        "when_false": {"kind": "LITERAL", "value": "1.00"},
    }
    adapter = TypeAdapter(ExpressionV2)
    expr = adapter.validate_python(payload)
    assert isinstance(expr, ConditionalExpression)
    assert isinstance(expr.condition, ComparisonConditionV2)


def test_discriminated_union_rejects_unknown_kind():
    from pydantic import TypeAdapter

    from app.ipir.v0_2.expressions import ExpressionV2

    adapter = TypeAdapter(ExpressionV2)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "MADE_UP", "value": 1})


def test_expression_rejects_unknown_field():
    with pytest.raises(ValidationError):
        LiteralExpression(value=1, unexpected_field="nope")
