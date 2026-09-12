from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ipir.enums import TableLookupType
from app.ipir.tables import ExactMatch, RangeMatch, RateTable, TableDimension, TableRow


def test_1d_exact_table() -> None:
    table = RateTable(
        id="territory_factor",
        name="Territory Rating Factor",
        dimensions=[TableDimension(input_ref="territory", lookup_type=TableLookupType.EXACT)],
        rows=[
            TableRow(matches=[ExactMatch(value="AZ01")], value=Decimal("1.00")),
            TableRow(matches=[ExactMatch(value="AZ02")], value=Decimal("1.15")),
        ],
    )
    assert table.id == "territory_factor"
    assert len(table.rows) == 2


def test_1d_range_table() -> None:
    table = RateTable(
        id="roof_age_factor",
        name="Roof Age Factor",
        dimensions=[TableDimension(input_ref="roof_age", lookup_type=TableLookupType.RANGE)],
        rows=[
            TableRow(matches=[RangeMatch(minimum=0, maximum=10)], value=Decimal("1.00")),
            TableRow(matches=[RangeMatch(minimum=11, maximum=20)], value=Decimal("1.10")),
            TableRow(matches=[RangeMatch(minimum=21, maximum=None)], value=Decimal("1.35")),
        ],
    )
    assert len(table.rows) == 3


def test_2d_table_construction() -> None:
    table = RateTable(
        id="protection_construction_factor",
        name="Protection x Construction Factor",
        dimensions=[
            TableDimension(input_ref="construction_type", lookup_type=TableLookupType.EXACT),
            TableDimension(input_ref="protection_class", lookup_type=TableLookupType.EXACT),
        ],
        rows=[
            TableRow(
                matches=[ExactMatch(value="frame"), ExactMatch(value="1")],
                value=Decimal("1.00"),
            ),
            TableRow(
                matches=[ExactMatch(value="frame"), ExactMatch(value="2")],
                value=Decimal("1.20"),
            ),
        ],
    )
    assert len(table.dimensions) == 2


def test_invalid_3d_table_rejection() -> None:
    with pytest.raises(ValidationError, match="must have 1 or 2 dimensions in IPIR 0.1"):
        RateTable(
            id="3d_table",
            name="Unsupported 3D Table",
            dimensions=[
                TableDimension(input_ref="d1", lookup_type=TableLookupType.EXACT),
                TableDimension(input_ref="d2", lookup_type=TableLookupType.EXACT),
                TableDimension(input_ref="d3", lookup_type=TableLookupType.EXACT),
            ],
            rows=[],
        )


def test_range_match_invalid() -> None:
    with pytest.raises(ValidationError, match="minimum .* cannot exceed maximum"):
        RangeMatch(minimum=50, maximum=10)

    with pytest.raises(ValidationError, match="cannot have both minimum and maximum as None"):
        RangeMatch(minimum=None, maximum=None)
