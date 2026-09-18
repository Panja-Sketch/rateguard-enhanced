"""Regression coverage for the recursive `extra="forbid"` hardening added to
every v0.1 IPIR leaf model (docs/implementation/IMPLEMENTATION_PLAN.md, CP1):
a typo'd/unknown field nested anywhere in a package must be a validation
error, not silently dropped."""

import pytest
from pydantic import ValidationError

from app.ipir.calculations import CalculationNode
from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference
from app.ipir.constraints import PremiumConstraint, PricingFee, RoundingRule
from app.ipir.expressions import Expression
from app.ipir.inputs import PricingInput
from app.ipir.modifiers import PricingModifier
from app.ipir.package import PricingConstant
from app.ipir.product import CoverageDefinition, InsuranceProduct, Jurisdiction, PricingOutput
from app.ipir.provenance import Provenance, SourceReference
from app.ipir.rules import ComparisonCondition, LogicalCondition, PricingRule


@pytest.mark.parametrize(
    "cls,kwargs",
    [
        (EffectivePeriod, {"start": "2026-01-01"}),
        (NodeReference, {"ref": "x"}),
        (LiteralValue, {"value": 1}),
        (PricingInput, {"id": "x", "name": "X", "data_type": "INTEGER"}),
        (RoundingRule, {"id": "x", "precision": 2, "mode": "HALF_UP"}),
        (PremiumConstraint, {"id": "x", "name": "X", "constraint_type": "MINIMUM", "amount": "1.00", "applies_to": "y"}),
        (PricingFee, {"id": "x", "name": "X", "amount": "1.00", "applies_to": "y"}),
        (CalculationNode, {"id": "x", "name": "X", "expression": LiteralValue(value=1)}),
        (Jurisdiction, {}),
        (InsuranceProduct, {"id": "x", "name": "X", "line": "HOMEOWNERS", "jurisdiction": Jurisdiction()}),
        (CoverageDefinition, {"id": "x", "name": "X"}),
        (PricingOutput, {"id": "x", "name": "X", "source_ref": "y"}),
        (
            PricingModifier,
            {"id": "x", "name": "X", "modifier_type": "FLAT_DISCOUNT", "applies_to": "y", "value": "1.00"},
        ),
        (ComparisonCondition, {"left": LiteralValue(value=1), "operator": "EQ", "right": LiteralValue(value=1)}),
        (
            LogicalCondition,
            {
                "operator": "AND",
                "conditions": [ComparisonCondition(left=LiteralValue(value=1), operator="EQ", right=LiteralValue(value=1))],
            },
        ),
        (
            PricingRule,
            {
                "id": "x",
                "name": "X",
                "condition": ComparisonCondition(left=LiteralValue(value=1), operator="EQ", right=LiteralValue(value=1)),
                "when_true": LiteralValue(value=1),
                "when_false": LiteralValue(value=0),
            },
        ),
        (SourceReference, {"source_type": "ACTUARIAL_SPEC", "source_id": "x"}),
        (Provenance, {}),
        (Expression, {"operator": "ADD", "operands": [LiteralValue(value=1), LiteralValue(value=2)]}),
        (PricingConstant, {"id": "x", "name": "X", "value": "1.00"}),
    ],
)
def test_model_rejects_unknown_field(cls, kwargs):
    cls(**kwargs)  # sanity: valid construction still works
    with pytest.raises(ValidationError):
        cls(**kwargs, this_field_does_not_exist="boom")
