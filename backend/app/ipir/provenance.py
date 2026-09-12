from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.ipir.enums import ProvenanceSourceType


class SourceReference(BaseModel):
    """Reference to an origin document, workbook, or system entry."""

    source_type: ProvenanceSourceType
    source_id: str
    source_name: str | None = None
    source_version: str | None = None
    location: str | None = None
    page: int | None = None
    section: str | None = None
    artifact_reference: str | None = None


class Provenance(BaseModel):
    """Lineage and audit metadata for IPIR pricing nodes."""

    sources: list[SourceReference] = Field(default_factory=list)
    extraction_confidence: Decimal | None = None
    interpretation_confidence: Decimal | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_confidence(self) -> "Provenance":
        for field_name, val in [
            ("extraction_confidence", self.extraction_confidence),
            ("interpretation_confidence", self.interpretation_confidence),
        ]:
            if val is not None:
                if val < Decimal("0") or val > Decimal("1"):
                    raise ValueError(f"{field_name} must be between 0 and 1 inclusive, got {val}")
        return self
