from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ipir.calculations import CalculationNode
from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference
from app.ipir.constraints import PremiumConstraint, RoundingRule
from app.ipir.enums import (
    ComparisonOperator,
    ConstraintType,
    ExpressionOperator,
    InputDataType,
    InsuranceLine,
    LogicalOperator,
    ModifierType,
    RoundingMode,
)
from app.ipir.expressions import Expression
from app.ipir.inputs import PricingInput
from app.ipir.modifiers import PricingModifier
from app.ipir.package import IPIRPackage, PricingConstant
from app.ipir.product import CoverageDefinition, InsuranceProduct, Jurisdiction, PricingOutput
from app.ipir.rules import ComparisonCondition, LogicalCondition, PricingRule


def test_full_ipir_package_creation() -> None:
    pkg = IPIRPackage(
        id="AZ_HO3_2026",
        name="Arizona Homeowners 2026",
        version="1.0.0",
        product=InsuranceProduct(
            id="HO3",
            name="Homeowners 3",
            line=InsuranceLine.HOMEOWNERS,
            jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
        ),
        effective_period=EffectivePeriod(start=date(2026, 1, 1)),
        inputs=[
            PricingInput(
                id="roof_age",
                name="Roof Age",
                data_type=InputDataType.INTEGER,
                minimum=0,
                maximum=100,
            )
        ],
        constants=[PricingConstant(id="base_rate", name="Base Rate", value=Decimal("500.00"))],
        calculations=[
            CalculationNode(
                id="total_premium",
                name="Total Premium",
                expression=Expression(
                    operator=ExpressionOperator.MULTIPLY,
                    operands=[NodeReference(ref="base_rate"), LiteralValue(value=Decimal("1.10"))],
                ),
                depends_on=["base_rate"],
            )
        ],
        rounding_rules=[
            RoundingRule(id="standard_money_rounding", precision=2, mode=RoundingMode.HALF_UP)
        ],
        modifiers=[
            PricingModifier(
                id="claims_free_discount",
                name="Claims Free Discount",
                modifier_type=ModifierType.PERCENTAGE_DISCOUNT,
                applies_to="total_premium",
                value=Decimal("0.10"),
            )
        ],
        constraints=[
            PremiumConstraint(
                id="min_policy_premium",
                name="Minimum Policy Premium",
                constraint_type=ConstraintType.MINIMUM,
                amount=Decimal("100.00"),
                applies_to="total_premium",
            )
        ],
        coverages=[
            CoverageDefinition(
                id="coverage_a",
                name="Dwelling Coverage",
                calculation_refs=["total_premium"],
            )
        ],
        outputs=[
            PricingOutput(
                id="final_premium",
                name="Final Premium",
                source_ref="total_premium",
            )
        ],
    )

    assert pkg.id == "AZ_HO3_2026"
    assert len(pkg.inputs) == 1
    assert len(pkg.constants) == 1
    assert len(pkg.calculations) == 1


def test_duplicate_node_id_rejection() -> None:
    with pytest.raises(ValidationError, match="Duplicate node ID 'base_rate'"):
        IPIRPackage(
            id="dup_pkg",
            name="Duplicate ID Package",
            version="1.0.0",
            product=InsuranceProduct(
                id="HO3",
                name="Homeowners",
                line=InsuranceLine.HOMEOWNERS,
                jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
            ),
            effective_period=EffectivePeriod(start=date(2026, 1, 1)),
            inputs=[
                PricingInput(id="base_rate", name="Roof Age Input", data_type=InputDataType.INTEGER)
            ],
            constants=[
                PricingConstant(id="base_rate", name="Base Rate Constant", value=Decimal("500.00"))
            ],
        )


def test_nonexistent_output_source_ref_rejection() -> None:
    with pytest.raises(ValidationError, match="references nonexistent source node 'missing_node'"):
        IPIRPackage(
            id="bad_output_pkg",
            name="Bad Output Ref Package",
            version="1.0.0",
            product=InsuranceProduct(
                id="HO3",
                name="Homeowners",
                line=InsuranceLine.HOMEOWNERS,
                jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
            ),
            effective_period=EffectivePeriod(start=date(2026, 1, 1)),
            outputs=[PricingOutput(id="final_out", name="Final Output", source_ref="missing_node")],
        )


def test_nested_rule_condition_creation() -> None:
    cond1 = ComparisonCondition(
        left=NodeReference(ref="roof_age"),
        operator=ComparisonOperator.GTE,
        right=LiteralValue(value=21),
    )
    cond2 = ComparisonCondition(
        left=NodeReference(ref="has_pool"),
        operator=ComparisonOperator.EQ,
        right=LiteralValue(value=True),
    )
    log_cond = LogicalCondition(operator=LogicalOperator.AND, conditions=[cond1, cond2])

    rule = PricingRule(
        id="surcharge_rule",
        name="Roof & Pool Combined Surcharge Rule",
        condition=log_cond,
        when_true=LiteralValue(value=Decimal("1.25")),
        when_false=LiteralValue(value=Decimal("1.00")),
    )

    assert rule.id == "surcharge_rule"
    assert isinstance(rule.condition, LogicalCondition)
