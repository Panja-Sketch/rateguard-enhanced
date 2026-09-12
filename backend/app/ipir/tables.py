from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.ipir.common import EffectivePeriod, validate_identifier_string
from app.ipir.enums import TableLookupType
from app.ipir.provenance import Provenance


class TableDimension(BaseModel):
    """Specification of a rate table dimension key."""

    input_ref: str
    lookup_type: TableLookupType

    @model_validator(mode="after")
    def validate_input_ref(self) -> "TableDimension":
        self.input_ref = validate_identifier_string(self.input_ref)
        return self


class ExactMatch(BaseModel):
    """Exact value lookup key for a table dimension."""

    value: str | int | Decimal | bool


class RangeMatch(BaseModel):
    """Numeric or date range bracket lookup key for a table dimension."""

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
    """Row in a rate table mapping dimension matches to a rate factor or amount."""

    matches: list[ExactMatch | RangeMatch]
    value: Decimal


class RateTable(BaseModel):
    """Canonical 1D or 2D lookup table definition."""

    id: str
    name: str
    dimensions: list[TableDimension]
    rows: list[TableRow]
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

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
