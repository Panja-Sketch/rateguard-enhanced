"""Typed rejection model for the Controlled Workbook v1 compiler, matching
the locked doc section 13.5 error shape:

    {"code": "...", "message": "...", "details": [{"sheet", "cell", "function"}]}

A single exception type carries everything `compile_workbook` needs to build
the receipt's error entry, so callers/tests assert on `.code` rather than on
exception subclass identity -- one typed carrier, many distinct codes.
"""

from pydantic import BaseModel, ConfigDict


class WorkbookErrorDetail(BaseModel):
    """One location/context entry for a rejection (locked doc section 13.5
    `details[]`). All fields optional since not every rejection reason has a
    meaningful sheet/cell/function (e.g. an oversized-file rejection has
    none of these)."""

    model_config = ConfigDict(extra="forbid")

    sheet: str | None = None
    cell: str | None = None
    function: str | None = None
    note: str | None = None


class WorkbookRejectionError(Exception):
    """A hard, fail-closed rejection raised anywhere in the compilation
    pipeline. Caught exactly once, at the top of
    `app.ingestion.workbook_v1.compiler.compile_workbook`, where it is
    turned into a `REJECTED` `CompilationReceipt`. Compilation never
    continues past this point with partial trust (locked doc sections 2.3
    and 17.3, "no false PASS")."""

    def __init__(
        self,
        code: str,
        message: str,
        details: list[WorkbookErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def to_error_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": [d.model_dump(exclude_none=True) for d in self.details],
        }
