from pydantic import BaseModel, model_validator

from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference, validate_identifier_string
from app.ipir.enums import ComparisonOperator, LogicalOperator
from app.ipir.expressions import Expression
from app.ipir.provenance import Provenance


class ComparisonCondition(BaseModel):
    """Relational comparison condition between references, literals, or expressions."""

    left: NodeReference | LiteralValue | Expression
    operator: ComparisonOperator
    right: NodeReference | LiteralValue | Expression


class LogicalCondition(BaseModel):
    """Logical combination (AND/OR) of boolean conditions."""

    operator: LogicalOperator
    conditions: list["ComparisonCondition | LogicalCondition"]

    @model_validator(mode="after")
    def validate_conditions(self) -> "LogicalCondition":
        if len(self.conditions) < 1:
            raise ValueError("LogicalCondition requires at least 1 child condition")
        return self


LogicalCondition.model_rebuild()

Condition = ComparisonCondition | LogicalCondition


class PricingRule(BaseModel):
    """Declarative conditional pricing rule (IF condition THEN value ELSE value)."""

    id: str
    name: str
    condition: ComparisonCondition | LogicalCondition
    when_true: Expression | NodeReference | LiteralValue
    when_false: Expression | NodeReference | LiteralValue
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_rule(self) -> "PricingRule":
        self.id = validate_identifier_string(self.id)
        return self
