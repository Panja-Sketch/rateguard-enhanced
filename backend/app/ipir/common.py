import re
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, model_validator

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


def validate_identifier_string(v: str) -> str:
    """Validates that an ID is machine-readable and contains no whitespace or special characters."""
    v_str = str(v).strip()
    if not v_str:
        raise ValueError("Identifier cannot be empty")
    if not ID_PATTERN.match(v_str):
        raise ValueError(
            f"Identifier '{v_str}' must contain only alphanumeric characters, "
            "underscores, hyphens, or dots"
        )
    return v_str


class EffectivePeriod(BaseModel):
    """Represents a date-range period for rate versioning and temporal evaluation."""

    start: date
    end: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "EffectivePeriod":
        if self.end is not None and self.end < self.start:
            raise ValueError(f"End date ({self.end}) cannot precede start date ({self.start})")
        return self


class NodeReference(BaseModel):
    """Explicit reference to another IPIR node or variable by identifier."""

    ref: str

    @model_validator(mode="after")
    def validate_ref(self) -> "NodeReference":
        self.ref = validate_identifier_string(self.ref)
        return self


class LiteralValue(BaseModel):
    """Typed literal value (Decimal, integer, string, or boolean) for expressions and conditions."""

    value: Decimal | int | str | bool

    def model_post_init(self, __context: Any) -> None:
        if isinstance(self.value, float):
            # Prevent silent float precision loss by converting float to Decimal string
            object.__setattr__(self, "value", Decimal(str(self.value)))
