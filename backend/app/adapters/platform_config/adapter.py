import json
from decimal import Decimal
from typing import Any

from app.adapters.base import PricingSourceAdapter
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.ipir.enums import ProvenanceSourceType
from app.ipir.package import IPIRPackage
from app.ipir.provenance import Provenance, SourceReference


class PlatformConfigAdapter(PricingSourceAdapter):
    """
    Adapter compiling synthetic rating platform configuration
    into IPIR packages.
    """

    adapter_id = "platform_config_adapter"
    supported_format = "PLATFORM_CONFIG"

    def to_ipir(
        self,
        source: SourceDescriptor,
        content: bytes,
        context: dict[str, Any] | None = None,
    ) -> AdapterResult:
        config_data = json.loads(content.decode("utf-8"))

        if "ipir_payload" in config_data:
            pkg = IPIRPackage.model_validate(config_data["ipir_payload"])
        else:
            pkg = IPIRPackage.model_validate_json(content.decode("utf-8"))

        prov = Provenance(
            sources=[
                SourceReference(
                    source_type=ProvenanceSourceType.ACTUARIAL_SPEC,
                    source_id=source.source_id,
                    source_name=source.name,
                    source_version=source.version,
                    section="Rating Engine Configuration Routines",
                    location=f"RateBook: {config_data.get('rateBook', {}).get('bookCode', source.source_id)}",
                )
            ],
            extraction_confidence=Decimal("1.0"),
            interpretation_confidence=Decimal("1.0"),
            notes="Compiled via RateGuard Platform Config Adapter.",
        )

        pkg.provenance = prov

        return AdapterResult(
            source_id=source.source_id,
            source_type=SourceFormat.PLATFORM_CONFIG,
            adapter_id=self.adapter_id,
            ipir_package=pkg,
            mapping_coverage=100.0,
            confidence=1.0,
            evidence={
                "rateBook": config_data.get("rateBook", {}),
                "format": config_data.get("vendor_neutral_format", "PLATFORM_CONFIG"),
            },
            provenance=prov,
            requires_human_review=False,
        )
