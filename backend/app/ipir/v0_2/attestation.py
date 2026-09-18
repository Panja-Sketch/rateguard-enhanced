from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

VerificationStatus = Literal["VERIFIED", "REVIEW_REQUIRED", "REJECTED"]


class Attestation(BaseModel):
    """Compilation/verification receipt embedded in an IPIR v0.2 package
    (locked doc section 6.1 `attestation`; section 2.3 defines the term
    "Attested": required stages completed and their hashes included). This
    session only defines the model; the workbook compiler (a later session)
    is what actually populates it from a real compilation run."""

    model_config = ConfigDict(extra="forbid")

    compiler_version: str
    schema_version: str
    source_hash: str
    verification_status: VerificationStatus
    validated_at: datetime
    control_case_results: list[str] = []
