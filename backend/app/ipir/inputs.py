from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.ipir.common import validate_identifier_string
from app.ipir.enums import InputDataType
from app.ipir.provenance import Provenance


class PricingInput(BaseModel):
    """Definition of a risk factor or policy input variable required for rating."""

    id: str
    name: str
    data_type: InputDataType
    required: bool = True
    description: str | None = None
    unit: str | None = None
    allowed_values: list[str] | None = None
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "PricingInput":
        self.id = validate_identifier_string(self.id)

        if self.minimum is not None and self.maximum is not None:
            if Decimal(str(self.minimum)) > Decimal(str(self.maximum)):
                raise ValueError(
                    f"Input '{self.id}' minimum ({self.minimum}) "
                    f"cannot exceed maximum ({self.maximum})"
                )

        if self.data_type == InputDataType.BOOLEAN and self.allowed_values is not None:
            raise ValueError(f"Boolean input '{self.id}' should not define allowed_values")

        return self
