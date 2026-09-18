from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.ipir.v0_2.common import validate_identifier_string_v2


class ControlCase(BaseModel):
    """A golden input/output example embedded in an IPIR v0.2 package (locked
    doc section 5.1 `RG_CONTROL_CASES` / section 6.1 `control_cases`). Run by
    `app.ipir.v0_2.compat.run_control_cases` against the existing, unmodified
    oracle after lowering — see docs/implementation/DECISIONS.md (D2)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    inputs: dict[str, Any]
    expected_outputs: dict[str, str]
    tolerance: Decimal

    @model_validator(mode="after")
    def _validate_case(self) -> "ControlCase":
        self.case_id = validate_identifier_string_v2(self.case_id)
        if self.tolerance < Decimal("0"):
            raise ValueError(f"ControlCase '{self.case_id}' tolerance must be >= 0, got {self.tolerance}")
        if not self.expected_outputs:
            raise ValueError(f"ControlCase '{self.case_id}' must declare at least one expected output.")
        return self


class ControlCaseResult(BaseModel):
    """Outcome of executing one ControlCase against an evaluated package."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    output_id: str
    expected: str
    actual: str
    difference: str
    detail: str | None = None
