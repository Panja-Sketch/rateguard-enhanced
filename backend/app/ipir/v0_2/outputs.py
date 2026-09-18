from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ipir.enums import RoundingMode
from app.ipir.v0_2.common import validate_identifier_string_v2


class PricingOutputV2(BaseModel):
    """A final output declaration with mandatory, explicit currency/scale/
    rounding (locked doc section 6.2: "Every output specifies currency,
    scale, and rounding mode... never inherited from a runtime default"). No
    field here has a default — Pydantic enforces presence, satisfying the
    "explicit, not implicit" requirement structurally."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    source_ref: str
    currency: str
    scale: int = Field(ge=0, le=10)
    rounding_mode: RoundingMode

    @model_validator(mode="after")
    def _validate_ids(self) -> "PricingOutputV2":
        self.id = validate_identifier_string_v2(self.id)
        self.source_ref = validate_identifier_string_v2(self.source_ref)
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"currency must be a 3-letter uppercase ISO code, got '{self.currency}'")
        return self
