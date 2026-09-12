from decimal import Decimal
from typing import Any

from app.adapters.base import PricingSourceAdapter
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.ipir.enums import ProvenanceSourceType
from app.ipir.package import IPIRPackage
from app.ipir.provenance import Provenance, SourceReference


class StructuredJSONPricingAdapter(PricingSourceAdapter):
    """Adapter parsing structured actuarial JSON specifications into canonical IPIR packages."""

    adapter_id = "structured_json_adapter"
    supported_format = "STRUCTURED_JSON"

    def to_ipir(
        self,
        source: SourceDescriptor,
        content: bytes,
        context: dict[str, Any] | None = None,
    ) -> AdapterResult:
        # Validate and deserialize IPIR package from structured JSON spec
        raw_json = content.decode("utf-8")
        pkg = IPIRPackage.model_validate_json(raw_json)

        prov = Provenance(
            sources=[
                SourceReference(
                    source_type=ProvenanceSourceType.ACTUARIAL_SPEC,
                    source_id=source.source_id,
                    source_name=source.name,
                    source_version=source.version,
                )
            ],
            extraction_confidence=Decimal("1.0"),
            interpretation_confidence=Decimal("1.0"),
            notes="Compiled via RateGuard Structured JSON Adapter.",
        )

        return AdapterResult(
            source_id=source.source_id,
            source_type=SourceFormat.STRUCTURED_JSON,
            adapter_id=self.adapter_id,
            ipir_package=pkg,
            mapping_coverage=100.0,
            confidence=1.0,
            provenance=prov,
            requires_human_review=False,
        )
