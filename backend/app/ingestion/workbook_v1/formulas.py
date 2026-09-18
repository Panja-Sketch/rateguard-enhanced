"""The RG_CALCULATIONS mini-DSL (locked doc section 5.2) and the defensive
raw-Excel-formula scan (locked doc section 5.3 step 6 / acceptance scenario
A8).

Two distinct things happen in this file, and they must not be confused:

1. `RG_CALCULATIONS` rows are RateGuard's own controlled mini-DSL -- an
   `operator` column plus `operand_1`/`operand_2` columns -- never Excel
   formula strings evaluated by Excel. `parse_operand`/`parse_condition`
   interpret that mini-DSL's small, fixed grammar.
2. `scan_cell_for_unsupported_formula` defensively scans *any* cell in the
   required sheets whose text happens to start with `=` (openpyxl in
   formula-preserving mode exposes such text verbatim) and rejects any
   function name outside the allowlist, exactly matching the locked doc
   section 13.5 example and acceptance scenario A8. This catches a user (or
   attacker) who pastes a live Excel formula into a workbook cell instead of
   using the mini-DSL -- it is never itself evaluated.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ipir.v0_2.common import ID_PATTERN_V2
from app.ipir.v0_2.expressions import (
    ComparisonConditionV2,
    ConditionV2,
    ExpressionV2,
    LiteralExpression,
    LogicalConditionV2,
    ReferenceExpression,
)
from app.ingestion.workbook_v1.errors import WorkbookErrorDetail, WorkbookRejectionError

# Locked doc section 5.2: the entire supported operator/condition vocabulary.
ALLOWED_CALCULATION_OPERATORS = {
    "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "MIN", "MAX", "ROUND", "LOOKUP", "IF",
}
ALLOWED_COMPARISON_OPS = {"EQ", "NE", "LT", "LTE", "GT", "GTE"}
ALLOWED_LOGICAL_OPS = {"AND", "OR"}

# Everything the raw-formula scan will accept as a function-name token,
# should a cell happen to contain a leading-'=' Excel-formula-shaped string.
ALLOWED_FORMULA_FUNCTIONS = (
    ALLOWED_CALCULATION_OPERATORS | ALLOWED_COMPARISON_OPS | ALLOWED_LOGICAL_OPS
)

_FORMULA_FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_LOGICAL_OP_PATTERN = {op: re.compile(rf"\b{op}\b") for op in ALLOWED_LOGICAL_OPS}


def parse_operand(token: Any, *, sheet: str, cell: str) -> ExpressionV2:  # noqa: ANN401
    """Parses one mini-DSL operand token: a numeric literal (optionally
    prefixed with '#'), or an identifier reference to another declared node
    (input/constant/table/calculation)."""
    text = str(token).strip() if token is not None else ""
    if not text:
        raise WorkbookRejectionError(
            code="MALFORMED_CALCULATION_OPERAND",
            message="Calculation operand cannot be empty.",
            details=[WorkbookErrorDetail(sheet=sheet, cell=cell)],
        )

    literal_text = text[1:] if text.startswith("#") else text
    try:
        return LiteralExpression(value=Decimal(literal_text))
    except InvalidOperation:
        pass

    if ID_PATTERN_V2.match(text):
        return ReferenceExpression(ref=text)

    raise WorkbookRejectionError(
        code="MALFORMED_CALCULATION_OPERAND",
        message=(
            f"Calculation operand '{text}' is neither a numeric literal nor a "
            "valid lowercase identifier reference."
        ),
        details=[WorkbookErrorDetail(sheet=sheet, cell=cell)],
    )


def _parse_comparison(text: str, *, sheet: str, cell: str) -> ComparisonConditionV2:
    tokens = text.split()
    if len(tokens) != 3 or tokens[1].upper() not in ALLOWED_COMPARISON_OPS:
        raise WorkbookRejectionError(
            code="MALFORMED_CONDITION",
            message=(
                f"Condition '{text}' must be exactly 'LEFT OP RIGHT' with OP in "
                f"{sorted(ALLOWED_COMPARISON_OPS)}."
            ),
            details=[WorkbookErrorDetail(sheet=sheet, cell=cell)],
        )
    left_tok, op_tok, right_tok = tokens
    return ComparisonConditionV2(
        left=parse_operand(left_tok, sheet=sheet, cell=cell),
        operator=op_tok.upper(),
        right=parse_operand(right_tok, sheet=sheet, cell=cell),
    )


def parse_condition(text: Any, *, sheet: str, cell: str) -> ConditionV2:  # noqa: ANN401
    """Parses the limited IF-condition mini-DSL (locked doc section 5.2): a
    single comparison, or exactly two comparisons joined by one AND/OR.
    Deliberately narrow -- not a general boolean-expression grammar -- to
    match the locked contract's "limited IF", not arbitrary logic nesting."""
    raw = str(text).strip() if text is not None else ""
    if not raw:
        raise WorkbookRejectionError(
            code="MALFORMED_CONDITION",
            message="IF condition (operand_1) cannot be empty.",
            details=[WorkbookErrorDetail(sheet=sheet, cell=cell)],
        )

    for logic_op, pattern in _LOGICAL_OP_PATTERN.items():
        match = pattern.search(raw)
        if match:
            left_text = raw[: match.start()].strip()
            right_text = raw[match.end() :].strip()
            return LogicalConditionV2(
                operator=logic_op,
                conditions=[
                    _parse_comparison(left_text, sheet=sheet, cell=cell),
                    _parse_comparison(right_text, sheet=sheet, cell=cell),
                ],
            )
    return _parse_comparison(raw, sheet=sheet, cell=cell)


def scan_cell_for_unsupported_formula(value: object, *, sheet: str, cell: str) -> None:
    """Rejects with `UNSUPPORTED_WORKBOOK_FUNCTION` (locked doc section 13.5;
    acceptance scenario A8) if `value` is a string starting with '=' that
    references any function name outside the allowlist. openpyxl in
    formula-preserving mode (`data_only=False`) exposes such a string
    verbatim, without ever evaluating it -- this scan is purely defensive
    text inspection, not formula execution."""
    if not isinstance(value, str) or not value.startswith("="):
        return
    for match in _FORMULA_FUNCTION_PATTERN.finditer(value):
        func_name = match.group(1)
        if func_name.upper() not in ALLOWED_FORMULA_FUNCTIONS:
            raise WorkbookRejectionError(
                code="UNSUPPORTED_WORKBOOK_FUNCTION",
                message="Workbook contains a function outside the supported contract.",
                details=[WorkbookErrorDetail(sheet=sheet, cell=cell, function=func_name)],
            )

