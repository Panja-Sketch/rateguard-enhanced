"""Maps validated `RG_*` sheet rows into IPIR v0.2 constructs
(`app.ipir.v0_2`), reusing those models directly rather than inventing a
parallel workbook-specific schema (locked doc section 6; session
instructions item 6).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.ingestion.workbook_v1.errors import WorkbookErrorDetail, WorkbookRejectionError
from app.ingestion.workbook_v1.formulas import (
    ALLOWED_CALCULATION_OPERATORS,
    parse_condition,
    parse_operand,
)
from app.ingestion.workbook_v1.limits import COMPILER_VERSION
from app.ingestion.workbook_v1.sanitize import sanitize_display_and_export
from app.ingestion.workbook_v1.vocabulary import (
    RECOGNIZED_CURRENCIES,
    RECOGNIZED_JURISDICTIONS,
    SUPPORTED_CURRENCIES,
    SUPPORTED_JURISDICTIONS,
)
from app.ipir.common import EffectivePeriod
from app.ipir.enums import InputDataType, RoundingMode, TableLookupType, TransactionType
from app.ipir.inputs import PricingInput
from app.ipir.package import PricingConstant
from app.ipir.product import Jurisdiction
from app.ipir.tables import ExactMatch, RangeMatch, RateTable, TableDimension, TableRow
from app.ipir.v0_2.calculations import CalculationNodeV2
from app.ipir.v0_2.control_cases import ControlCase
from app.ipir.v0_2.envelope import IpirSourceType, ProductRefV2, SourceMetadata
from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ConditionalExpression,
    LiteralExpression,
    RoundExpression,
    TableLookupExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2
from app.ipir.v0_2.package import IPIRPackageV2

REQUIRED_METADATA_KEYS = (
    "package_id", "product_id", "line", "country", "currency", "effective_start",
)

_TRUE_STRINGS = {"true", "1", "yes", "y"}
_FALSE_STRINGS = {"false", "0", "no", "n", ""}


def _s(value: Any) -> str | None:  # noqa: ANN401
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _sname(value: Any, *, sheet: str, cell: str) -> str:  # noqa: ANN401
    """A required, human-readable `name` field: sanitized against
    formula-injection/XSS at the boundary where it is read from a raw cell
    (locked doc section 15.3)."""
    text = _s(value)
    if text is None:
        raise WorkbookRejectionError(
            code="MISSING_REQUIRED_VALUE",
            message=f"Sheet '{sheet}' row is missing a required 'name' value.",
            details=[WorkbookErrorDetail(sheet=sheet, cell=cell)],
        )
    return sanitize_display_and_export(text)


def _bool(value: Any, *, default: bool = False) -> bool:  # noqa: ANN401
    text = _s(value)
    if text is None:
        return default
    lowered = text.lower()
    if lowered in _TRUE_STRINGS:
        return True
    if lowered in _FALSE_STRINGS:
        return False
    return default


def _decimal(value: Any) -> Decimal | None:  # noqa: ANN401
    text = _s(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def build_metadata(rows: list[dict]) -> dict[str, str]:
    """RG_METADATA is a flat key/value sheet. Returns the raw (unsanitized --
    used for actual parsing) key->value map."""
    metadata: dict[str, str] = {}
    for row in rows:
        key = _s(row.get("key"))
        value = _s(row.get("value"))
        if key is not None:
            metadata[key.lower()] = value or ""

    missing = [k for k in REQUIRED_METADATA_KEYS if k not in metadata or not metadata[k]]
    if missing:
        raise WorkbookRejectionError(
            code="MISSING_REQUIRED_METADATA_KEY",
            message=f"RG_METADATA is missing required key(s): {', '.join(missing)}.",
            details=[WorkbookErrorDetail(sheet="RG_METADATA", note=k) for k in missing],
        )
    _validate_currency_and_jurisdiction(metadata)
    return metadata


def _validate_currency_and_jurisdiction(metadata: dict[str, str]) -> None:
    """Semantic (not merely format) validation of currency and jurisdiction.
    Exact, case-sensitive codes: an unsupported value is rejected, never
    normalized into a supported one."""
    currency = metadata["currency"]
    if currency not in SUPPORTED_CURRENCIES:
        recognized = currency in RECOGNIZED_CURRENCIES
        raise WorkbookRejectionError(
            code="UNSUPPORTED_CURRENCY",
            message=(
                f"RG_METADATA currency {currency!r} is "
                f"{'a recognized ISO 4217 code but not supported' if recognized else 'not a recognized ISO 4217 code'}"
                f" by this product scope. Supported: {sorted(SUPPORTED_CURRENCIES)}."
            ),
            details=[WorkbookErrorDetail(sheet="RG_METADATA", note="currency")],
        )

    country = metadata["country"]
    supported_states = SUPPORTED_JURISDICTIONS.get(country)
    if supported_states is None:
        raise WorkbookRejectionError(
            code="UNSUPPORTED_JURISDICTION_COUNTRY",
            message=(
                f"RG_METADATA country {country!r} is not a supported jurisdiction. "
                f"Supported: {sorted(SUPPORTED_JURISDICTIONS)}."
            ),
            details=[WorkbookErrorDetail(sheet="RG_METADATA", note="country")],
        )
    state = metadata.get("state", "")
    if state not in supported_states:
        recognized = state in RECOGNIZED_JURISDICTIONS.get(country, frozenset())
        raise WorkbookRejectionError(
            code="UNSUPPORTED_JURISDICTION_STATE",
            message=(
                f"RG_METADATA state {state!r} for country {country!r} is "
                f"{'a recognized code but not supported' if recognized else 'missing or not a recognized code'}"
                f" by this product scope. Supported: {sorted(supported_states)}."
            ),
            details=[WorkbookErrorDetail(sheet="RG_METADATA", note="state")],
        )


def build_inputs(rows: list[dict]) -> list[PricingInput]:
    inputs: list[PricingInput] = []
    for row in rows:
        row_id = _s(row.get("id"))
        if row_id is None:
            continue
        allowed_raw = _s(row.get("allowed_values"))
        allowed_values = (
            [v.strip() for v in allowed_raw.split(",") if v.strip()] if allowed_raw else None
        )
        try:
            data_type = InputDataType(str(row.get("data_type")).strip().upper())
        except ValueError as exc:
            raise WorkbookRejectionError(
                code="UNSUPPORTED_INPUT_DATA_TYPE",
                message=f"RG_INPUTS row '{row_id}' has unsupported data_type '{row.get('data_type')}'.",
                details=[WorkbookErrorDetail(sheet="RG_INPUTS", cell=row_id)],
            ) from exc
        inputs.append(
            PricingInput(
                id=row_id,
                name=_sname(row.get("name"), sheet="RG_INPUTS", cell=row_id),
                data_type=data_type,
                required=_bool(row.get("required"), default=True),
                minimum=_decimal(row.get("minimum")),
                maximum=_decimal(row.get("maximum")),
                allowed_values=allowed_values,
            )
        )
    return inputs


def build_constants(rows: list[dict]) -> list[PricingConstant]:
    constants: list[PricingConstant] = []
    for row in rows:
        row_id = _s(row.get("id"))
        if row_id is None:
            continue
        value = _decimal(row.get("value"))
        if value is None:
            raise WorkbookRejectionError(
                code="MALFORMED_CONSTANT_VALUE",
                message=f"RG_CONSTANTS row '{row_id}' has a non-numeric value '{row.get('value')}'.",
                details=[WorkbookErrorDetail(sheet="RG_CONSTANTS", cell=row_id)],
            )
        constants.append(
            PricingConstant(
                id=row_id,
                name=_sname(row.get("name"), sheet="RG_CONSTANTS", cell=row_id),
                value=value,
            )
        )
    return constants


def build_tables(rows: list[dict]) -> list[RateTable]:
    by_table: dict[str, list[dict]] = {}
    for row in rows:
        table_id = _s(row.get("table_id"))
        if table_id is None:
            continue
        by_table.setdefault(table_id, []).append(row)

    tables: list[RateTable] = []
    for table_id, table_rows in by_table.items():
        dimension_ids = {_s(r.get("dimension_id")) for r in table_rows}
        dimension_ids.discard(None)
        if len(dimension_ids) != 1:
            raise WorkbookRejectionError(
                code="MALFORMED_TABLE_DIMENSION",
                message=(
                    f"RG_TABLES table '{table_id}' must declare exactly one consistent "
                    f"dimension_id across its rows; found {sorted(dimension_ids)}."
                ),
                details=[WorkbookErrorDetail(sheet="RG_TABLES", cell=table_id)],
            )
        dimension_id = next(iter(dimension_ids))

        table_rows_out: list[TableRow] = []
        has_range = any(_decimal(r.get("min")) is not None or _decimal(r.get("max")) is not None for r in table_rows)
        for row in table_rows:
            priority = None
            priority_raw = _s(row.get("priority"))
            if priority_raw is not None:
                try:
                    priority = int(Decimal(priority_raw))
                except InvalidOperation:
                    priority = None

            result_value = _decimal(row.get("result_value"))
            if result_value is None:
                raise WorkbookRejectionError(
                    code="MALFORMED_TABLE_ROW",
                    message=f"RG_TABLES table '{table_id}' row has a non-numeric result_value.",
                    details=[WorkbookErrorDetail(sheet="RG_TABLES", cell=table_id)],
                )

            min_v, max_v = _decimal(row.get("min")), _decimal(row.get("max"))
            match_value = _s(row.get("match_value"))
            if min_v is not None or max_v is not None:
                match: ExactMatch | RangeMatch = RangeMatch(
                    minimum=min_v,
                    maximum=max_v,
                    include_minimum=_bool(row.get("include_min"), default=True),
                    include_maximum=_bool(row.get("include_max"), default=True),
                )
            elif match_value is not None:
                match = ExactMatch(value=match_value)
            else:
                raise WorkbookRejectionError(
                    code="MALFORMED_TABLE_ROW",
                    message=(
                        f"RG_TABLES table '{table_id}' row declares neither a min/max range "
                        "nor a match_value."
                    ),
                    details=[WorkbookErrorDetail(sheet="RG_TABLES", cell=table_id)],
                )
            table_rows_out.append(TableRow(matches=[match], value=result_value, priority=priority))

        tables.append(
            RateTable(
                id=table_id,
                name=sanitize_display_and_export(table_id),
                dimensions=[
                    TableDimension(
                        input_ref=dimension_id,
                        lookup_type=TableLookupType.RANGE if has_range else TableLookupType.EXACT,
                    )
                ],
                rows=table_rows_out,
            )
        )
    return tables


def build_calculations(rows: list[dict]) -> list[CalculationNodeV2]:
    nodes: list[CalculationNodeV2] = []
    for row in rows:
        node_id = _s(row.get("node_id"))
        if node_id is None:
            continue
        operator = (_s(row.get("operator")) or "").upper()
        if operator not in ALLOWED_CALCULATION_OPERATORS:
            raise WorkbookRejectionError(
                code="UNSUPPORTED_WORKBOOK_FUNCTION",
                message=f"RG_CALCULATIONS node '{node_id}' uses unsupported operator '{operator}'.",
                details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id, function=operator)],
            )

        operand_1, operand_2 = row.get("operand_1"), row.get("operand_2")

        if operator == "LOOKUP":
            table_id = _s(operand_1)
            if table_id is None:
                raise WorkbookRejectionError(
                    code="MALFORMED_CALCULATION_OPERAND",
                    message=f"RG_CALCULATIONS node '{node_id}' LOOKUP requires operand_1=table_id.",
                    details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id)],
                )
            expression = TableLookupExpression(table_id=table_id)
        elif operator == "ROUND":
            scale_raw, rounding_raw = _s(row.get("scale")), _s(row.get("rounding_mode"))
            if scale_raw is None or rounding_raw is None:
                raise WorkbookRejectionError(
                    code="MISSING_ROUNDING",
                    message=f"RG_CALCULATIONS node '{node_id}' uses ROUND but is missing scale/rounding_mode.",
                    details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id)],
                )
            try:
                rounding_mode = RoundingMode(rounding_raw.upper())
            except ValueError as exc:
                raise WorkbookRejectionError(
                    code="UNSUPPORTED_ROUNDING_MODE",
                    message=f"RG_CALCULATIONS node '{node_id}' has unsupported rounding_mode '{rounding_raw}'.",
                    details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id)],
                ) from exc
            expression = RoundExpression(
                operand=parse_operand(operand_1, sheet="RG_CALCULATIONS", cell=node_id),
                scale=int(Decimal(scale_raw)),
                rounding_mode=rounding_mode,
            )
        elif operator == "IF":
            condition = parse_condition(operand_1, sheet="RG_CALCULATIONS", cell=node_id)
            branch_text = _s(operand_2) or ""
            parts = branch_text.split("|")
            if len(parts) != 2:
                raise WorkbookRejectionError(
                    code="MALFORMED_CONDITION",
                    message=(
                        f"RG_CALCULATIONS node '{node_id}' IF operand_2 must be "
                        "'WHEN_TRUE|WHEN_FALSE'."
                    ),
                    details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id)],
                )
            expression = ConditionalExpression(
                condition=condition,
                when_true=parse_operand(parts[0], sheet="RG_CALCULATIONS", cell=node_id),
                when_false=parse_operand(parts[1], sheet="RG_CALCULATIONS", cell=node_id),
            )
        else:  # ADD / SUBTRACT / MULTIPLY / DIVIDE / MIN / MAX
            left = parse_operand(operand_1, sheet="RG_CALCULATIONS", cell=node_id)
            right = parse_operand(operand_2, sheet="RG_CALCULATIONS", cell=node_id)
            if operator == "DIVIDE" and isinstance(right, LiteralExpression):
                try:
                    if Decimal(str(right.value)) == 0:
                        raise WorkbookRejectionError(
                            code="DIVISION_BY_ZERO_PATH",
                            message=f"RG_CALCULATIONS node '{node_id}' divides by a literal zero.",
                            details=[WorkbookErrorDetail(sheet="RG_CALCULATIONS", cell=node_id)],
                        )
                except InvalidOperation:
                    pass
            expression = BinaryExpression(operator=operator, left=left, right=right)

        nodes.append(
            CalculationNodeV2(
                id=node_id,
                name=sanitize_display_and_export(node_id),
                expression=expression,
            )
        )
    return nodes


def build_outputs(rows: list[dict], calculations: list[CalculationNodeV2]) -> list[PricingOutputV2]:
    calc_by_id = {c.id: c for c in calculations}
    outputs: list[PricingOutputV2] = []
    currencies: set[str] = set()
    for row in rows:
        output_id = _s(row.get("output_id"))
        if output_id is None:
            continue
        source_ref = _s(row.get("source_ref"))
        currency = (_s(row.get("currency")) or "").upper()
        currencies.add(currency)

        source_calc = calc_by_id.get(source_ref) if source_ref else None
        if source_calc is None:
            raise WorkbookRejectionError(
                code="UNRESOLVED_REFERENCE",
                message=(
                    f"RG_OUTPUTS output '{output_id}' references source_ref '{source_ref}', which "
                    "is not declared anywhere in RG_CALCULATIONS (a hidden dependency outside the "
                    "declared RateGuard contract)."
                ),
                details=[WorkbookErrorDetail(sheet="RG_OUTPUTS", cell=output_id)],
            )
        if not isinstance(source_calc.expression, RoundExpression):
            raise WorkbookRejectionError(
                code="MISSING_ROUNDING",
                message=(
                    f"RG_OUTPUTS output '{output_id}' source_ref '{source_ref}' must be a "
                    "calculation node whose top-level operator is ROUND (declared rounding "
                    "is required on every output; locked doc section 6.2)."
                ),
                details=[WorkbookErrorDetail(sheet="RG_OUTPUTS", cell=output_id)],
            )
        round_expr: RoundExpression = source_calc.expression

        outputs.append(
            PricingOutputV2(
                id=output_id,
                name=sanitize_display_and_export(output_id),
                source_ref=source_ref,
                currency=currency,
                scale=round_expr.scale,
                rounding_mode=round_expr.rounding_mode,
            )
        )

    if len(currencies) > 1:
        raise WorkbookRejectionError(
            code="CURRENCY_INCONSISTENCY",
            message=f"RG_OUTPUTS declares inconsistent currencies across outputs: {sorted(currencies)}.",
            details=[WorkbookErrorDetail(sheet="RG_OUTPUTS")],
        )
    return outputs


def build_control_cases(rows: list[dict]) -> list[ControlCase]:
    import json

    cases: list[ControlCase] = []
    for row in rows:
        case_id = _s(row.get("case_id"))
        if case_id is None:
            continue
        try:
            inputs = json.loads(_s(row.get("input")) or "{}")
            expected_outputs = json.loads(_s(row.get("expected_output")) or "{}")
        except json.JSONDecodeError as exc:
            raise WorkbookRejectionError(
                code="MALFORMED_CONTROL_CASE",
                message=f"RG_CONTROL_CASES row '{case_id}' has malformed JSON: {exc}",
                details=[WorkbookErrorDetail(sheet="RG_CONTROL_CASES", cell=case_id)],
            ) from exc
        tolerance = _decimal(row.get("tolerance"))
        if tolerance is None:
            raise WorkbookRejectionError(
                code="MALFORMED_CONTROL_CASE",
                message=f"RG_CONTROL_CASES row '{case_id}' has a non-numeric tolerance.",
                details=[WorkbookErrorDetail(sheet="RG_CONTROL_CASES", cell=case_id)],
            )
        cases.append(
            ControlCase(
                case_id=case_id,
                inputs=inputs,
                expected_outputs={k: str(v) for k, v in expected_outputs.items()},
                tolerance=tolerance,
            )
        )
    return cases


def classify_ipir_validation_error(exc: Exception) -> WorkbookRejectionError:
    """Translates a `ValidationError`/`ValueError` raised by `IPIRPackageV2`'s
    own semantic validators (duplicate IDs, cycles, unresolved references,
    ambiguous range overlaps, ...) into the matching workbook-compiler error
    code, so the same, already-tested validation logic (CP2, session 1) is
    reused rather than reimplemented for workbook-sourced packages."""
    msg = str(exc)
    if "Duplicate node ID" in msg or "Duplicate ControlCase ID" in msg:
        code = "DUPLICATE_ID"
    elif "dependency cycle" in msg:
        code = "CALCULATION_CYCLE"
    elif (
        "unresolved node" in msg
        or "unknown table" in msg
        or "nonexistent source node" in msg
        or "depends_on unresolved node" in msg
    ):
        code = "UNRESOLVED_REFERENCE"
    elif "ambiguous overlap" in msg:
        code = "AMBIGUOUS_RANGE_OVERLAP"
    elif "requires total coverage but has a gap" in msg:
        code = "RANGE_GAP"
    elif "declares scale=" in msg:
        code = "ROUNDING_MISMATCH"
    else:
        code = "IPIR_VALIDATION_FAILED"
    return WorkbookRejectionError(code=code, message=msg)


def build_package(
    *,
    metadata: dict[str, str],
    inputs: list[PricingInput],
    constants: list[PricingConstant],
    tables: list[RateTable],
    calculations: list[CalculationNodeV2],
    outputs: list[PricingOutputV2],
    control_cases: list[ControlCase],
    artifact_sha256: str,
    compiled_at: str,
) -> IPIRPackageV2:
    transaction_types_raw = metadata.get("transaction_types") or "NEW_BUSINESS,RENEWAL"
    try:
        transaction_types = [
            TransactionType(t.strip().upper()) for t in transaction_types_raw.split(",") if t.strip()
        ]
    except ValueError as exc:
        raise WorkbookRejectionError(
            code="UNSUPPORTED_TRANSACTION_TYPE",
            message=f"RG_METADATA transaction_types contains an unsupported value: {exc}",
            details=[WorkbookErrorDetail(sheet="RG_METADATA", note="transaction_types")],
        ) from exc

    try:
        return IPIRPackageV2(
            package_id=metadata["package_id"],
            package_version=metadata.get("package_version") or "1.0.0",
            source=SourceMetadata(
                source_type=IpirSourceType.CONTROLLED_XLSX,
                artifact_sha256=artifact_sha256,
                compiler_version=COMPILER_VERSION,
                compiled_at=compiled_at,
            ),
            product=ProductRefV2(
                product_id=metadata["product_id"],
                line=metadata["line"].upper(),
                jurisdiction=Jurisdiction(
                    country=metadata["country"], state_or_province=metadata.get("state") or None
                ),
                currency=metadata["currency"].upper(),
            ),
            effective_period=EffectivePeriod(
                start=metadata["effective_start"], end=metadata.get("effective_end") or None
            ),
            transaction_types=transaction_types,
            inputs=inputs,
            constants=constants,
            tables=tables,
            calculations=calculations,
            outputs=outputs,
            control_cases=control_cases,
        )
    except (ValidationError, ValueError) as exc:
        raise classify_ipir_validation_error(exc) from exc
