from decimal import Decimal
from pathlib import Path

import pytest

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.enums import TransactionType
from app.ipir.v0_2.calculations import CalculationNodeV2
from app.ipir.v0_2.compat import lower_to_v0_1, run_control_cases
from app.ipir.v0_2.errors import LoweringNotSupportedError
from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ComparisonConditionV2,
    ConditionalExpression,
    LiteralExpression,
    NaryExpression,
    ReferenceExpression,
    RoundExpression,
    UnaryExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2
from app.ipir.v0_2.package import IPIRPackageV2

from .conftest import make_minimal_package

GOLDEN_DIR = Path(__file__).resolve().parents[4] / "data" / "implementations" / "v0_2"


def _load_golden(name: str) -> IPIRPackageV2:
    path = GOLDEN_DIR / name / "AZ_HO3_GOLDEN_ipir.json"
    return IPIRPackageV2.model_validate_json(path.read_text(encoding="utf-8"))


def test_canonical_golden_fixture_reproduces_700():
    pkg = _load_golden("canonical")
    results = run_control_cases(pkg)
    assert len(results) == 1
    assert results[0].passed
    assert results[0].actual == "700.00"


def test_defective_golden_fixture_reproduces_655():
    pkg = _load_golden("defective")
    results = run_control_cases(pkg)
    assert len(results) == 1
    assert results[0].passed
    assert results[0].actual == "655.00"


def test_golden_fixtures_are_committed_and_parse_as_v0_2():
    # Guards against someone accidentally regenerating the fixtures in a way
    # that changes the golden narrative without updating the control cases.
    for name, expected in (("canonical", "700.00"), ("defective", "655.00")):
        pkg = _load_golden(name)
        assert pkg.control_cases[0].expected_outputs["final_premium_output"] == expected


def test_lower_to_v0_1_produces_evaluable_package():
    pkg = make_minimal_package()
    lowered = lower_to_v0_1(pkg)
    result = evaluate_package(
        package=lowered,
        risk=RiskInput(values={"roof_age": 25}),
        effective_date=pkg.effective_period.start,
        transaction_type=TransactionType.NEW_BUSINESS,
    )
    assert result.final_premium == Decimal("700.00")


def test_unary_negate_lowers_correctly():
    pkg = make_minimal_package(
        calculations=[
            CalculationNodeV2(
                id="final_premium",
                name="Negated",
                expression=UnaryExpression(operator="NEGATE", operand=LiteralExpression(value="5.00")),
            )
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Out",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode="HALF_UP",
            )
        ],
        control_cases=[],
    )
    lowered = lower_to_v0_1(pkg)
    result = evaluate_package(
        package=lowered, risk=RiskInput(values={"roof_age": 25}), effective_date=pkg.effective_period.start
    )
    assert result.final_premium == Decimal("-5.00")


def test_nary_add_lowers_correctly():
    pkg = make_minimal_package(
        calculations=[
            CalculationNodeV2(
                id="final_premium",
                name="Sum",
                expression=NaryExpression(
                    operator="ADD",
                    operands=[LiteralExpression(value="1.00"), LiteralExpression(value="2.00"), LiteralExpression(value="3.00")],
                ),
            )
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Out",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode="HALF_UP",
            )
        ],
        control_cases=[],
    )
    lowered = lower_to_v0_1(pkg)
    result = evaluate_package(
        package=lowered, risk=RiskInput(values={"roof_age": 25}), effective_date=pkg.effective_period.start
    )
    assert result.final_premium == Decimal("6.00")


def test_conditional_expression_lowers_via_synthesized_rule():
    pkg = make_minimal_package(
        calculations=[
            CalculationNodeV2(
                id="final_premium",
                name="Conditional",
                expression=ConditionalExpression(
                    condition=ComparisonConditionV2(
                        left=ReferenceExpression(ref="roof_age"),
                        operator="GTE",
                        right=LiteralExpression(value=20),
                    ),
                    when_true=LiteralExpression(value="700.00"),
                    when_false=LiteralExpression(value="500.00"),
                ),
            )
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Out",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode="HALF_UP",
            )
        ],
        control_cases=[],
    )
    lowered = lower_to_v0_1(pkg)
    assert len(lowered.rules) == 1

    high_roof_age = evaluate_package(
        package=lowered, risk=RiskInput(values={"roof_age": 25}), effective_date=pkg.effective_period.start
    )
    assert high_roof_age.final_premium == Decimal("700.00")

    low_roof_age = evaluate_package(
        package=lowered, risk=RiskInput(values={"roof_age": 5}), effective_date=pkg.effective_period.start
    )
    assert low_roof_age.final_premium == Decimal("500.00")


def test_nested_round_expression_raises_lowering_not_supported():
    pkg = make_minimal_package(
        calculations=[
            CalculationNodeV2(
                id="final_premium",
                name="Nested round",
                expression=BinaryExpression(
                    operator="ADD",
                    left=RoundExpression(operand=LiteralExpression(value="1.005"), scale=2, rounding_mode="HALF_UP"),
                    right=LiteralExpression(value="1.00"),
                ),
            )
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Out",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode="HALF_UP",
            )
        ],
        control_cases=[],
    )
    with pytest.raises(LoweringNotSupportedError):
        lower_to_v0_1(pkg)


def test_divide_by_zero_still_rejected_after_lowering():
    from app.engines.oracle.errors import ExpressionEvaluationError

    pkg = make_minimal_package(
        calculations=[
            CalculationNodeV2(
                id="final_premium",
                name="Divide by zero",
                expression=BinaryExpression(
                    operator="DIVIDE", left=LiteralExpression(value="1.00"), right=LiteralExpression(value="0")
                ),
            )
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Out",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode="HALF_UP",
            )
        ],
        control_cases=[],
    )
    lowered = lower_to_v0_1(pkg)
    with pytest.raises(ExpressionEvaluationError):
        evaluate_package(package=lowered, risk=RiskInput(values={"roof_age": 25}), effective_date=pkg.effective_period.start)
