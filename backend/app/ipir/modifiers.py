from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.ipir.common import EffectivePeriod, NodeReference, validate_identifier_string
from app.ipir.enums import ModifierType
from app.ipir.provenance import Provenance
from app.ipir.rules import ComparisonCondition, LogicalCondition


class PricingModifier(BaseModel):
    """Specification of discounts, surcharges, or credits applied to rating steps."""

    id: str
    name: str
    modifier_type: ModifierType
    applies_to: str
    value: Decimal | NodeReference
    eligibility: ComparisonCondition | LogicalCondition | None = None
    sequence: int | None = None
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_modifier(self) -> "PricingModifier":
        self.id = validate_identifier_string(self.id)
        self.applies_to = validate_identifier_string(self.applies_to)
        return self
