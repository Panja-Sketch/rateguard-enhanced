"""The workbook compilation receipt (locked doc section 4.1.B: "Workbook
compilation receipt listing sheets, tables, formulas, supported/unsupported
constructs, normalized counts, warnings, content hash, and verification
status"). This is the single object `compile_workbook` always returns --
success, `REVIEW_REQUIRED`, or `REJECTED` -- so a caller never has to
distinguish "an exception happened" from "compilation reported a problem".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ipir.v0_2.control_cases import ControlCaseResult
from app.ipir.v0_2.package import IPIRPackageV2

CompileStatus = Literal["VERIFIED", "REVIEW_REQUIRED", "REJECTED"]


class WorkbookError(BaseModel):
    """One rejection reason, in the locked doc section 13.5 error shape."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: list[dict] = []


class CompilationReceipt(BaseModel):
    """The full record of one workbook compilation attempt."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    artifact_sha256: str
    compiler_version: str
    sheets_found: list[str] = []
    supported_constructs: list[str] = []
    rejected_constructs: list[str] = []
    node_counts: dict[str, int] = {}
    control_case_results: list[ControlCaseResult] = []
    warnings: list[str] = []
    errors: list[WorkbookError] = []
    metadata: dict[str, str] = {}
    status: CompileStatus
    package: IPIRPackageV2 | None = None

    def is_verified(self) -> bool:
        return self.status == "VERIFIED"
