from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from app.ipir.enums import InsuranceLine
from app.ipir.product import Jurisdiction
from app.ipir.v0_2.common import validate_identifier_string_v2


class IpirSourceType(StrEnum):
    """Origin format of a compiled IPIR v0.2 package (locked doc section 6.1
    `source.source_type`). Distinct from `app.ipir.enums.ProvenanceSourceType`,
    which describes node-level lineage, not package-level compilation origin."""

    STRUCTURED_JSON = "STRUCTURED_JSON"
    CONTROLLED_XLSX = "CONTROLLED_XLSX"
    API_CONNECTOR = "API_CONNECTOR"


class SourceMetadata(BaseModel):
    """Compilation-origin metadata required on every IPIR v0.2 package
    (locked doc section 6.1)."""

    model_config = ConfigDict(extra="forbid")

    source_type: IpirSourceType
    artifact_sha256: str
    compiler_version: str
    compiled_at: datetime

    @model_validator(mode="after")
    def validate_hash(self) -> "SourceMetadata":
        digest = self.artifact_sha256.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(
                f"artifact_sha256 must be a 64-character hex SHA-256 digest, got '{self.artifact_sha256}'"
            )
        self.artifact_sha256 = digest
        return self


class ProductRefV2(BaseModel):
    """Product/jurisdiction/currency envelope for an IPIR v0.2 package
    (locked doc section 6.1 `product`)."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    line: InsuranceLine
    jurisdiction: Jurisdiction
    currency: str

    @model_validator(mode="after")
    def validate_product_ref(self) -> "ProductRefV2":
        self.product_id = validate_identifier_string_v2(self.product_id)
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"currency must be a 3-letter uppercase ISO code, got '{self.currency}'")
        return self
