from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.ipir.common import EffectivePeriod, validate_identifier_string
from app.ipir.enums import TableLookupType
from app.ipir.provenance import Provenance


class TableDimension(BaseModel):
    """Specification of a rate table dimension key."""

    model_config = ConfigDict(extra="forbid")

    input_ref: str
    lookup_type: TableLookupType

    @model_validator(mode="after")
    def validate_input_ref(self) -> "TableDimension":
        self.input_ref = validate_identifier_string(self.input_ref)
        return self


class ExactMatch(BaseModel):
    """Exact value lookup key for a table dimension."""

    model_config = ConfigDict(extra="forbid")

    value: str | int | Decimal | bool


class RangeMatch(BaseModel):
    """Numeric or date range bracket lookup key for a table dimension."""

    model_config = ConfigDict(extra="forbid")

    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None
    include_minimum: bool = True
    include_maximum: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> "RangeMatch":
        if self.minimum is None and self.maximum is None:
            raise ValueError("RangeMatch cannot have both minimum and maximum as None")
        if self.minimum is not None and self.maximum is not None:
            min_dec = Decimal(str(self.minimum))
            max_dec = Decimal(str(self.maximum))
            if min_dec > max_dec:
                raise ValueError(
                    f"RangeMatch minimum ({self.minimum}) cannot exceed maximum ({self.maximum})"
                )
        return self


class TableRow(BaseModel):
    """Row in a rate table mapping dimension matches to a rate factor or amount.

    `priority` is optional and additive (default `None`, meaning "no explicit
    priority declared"): it exists so `validate_range_coverage` can distinguish
    an intentional, prioritized overlap (every overlapping row in the group has
    a distinct priority) from an ambiguous one (two or more overlapping rows
    share a priority, or none is declared) without changing any pre-existing
    table's parsed shape or lookup behavior.
    """

    model_config = ConfigDict(extra="forbid")

    matches: list[ExactMatch | RangeMatch]
    value: Decimal
    priority: int | None = None


class RateTable(BaseModel):
    """Canonical 1D or 2D lookup table definition.

    `requires_total_coverage` and `default_value` are optional and additive
    (default `False`/`None`): existing tables that omit them are unaffected.
    When `requires_total_coverage` is set, `validate_range_coverage` treats an
    uncovered region of a RANGE dimension as an error unless `default_value`
    is provided.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    dimensions: list[TableDimension]
    rows: list[TableRow]
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None
    requires_total_coverage: bool = False
    default_value: Decimal | None = None

    @model_validator(mode="after")
    def validate_table(self) -> "RateTable":
        self.id = validate_identifier_string(self.id)
        dim_count = len(self.dimensions)
        if dim_count not in (1, 2):
            raise ValueError(
                f"RateTable '{self.id}' must have 1 or 2 dimensions in IPIR 0.1, got {dim_count}"
            )
        for idx, row in enumerate(self.rows):
            if len(row.matches) != dim_count:
                raise ValueError(
                    f"RateTable '{self.id}' row index {idx} match count ({len(row.matches)}) "
                    f"does not match table dimension count ({dim_count})"
                )
        return self


def _range_bounds(match: RangeMatch) -> tuple[Decimal, Decimal]:
    """Normalizes a RangeMatch to a closed [low, high] Decimal interval for
    overlap comparison, treating an open end as +/- infinity."""
    low = Decimal(str(match.minimum)) if match.minimum is not None else Decimal("-Infinity")
    high = Decimal(str(match.maximum)) if match.maximum is not None else Decimal("Infinity")
    return low, high


def _ranges_overlap(a: RangeMatch, b: RangeMatch) -> bool:
    a_low, a_high = _range_bounds(a)
    b_low, b_high = _range_bounds(b)
    # Two closed intervals overlap unless one ends (exclusive-aware) strictly
    # before the other begins.
    if a_high < b_low or (a_high == b_low and not (a.include_maximum and b.include_minimum)):
        return False
    if b_high < a_low or (b_high == a_low and not (b.include_maximum and a.include_minimum)):
        return False
    return True


def validate_range_coverage(table: RateTable) -> list[str]:
    """Deterministically checks a single-dimension RANGE-lookup table for
    ambiguous overlaps and (when `requires_total_coverage` is set) gaps.

    A shared validator (not duplicated per IPIR schema version) so both v0.1
    and v0.2 packages get identical range-safety guarantees. Returns a list
    of human-readable issues; empty means the table's ranges are unambiguous
    (and, if required, fully covered).

    Only evaluates 1-dimensional RANGE tables: a 2D table's per-cell
    ambiguity is already caught by RateTable/TableRow's existing structural
    validation (row match-count vs. dimension count), and total-coverage
    gap analysis across two independent RANGE dimensions is out of scope for
    this pass.
    """
    if len(table.dimensions) != 1 or table.dimensions[0].lookup_type != TableLookupType.RANGE:
        return []

    range_rows: list[tuple[TableRow, RangeMatch]] = []
    for row in table.rows:
        match = row.matches[0]
        if isinstance(match, RangeMatch):
            range_rows.append((row, match))

    issues: list[str] = []

    # Ambiguous overlap: any two rows whose ranges overlap must have distinct,
    # explicit priorities; otherwise a lookup could match more than one row.
    for i in range(len(range_rows)):
        for j in range(i + 1, len(range_rows)):
            row_a, match_a = range_rows[i]
            row_b, match_b = range_rows[j]
            if not _ranges_overlap(match_a, match_b):
                continue
            if (
                row_a.priority is not None
                and row_b.priority is not None
                and row_a.priority != row_b.priority
            ):
                continue
            issues.append(
                f"RateTable '{table.id}' has an ambiguous overlap between rows {i} and {j} "
                "with no distinct explicit priority."
            )

    # Gap coverage: only enforced when the table declares it must cover its
    # entire domain and provides no explicit default for the uncovered
    # region. This checks for a gap *between* declared ranges; it does not
    # require the domain to start at -Infinity or end at +Infinity, since a
    # real-world bounded input (e.g. roof_age >= 0) has a legitimate finite
    # lower/upper bound that a single declared row can cover without an open
    # end.
    if table.requires_total_coverage and table.default_value is None:
        sorted_ranges = sorted(
            (_range_bounds(m) for _, m in range_rows), key=lambda bounds: bounds[0]
        )
        for (_, prev_high), (next_low, _) in zip(sorted_ranges, sorted_ranges[1:], strict=False):
            if next_low > prev_high:
                issues.append(
                    f"RateTable '{table.id}' requires total coverage but has a gap "
                    f"between {prev_high} and {next_low}, and no default_value is declared."
                )

    return issues
