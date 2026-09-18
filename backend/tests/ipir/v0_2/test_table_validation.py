from decimal import Decimal

from app.ipir.enums import TableLookupType
from app.ipir.tables import RangeMatch, RateTable, TableDimension, TableRow, validate_range_coverage


def _table(rows: list[TableRow], **overrides) -> RateTable:
    defaults = dict(
        id="t1",
        name="T1",
        dimensions=[TableDimension(input_ref="x", lookup_type=TableLookupType.RANGE)],
        rows=rows,
    )
    defaults.update(overrides)
    return RateTable(**defaults)


def test_non_overlapping_ranges_have_no_issues():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=10)], value="1.00"),
            TableRow(matches=[RangeMatch(minimum=11, maximum=None)], value="1.40"),
        ]
    )
    assert validate_range_coverage(table) == []


def test_ambiguous_overlap_without_priority_is_flagged():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=15)], value="1.00"),
            TableRow(matches=[RangeMatch(minimum=10, maximum=20)], value="1.20"),
        ]
    )
    issues = validate_range_coverage(table)
    assert len(issues) == 1
    assert "ambiguous overlap" in issues[0]


def test_overlap_with_distinct_priority_is_not_flagged():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=15)], value="1.00", priority=1),
            TableRow(matches=[RangeMatch(minimum=10, maximum=20)], value="1.20", priority=2),
        ]
    )
    assert validate_range_coverage(table) == []


def test_overlap_with_shared_priority_is_still_flagged():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=15)], value="1.00", priority=1),
            TableRow(matches=[RangeMatch(minimum=10, maximum=20)], value="1.20", priority=1),
        ]
    )
    issues = validate_range_coverage(table)
    assert len(issues) == 1


def test_total_coverage_gap_is_flagged_without_default():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=10)], value="1.00"),
            TableRow(matches=[RangeMatch(minimum=21, maximum=None)], value="1.40"),
        ],
        requires_total_coverage=True,
    )
    issues = validate_range_coverage(table)
    assert any("gap" in i for i in issues)


def test_total_coverage_gap_is_not_flagged_with_default_value():
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=0, maximum=10)], value="1.00"),
            TableRow(matches=[RangeMatch(minimum=21, maximum=None)], value="1.40"),
        ],
        requires_total_coverage=True,
        default_value=Decimal("1.00"),
    )
    assert validate_range_coverage(table) == []


def test_full_coverage_with_touching_boundaries_passes():
    # Row 1 covers up to and including 10; row 2 picks up strictly above 10
    # (include_minimum=False) -- a continuous domain with no true gap and no
    # ambiguous overlap at the touchpoint.
    table = _table(
        [
            TableRow(matches=[RangeMatch(minimum=None, maximum=10)], value="1.00"),
            TableRow(matches=[RangeMatch(minimum=10, maximum=None, include_minimum=False)], value="1.40"),
        ],
        requires_total_coverage=True,
    )
    assert validate_range_coverage(table) == []


def test_exact_lookup_tables_are_not_evaluated_by_range_validator():
    table = RateTable(
        id="t2",
        name="T2",
        dimensions=[TableDimension(input_ref="x", lookup_type=TableLookupType.EXACT)],
        rows=[],
    )
    assert validate_range_coverage(table) == []
