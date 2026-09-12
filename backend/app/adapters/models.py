from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.ipir.package import IPIRPackage
from app.ipir.provenance import Provenance


class SourceFormat(str, Enum):  # noqa: UP042
    """Supported source format types for pricing adapters."""

    STRUCTURED_JSON = "STRUCTURED_JSON"
    EXCEL = "EXCEL"
    PDF = "PDF"
    PLATFORM_CONFIG = "PLATFORM_CONFIG"


class SourceDescriptor(BaseModel):
    """Generic descriptor identifying a pricing source representation."""

    source_id: str
    name: str
    source_type: SourceFormat
    format: str
    storage_uri: str
    version: str = "1.0.0"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterResult(BaseModel):
    """Strongly typed result returned by a PricingSourceAdapter."""

    source_id: str
    source_type: SourceFormat
    adapter_id: str
    ipir_package: IPIRPackage
    mapping_coverage: float = 100.0
    warnings: list[str] = Field(default_factory=list)
    unsupported_constructs: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    requires_human_review: bool = False
