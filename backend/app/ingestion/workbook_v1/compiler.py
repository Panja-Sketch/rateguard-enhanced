"""Top-level Controlled Workbook v1 compiler entrypoint (locked doc section
5.3, the 12 compilation stages). `compile_workbook` never raises to its
caller -- every `WorkbookRejectionError` raised anywhere in the pipeline is
caught exactly once, here, and turned into a `REJECTED` `CompilationReceipt`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.ipir.v0_2.compat import run_control_cases
from app.ingestion.workbook_v1 import mapping
from app.ingestion.workbook_v1.errors import WorkbookRejectionError
from app.ingestion.workbook_v1.formulas import scan_cell_for_unsupported_formula
from app.ingestion.workbook_v1.limits import COMPILER_VERSION
from app.ingestion.workbook_v1.receipt import CompilationReceipt, WorkbookError
from app.ingestion.workbook_v1.sanitize import sanitize_display_and_export
from app.ingestion.workbook_v1.sheets import (
    REQUIRED_SHEETS,
    extract_rows,
    load_workbook_safely,
    validate_required_sheets_and_columns,
)
from app.ingestion.workbook_v1.zip_safety import validate_archive_safety, validate_upload_basics


def _scan_all_required_sheets_for_unsupported_formulas(wb, sheets_present: list[str]) -> None:
    from app.ingestion.workbook_v1.sheets import all_used_cells

    for sheet_name, cell_coord, value in all_used_cells(wb, sheets_present):
        scan_cell_for_unsupported_formula(value, sheet=sheet_name, cell=cell_coord)


def compile_workbook(content: bytes, filename: str) -> CompilationReceipt:
    """Compiles raw `.xlsx` bytes into a `CompilationReceipt`. Always
    returns a receipt; never raises. See module docstring and locked doc
    sections 2.3/5.3/9 (session instructions) for the exact status logic."""
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    compiled_at = datetime.now(UTC).isoformat()

    try:
        # Stages 1-3 (locked doc 5.3): quarantine (caller's responsibility --
        # this function only ever receives already-quarantined bytes and a
        # display filename, never a trusted filesystem path), hash, ZIP/XML
        # safety.
        validate_upload_basics(content, filename)
        validate_archive_safety(content)

        # Stage 4: non-executing, formula-preserving load.
        wb = load_workbook_safely(content)
        sheets_found = list(wb.sheetnames)

        # Stage 5: required sheets/columns.
        header_maps = validate_required_sheets_and_columns(wb)

        # Stage 6 (defensive half): scan every required-sheet cell for a raw
        # Excel formula string referencing a disallowed function, before any
        # mini-DSL parsing happens.
        _scan_all_required_sheets_for_unsupported_formulas(wb, list(REQUIRED_SHEETS))

        rows = {
            sheet_name: extract_rows(wb, sheet_name, headers)
            for sheet_name, headers in header_maps.items()
        }

        metadata = mapping.build_metadata(rows["RG_METADATA"])
        inputs = mapping.build_inputs(rows["RG_INPUTS"])
        constants = mapping.build_constants(rows["RG_CONSTANTS"])
        tables = mapping.build_tables(rows["RG_TABLES"])
        # Stage 6 (mini-DSL half) + stage 8 (division-by-zero/missing-rounding
        # detection at parse time): building calculation nodes parses every
        # RG_CALCULATIONS row's operator/operand mini-DSL and rejects a
        # literal-zero DIVIDE divisor immediately.
        calculations = mapping.build_calculations(rows["RG_CALCULATIONS"])
        outputs = mapping.build_outputs(rows["RG_OUTPUTS"], calculations)
        control_cases = mapping.build_control_cases(rows["RG_CONTROL_CASES"])

        # Stages 7-9: reference resolution, dependency-DAG cycle detection,
        # range gap/overlap detection, and canonical-IPIR construction all
        # happen inside `IPIRPackageV2`'s own semantic validator (session 1,
        # CP2) -- reused here, not reimplemented.
        package = mapping.build_package(
            metadata=metadata,
            inputs=inputs,
            constants=constants,
            tables=tables,
            calculations=calculations,
            outputs=outputs,
            control_cases=control_cases,
            artifact_sha256=artifact_sha256,
            compiled_at=compiled_at,
        )

    except WorkbookRejectionError as exc:
        return CompilationReceipt(
            artifact_sha256=artifact_sha256,
            compiler_version=COMPILER_VERSION,
            sheets_found=[],
            status="REJECTED",
            errors=[WorkbookError(**exc.to_error_dict())],
        )

    # Stages 10-11: run every RG_CONTROL_CASES row through the existing,
    # unmodified oracle via the v0.2->v0.1 lowering boundary (session 1,
    # CP3). No second pricing evaluator is written here.
    warnings: list[str] = []
    control_case_results = []
    if not package.control_cases:
        warnings.append(
            "No RG_CONTROL_CASES rows were declared; the package cannot be VERIFIED "
            "without at least one passing control case (locked doc section 17.1)."
        )
    else:
        try:
            control_case_results = run_control_cases(package)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            warnings.append(f"Control-case execution failed: {type(exc).__name__}: {exc}")

    all_cases_passed = bool(control_case_results) and all(r.passed for r in control_case_results)

    # Stage 12: final status (locked doc section 2.3 / session instructions
    # item 9 -- "no false PASS"). VERIFIED requires zero errors, zero
    # rejected constructs, and every declared control case passing within
    # tolerance.
    if all_cases_passed and not warnings:
        status = "VERIFIED"
    else:
        status = "REVIEW_REQUIRED"

    node_counts = {
        "inputs": len(package.inputs),
        "constants": len(package.constants),
        "tables": len(package.tables),
        "calculations": len(package.calculations),
        "outputs": len(package.outputs),
        "control_cases": len(package.control_cases),
    }
    _kind_to_operator_label = {
        "ROUND": "ROUND",
        "TABLE_LOOKUP": "LOOKUP",
        "CONDITIONAL": "IF",
    }
    used_operators = sorted(
        {
            getattr(c.expression, "operator", None) or _kind_to_operator_label.get(c.expression.kind, c.expression.kind)
            for c in package.calculations
        }
    )

    metadata_display = {k: sanitize_display_and_export(v) for k, v in metadata.items()}

    return CompilationReceipt(
        artifact_sha256=artifact_sha256,
        compiler_version=COMPILER_VERSION,
        sheets_found=sheets_found,
        supported_constructs=used_operators,
        rejected_constructs=[],
        node_counts=node_counts,
        control_case_results=control_case_results,
        warnings=warnings,
        errors=[],
        metadata=metadata_display,
        status=status,
        package=package,
    )
