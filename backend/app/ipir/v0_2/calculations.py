from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ipir.common import EffectivePeriod
from app.ipir.v0_2.common import validate_identifier_string_v2
from app.ipir.v0_2.expressions import ExpressionV2


class CalculationNodeV2(BaseModel):
    """A calculation-graph node (locked doc section 5.1 `RG_CALCULATIONS` /
    section 6.1 `calculations`). Unlike v0.1's `CalculationNode`, rounding is
    expressed inline via a top-level `RoundExpression` rather than a separate
    `rounding_rule` field — see `app.ipir.v0_2.compat` for how that lowers
    onto v0.1's existing `CalculationNode.rounding_rule` mechanism."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    expression: ExpressionV2
    depends_on: list[str] = Field(default_factory=list)
    effective_period: EffectivePeriod | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _validate_ids(self) -> "CalculationNodeV2":
        self.id = validate_identifier_string_v2(self.id)
        self.depends_on = [validate_identifier_string_v2(d) for d in self.depends_on]
        return self
