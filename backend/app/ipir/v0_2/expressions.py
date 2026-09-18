"""IPIR v0.2 discriminated-union expression model (locked doc section 6.3).

Replaces v0.1's single flat `Expression(operator, operands)` shape with eight
distinct, `kind`-tagged node types so an invalid combination (e.g. a DIVIDE
with no declared zero-division behavior, or a ROUND with no scale) is a
schema-validation error rather than something a generic evaluator might
mishandle silently. See `app.ipir.v0_2.compat` for how each node type lowers
onto the existing, unmodified v0.1 evaluator.
"""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ipir.enums import ComparisonOperator, LogicalOperator, RoundingMode
from app.ipir.v0_2.common import validate_identifier_string_v2

# Locked doc section 5.2 / 6.3: only these arithmetic operators are supported
# for Binary/Nary expressions; MIN/MAX/ADD/MULTIPLY are N-ary capable,
# SUBTRACT/DIVIDE are binary-only.
BinaryOperator = Literal["ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "MIN", "MAX"]
NaryOperator = Literal["ADD", "MULTIPLY", "MIN", "MAX"]

# Only REJECT is implemented by the v0.1 lowering boundary in this release
# (v0.1's evaluator has always unconditionally rejected division by zero).
# The field is still required and explicit per locked doc section 6.2
# ("Division nodes declare zero behavior; the default is reject") rather than
# silently defaulted at the evaluator level.
OnZeroBehavior = Literal["REJECT"]


class LiteralExpression(BaseModel):
    """A constant Decimal, integer, string, or boolean value."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["LITERAL"] = "LITERAL"
    value: Decimal | int | str | bool


class ReferenceExpression(BaseModel):
    """A reference to another node's already-resolved value (input, constant,
    table, or calculation) by identifier."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["REFERENCE"] = "REFERENCE"
    ref: str

    @model_validator(mode="after")
    def _validate_ref(self) -> "ReferenceExpression":
        self.ref = validate_identifier_string_v2(self.ref)
        return self


class UnaryExpression(BaseModel):
    """A single-operand transform. Only NEGATE is supported in this release."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["UNARY"] = "UNARY"
    operator: Literal["NEGATE"]
    operand: "ExpressionV2"


class BinaryExpression(BaseModel):
    """A strictly two-operand arithmetic expression."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["BINARY"] = "BINARY"
    operator: BinaryOperator
    left: "ExpressionV2"
    right: "ExpressionV2"
    on_zero_behavior: OnZeroBehavior = "REJECT"

    @model_validator(mode="after")
    def _validate_zero_behavior(self) -> "BinaryExpression":
        if self.operator == "DIVIDE" and self.on_zero_behavior != "REJECT":
            raise ValueError(
                "DIVIDE.on_zero_behavior only supports 'REJECT' in this release; "
                f"got '{self.on_zero_behavior}'."
            )
        return self


class NaryExpression(BaseModel):
    """A variadic (2+) commutative-associative arithmetic expression."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["NARY"] = "NARY"
    operator: NaryOperator
    operands: list["ExpressionV2"]

    @model_validator(mode="after")
    def _validate_arity(self) -> "NaryExpression":
        if len(self.operands) < 2:
            raise ValueError(f"NaryExpression '{self.operator}' requires at least 2 operands.")
        return self


class ComparisonConditionV2(BaseModel):
    """A relational comparison used only inside a ConditionalExpression's
    condition (locked doc section 5.2's limited IF)."""

    model_config = ConfigDict(extra="forbid")

    left: "ExpressionV2"
    operator: ComparisonOperator
    right: "ExpressionV2"


class LogicalConditionV2(BaseModel):
    """An AND/OR combination of comparison or logical conditions."""

    model_config = ConfigDict(extra="forbid")

    operator: LogicalOperator
    conditions: list["ComparisonConditionV2 | LogicalConditionV2"]

    @model_validator(mode="after")
    def _validate_conditions(self) -> "LogicalConditionV2":
        if len(self.conditions) < 1:
            raise ValueError("LogicalConditionV2 requires at least 1 child condition.")
        return self


ConditionV2 = ComparisonConditionV2 | LogicalConditionV2


class ConditionalExpression(BaseModel):
    """A limited IF/THEN/ELSE (locked doc section 5.2)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["CONDITIONAL"] = "CONDITIONAL"
    condition: ConditionV2
    when_true: "ExpressionV2"
    when_false: "ExpressionV2"


class TableLookupExpression(BaseModel):
    """A reference to a declared RateTable's resolved factor for the current
    evaluation context. Dimension inputs are not supplied at the call site:
    like v0.1, a table's dimensions are declared on the table itself and
    resolved once per evaluation (see `app.ipir.v0_2.compat`)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["TABLE_LOOKUP"] = "TABLE_LOOKUP"
    table_id: str

    @model_validator(mode="after")
    def _validate_table_id(self) -> "TableLookupExpression":
        self.table_id = validate_identifier_string_v2(self.table_id)
        return self


class RoundExpression(BaseModel):
    """Explicit rounding of a nested expression. `scale` and `rounding_mode`
    are always required (locked doc section 6.2: "never inherited from a
    runtime default")."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ROUND"] = "ROUND"
    operand: "ExpressionV2"
    scale: int = Field(ge=0, le=10)
    rounding_mode: RoundingMode


ExpressionV2 = Annotated[
    LiteralExpression | ReferenceExpression | UnaryExpression | BinaryExpression | NaryExpression | ConditionalExpression | TableLookupExpression | RoundExpression,
    Field(discriminator="kind"),
]

UnaryExpression.model_rebuild()
BinaryExpression.model_rebuild()
NaryExpression.model_rebuild()
ConditionalExpression.model_rebuild()
RoundExpression.model_rebuild()
ComparisonConditionV2.model_rebuild()
LogicalConditionV2.model_rebuild()
