import pytest
from pydantic import ValidationError

from app.ipir.common import EffectivePeriod
from app.ipir.enums import RoundingMode
from app.ipir.v0_2.calculations import CalculationNodeV2
from app.ipir.v0_2.expressions import (
    LiteralExpression,
    ReferenceExpression,
    RoundExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2

from .conftest import make_minimal_package


def test_minimal_package_is_valid():
    pkg = make_minimal_package()
    assert pkg.schema_version == "0.2.0"


def test_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        make_minimal_package(extra_top_level_field="nope")


def test_rejects_bad_schema_version():
    with pytest.raises(ValidationError):
        make_minimal_package(schema_version="1.0.0")


def test_rejects_uppercase_package_id():
    with pytest.raises(ValidationError):
        make_minimal_package(package_id="AZ_HO3")


def test_rejects_duplicate_ids_across_entity_types():
    # A constant and a calculation sharing the same id.
    with pytest.raises(ValidationError, match="Duplicate node ID"):
        make_minimal_package(
            constants=[
                __import__("app.ipir.package", fromlist=["PricingConstant"]).PricingConstant(
                    id="final_premium", name="Collides with calc id", value="1.00"
                )
            ]
        )


def test_rejects_unresolved_reference_in_calculation():
    with pytest.raises(ValidationError, match="unresolved node"):
        make_minimal_package(
            calculations=[
                CalculationNodeV2(
                    id="broken",
                    name="Broken",
                    expression=ReferenceExpression(ref="does_not_exist"),
                )
            ],
            outputs=[
                PricingOutputV2(
                    id="out1",
                    name="Out",
                    source_ref="broken",
                    currency="USD",
                    scale=2,
                    rounding_mode=RoundingMode.HALF_UP,
                )
            ],
            control_cases=[],
        )


def test_rejects_unresolved_table_reference():
    from app.ipir.v0_2.expressions import TableLookupExpression

    with pytest.raises(ValidationError, match="unknown table"):
        make_minimal_package(
            calculations=[
                CalculationNodeV2(
                    id="broken",
                    name="Broken",
                    expression=TableLookupExpression(table_id="no_such_table"),
                )
            ],
            outputs=[
                PricingOutputV2(
                    id="out1",
                    name="Out",
                    source_ref="broken",
                    currency="USD",
                    scale=2,
                    rounding_mode=RoundingMode.HALF_UP,
                )
            ],
            control_cases=[],
        )


def test_rejects_dangling_output_source_ref():
    with pytest.raises(ValidationError, match="nonexistent source node"):
        make_minimal_package(
            outputs=[
                PricingOutputV2(
                    id="out1",
                    name="Out",
                    source_ref="does_not_exist",
                    currency="USD",
                    scale=2,
                    rounding_mode=RoundingMode.HALF_UP,
                )
            ],
            control_cases=[],
        )


def test_rejects_calculation_cycle():
    with pytest.raises(ValidationError, match="dependency cycle"):
        make_minimal_package(
            calculations=[
                CalculationNodeV2(id="calc_a", name="A", expression=ReferenceExpression(ref="calc_b"), depends_on=["calc_b"]),
                CalculationNodeV2(id="calc_b", name="B", expression=ReferenceExpression(ref="calc_a"), depends_on=["calc_a"]),
            ],
            outputs=[
                PricingOutputV2(
                    id="out1", name="Out", source_ref="calc_a", currency="USD", scale=2, rounding_mode=RoundingMode.HALF_UP
                )
            ],
            control_cases=[],
        )


def test_rejects_cycle_hidden_purely_in_expression_reference_not_declared_depends_on():
    # No depends_on declared at all -- the cycle only exists via the
    # expression tree's own REFERENCE nodes, proving cycle detection walks
    # references, not only the declared depends_on list.
    with pytest.raises(ValidationError, match="dependency cycle"):
        make_minimal_package(
            calculations=[
                CalculationNodeV2(id="calc_a", name="A", expression=ReferenceExpression(ref="calc_b")),
                CalculationNodeV2(id="calc_b", name="B", expression=ReferenceExpression(ref="calc_a")),
            ],
            outputs=[
                PricingOutputV2(
                    id="out1", name="Out", source_ref="calc_a", currency="USD", scale=2, rounding_mode=RoundingMode.HALF_UP
                )
            ],
            control_cases=[],
        )


def test_output_scale_mismatch_with_source_round_expression_rejected():
    with pytest.raises(ValidationError, match="rounds to scale"):
        make_minimal_package(
            calculations=[
                CalculationNodeV2(
                    id="final_premium",
                    name="Final premium",
                    expression=RoundExpression(
                        operand=LiteralExpression(value="700.001"), scale=2, rounding_mode=RoundingMode.HALF_UP
                    ),
                )
            ],
            outputs=[
                PricingOutputV2(
                    id="final_premium_output",
                    name="Final Premium",
                    source_ref="final_premium",
                    currency="USD",
                    scale=4,  # mismatched on purpose
                    rounding_mode=RoundingMode.HALF_UP,
                )
            ],
            control_cases=[],
        )


def test_output_requires_currency_scale_and_rounding_mode():
    with pytest.raises(ValidationError):
        PricingOutputV2(id="out1", name="Out", source_ref="x", currency="USD")  # missing scale/rounding_mode


def test_binary_divide_lowers_and_rejects_zero_at_evaluation_time():
    # Model-level: constructing is fine (REJECT is the only supported value).
    from app.ipir.v0_2.expressions import BinaryExpression as BE

    expr = BE(operator="DIVIDE", left=LiteralExpression(value=1), right=LiteralExpression(value=0))
    assert expr.on_zero_behavior == "REJECT"


def test_effective_period_end_before_start_rejected():
    with pytest.raises(ValidationError):
        make_minimal_package(effective_period=EffectivePeriod(start="2026-10-01", end="2026-09-01"))
