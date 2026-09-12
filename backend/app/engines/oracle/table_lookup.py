from decimal import Decimal
from typing import Any

from app.engines.oracle.errors import TableLookupError
from app.ipir.enums import TableLookupType
from app.ipir.tables import ExactMatch, RangeMatch, RateTable, TableRow


def _matches_exact(match: ExactMatch, input_val: Any) -> bool:
    """Checks exact equality match for string, integer, or boolean inputs."""
    if isinstance(input_val, (int, float, Decimal)) and isinstance(
        match.value, (int, float, Decimal)
    ):
        return Decimal(str(input_val)) == Decimal(str(match.value))
    return str(input_val).strip() == str(match.value).strip()


def _matches_range(match: RangeMatch, input_val: Any) -> bool:
    """Checks continuous numeric range boundary matches (inclusive)."""
    try:
        val = Decimal(str(input_val))
    except (ValueError, TypeError):
        return False

    if match.minimum is not None:
        min_val = Decimal(str(match.minimum))
        if val < min_val:
            return False

    if match.maximum is not None:
        max_val = Decimal(str(match.maximum))
        if val > max_val:
            return False

    return True


def lookup_table(table: RateTable, dimension_values: dict[str, Any]) -> tuple[Decimal, TableRow]:
    """Deterministically resolves a single rate table factor from dimension inputs.

    Args:
        table: Target IPIR RateTable instance.
        dimension_values: Mapping of input_ref -> supplied input value.

    Returns:
        Tuple of (factor_value: Decimal, matched_row: TableRow).

    Raises:
        TableLookupError: If zero rows or multiple rows match.
    """
    matching_rows: list[TableRow] = []

    for row in table.rows:
        row_matches = True
        for dim, match in zip(table.dimensions, row.matches, strict=True):
            input_val = dimension_values.get(dim.input_ref)
            if input_val is None:
                row_matches = False
                break

            if dim.lookup_type == TableLookupType.EXACT:
                if not isinstance(match, ExactMatch) or not _matches_exact(match, input_val):
                    row_matches = False
                    break
            elif dim.lookup_type == TableLookupType.RANGE:
                if not isinstance(match, RangeMatch) or not _matches_range(match, input_val):
                    row_matches = False
                    break

        if row_matches:
            matching_rows.append(row)

    if len(matching_rows) == 0:
        if len(table.dimensions) > 1:
            # Neutral default fallback for 2D adjustment tables
            fallback_row = TableRow(matches=[], value=Decimal("1.00"))
            return Decimal("1.00"), fallback_row
        raise TableLookupError(
            f"No matching row found in table '{table.id}' for inputs: {dimension_values}"
        )

    if len(matching_rows) > 1:
        raise TableLookupError(
            f"Ambiguous rate table lookup in table '{table.id}': "
            f"found {len(matching_rows)} matching rows for inputs: {dimension_values}"
        )

    matched_row = matching_rows[0]
    return Decimal(str(matched_row.value)), matched_row
