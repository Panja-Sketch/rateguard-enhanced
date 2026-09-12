from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.ipir.common import EffectivePeriod, validate_identifier_string
from app.ipir.enums import ConstraintType, RoundingMode
from app.ipir.provenance import Provenance


class RoundingRule(BaseModel):
    """Specification of rounding direction and decimal precision for calculation steps."""

    id: str
    precision: int
    mode: RoundingMode

    @model_validator(mode="after")
    def validate_rule(self) -> "RoundingRule":
        self.id = validate_identifier_string(self.id)
        if self.precision < 0 or self.precision > 10:
            raise ValueError(
                f"RoundingRule precision must be between 0 and 10 inclusive, got {self.precision}"
            )
        return self


class PremiumConstraint(BaseModel):
    """Specification of minimum or maximum premium bounds."""

    id: str
    name: str
    constraint_type: ConstraintType
    amount: Decimal
    applies_to: str
    sequence: int | None = None
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_constraint(self) -> "PremiumConstraint":
        self.id = validate_identifier_string(self.id)
        self.applies_to = validate_identifier_string(self.applies_to)
        return self


class PricingFee(BaseModel):
    """Specification of fixed policy or transaction fees."""

    id: str
    name: str
    amount: Decimal
    applies_to: str
    sequence: int | None = None
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_fee(self) -> "PricingFee":
        self.id = validate_identifier_string(self.id)
        self.applies_to = validate_identifier_string(self.applies_to)
        return self
