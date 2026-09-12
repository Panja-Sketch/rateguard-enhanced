from pydantic import BaseModel, Field, model_validator

from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference, validate_identifier_string
from app.ipir.constraints import RoundingRule
from app.ipir.expressions import Expression
from app.ipir.provenance import Provenance


class CalculationNode(BaseModel):
    """Execution step node representing an intermediate calculation or rate factor application."""

    id: str
    name: str
    expression: Expression | NodeReference | LiteralValue
    depends_on: list[str] = Field(default_factory=list)
    rounding_rule_ref: str | None = None
    rounding_rule: RoundingRule | None = None
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_node(self) -> "CalculationNode":
        self.id = validate_identifier_string(self.id)
        self.depends_on = [validate_identifier_string(dep) for dep in self.depends_on]
        if self.rounding_rule_ref is not None:
            self.rounding_rule_ref = validate_identifier_string(self.rounding_rule_ref)
        if self.rounding_rule is not None and not self.rounding_rule_ref:
            self.rounding_rule_ref = self.rounding_rule.id
        return self
