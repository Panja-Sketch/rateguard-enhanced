from pydantic import BaseModel, model_validator

from app.ipir.common import LiteralValue, NodeReference
from app.ipir.enums import ExpressionOperator


class Expression(BaseModel):
    """Declarative AST node for mathematical calculations."""

    operator: ExpressionOperator
    operands: list["Expression | NodeReference | LiteralValue"]

    @model_validator(mode="after")
    def validate_operands(self) -> "Expression":
        op_count = len(self.operands)
        if self.operator == ExpressionOperator.DIVIDE:
            if op_count != 2:
                raise ValueError(f"DIVIDE operator requires exactly 2 operands, got {op_count}")
        elif self.operator == ExpressionOperator.ROUND:
            if op_count not in (1, 2):
                raise ValueError(f"ROUND operator requires 1 or 2 operands, got {op_count}")
        elif self.operator in (
            ExpressionOperator.ADD,
            ExpressionOperator.SUBTRACT,
            ExpressionOperator.MULTIPLY,
            ExpressionOperator.MIN,
            ExpressionOperator.MAX,
        ):
            if op_count < 2:
                raise ValueError(
                    f"{self.operator.value} operator requires at least 2 operands, got {op_count}"
                )
        return self


Expression.model_rebuild()
