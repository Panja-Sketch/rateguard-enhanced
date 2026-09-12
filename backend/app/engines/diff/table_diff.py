from decimal import Decimal

from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.diff.models import SemanticDifference
from app.ipir.tables import ExactMatch, RangeMatch, RateTable, TableRow


def format_match_key(row: TableRow) -> str:
    """Formats a deterministic semantic key for a row based on its dimension matches."""
    parts: list[str] = []
    for match in row.matches:
        if isinstance(match, ExactMatch):
            parts.append(str(match.value))
        elif isinstance(match, RangeMatch):
            min_str = str(match.minimum) if match.minimum is not None else "-inf"
            max_str = str(match.maximum) if match.maximum is not None else "+inf"
            parts.append(f"{min_str}..{max_str}")
        else:
            parts.append(str(match))
    return "|".join(parts) if len(parts) > 1 else parts[0]


def compare_rate_tables(left_table: RateTable, right_table: RateTable) -> list[SemanticDifference]:
    """Deterministically compares two RateTable instances using semantic dimension keys."""
    differences: list[SemanticDifference] = []
    table_id = left_table.id

    left_map = {format_match_key(row): row for row in left_table.rows}
    right_map = {format_match_key(row): row for row in right_table.rows}

    all_keys = sorted(set(left_map.keys()) | set(right_map.keys()))

    for key in all_keys:
        left_row = left_map.get(key)
        right_row = right_map.get(key)

        if left_row is not None and right_row is not None:
            if Decimal(str(left_row.value)) != Decimal(str(right_row.value)):
                differences.append(
                    SemanticDifference(
                        id=f"diff_{table_id}_{key}_factor",
                        difference_type=DifferenceType.TABLE_ROW_CHANGE,
                        semantic_path=f"tables.{table_id}[{key}]",
                        node_id=table_id,
                        node_type="TABLE_ROW",
                        left_value=left_row.value,
                        right_value=right_row.value,
                        severity=DifferenceSeverity.CRITICAL,
                        description=(
                            f"Rate table '{table_id}' factor for '{key}' changed from "
                            f"{left_row.value} to {right_row.value}"
                        ),
                        left_provenance=left_table.provenance,
                        right_provenance=right_table.provenance,
                        metadata={"dimension_key": key},
                    )
                )
        elif left_row is not None and right_row is None:
            differences.append(
                SemanticDifference(
                    id=f"diff_{table_id}_{key}_missing",
                    difference_type=DifferenceType.TABLE_ROW_MISSING,
                    semantic_path=f"tables.{table_id}[{key}]",
                    node_id=table_id,
                    node_type="TABLE_ROW",
                    left_value=left_row.value,
                    right_value=None,
                    severity=DifferenceSeverity.CRITICAL,
                    description=f"Rate table '{table_id}' row '{key}' missing on right side",
                    left_provenance=left_table.provenance,
                    right_provenance=None,
                    metadata={"dimension_key": key},
                )
            )
        elif left_row is None and right_row is not None:
            differences.append(
                SemanticDifference(
                    id=f"diff_{table_id}_{key}_extra",
                    difference_type=DifferenceType.TABLE_ROW_EXTRA,
                    semantic_path=f"tables.{table_id}[{key}]",
                    node_id=table_id,
                    node_type="TABLE_ROW",
                    left_value=None,
                    right_value=right_row.value,
                    severity=DifferenceSeverity.CRITICAL,
                    description=f"Rate table '{table_id}' row '{key}' present on right side only",
                    left_provenance=None,
                    right_provenance=right_table.provenance,
                    metadata={"dimension_key": key},
                )
            )

    return differences
