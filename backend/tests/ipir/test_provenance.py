from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ipir.enums import ProvenanceSourceType
from app.ipir.provenance import Provenance, SourceReference


def test_provenance_valid_confidence() -> None:
    source = SourceReference(
        source_type=ProvenanceSourceType.REGULATORY_FILING,
        source_id="FILING_AZ_2026",
        source_name="AZ Department of Insurance Filing",
        page=42,
    )
    prov = Provenance(
        sources=[source],
        extraction_confidence=Decimal("0.95"),
        interpretation_confidence=Decimal("1.00"),
    )
    assert prov.extraction_confidence == Decimal("0.95")
    assert prov.sources[0].page == 42


def test_provenance_invalid_confidence_too_high() -> None:
    with pytest.raises(ValidationError, match="must be between 0 and 1 inclusive"):
        Provenance(extraction_confidence=Decimal("1.5"))


def test_provenance_invalid_confidence_negative() -> None:
    with pytest.raises(ValidationError, match="must be between 0 and 1 inclusive"):
        Provenance(interpretation_confidence=Decimal("-0.1"))
