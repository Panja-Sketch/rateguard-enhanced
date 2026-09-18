"""Package-compatibility gate (locked doc section 6.4): "Before comparison,
require matching product line, jurisdiction, currency, overlapping effective
period, and transaction type. A mismatch is REVIEW_REQUIRED, never PASS.
Package schema major versions must match."
"""

from pydantic import BaseModel, ConfigDict

from app.ipir.v0_2.package import IPIRPackageV2


class CompatibilityResult(BaseModel):
    """Result of the compatibility gate. `compatible=False` always means
    REVIEW_REQUIRED at the mission-decision layer — this function never
    returns a bare boolean, so a caller cannot accidentally collapse a
    mismatch into a silent PASS."""

    model_config = ConfigDict(extra="forbid")

    compatible: bool
    reasons: list[str]


def _schema_major(schema_version: str) -> str:
    return schema_version.split(".", 1)[0]


def _periods_overlap(a: IPIRPackageV2, b: IPIRPackageV2) -> bool:
    a_start, a_end = a.effective_period.start, a.effective_period.end
    b_start, b_end = b.effective_period.start, b.effective_period.end
    if a_end is not None and b_start > a_end:
        return False
    if b_end is not None and a_start > b_end:
        return False
    return True


def check_compatibility(a: IPIRPackageV2, b: IPIRPackageV2) -> CompatibilityResult:
    """Checks package `a` (typically the authoritative source) against
    package `b` (typically the candidate) for the locked section 6.4 gate.
    Never raises for a mismatch — mismatches are reported as data so the
    caller can route to REVIEW_REQUIRED explicitly, per the locked truth
    table (section 7.4)."""
    reasons: list[str] = []

    if _schema_major(a.schema_version) != _schema_major(b.schema_version):
        reasons.append(
            f"Schema major version mismatch: '{a.schema_version}' vs '{b.schema_version}'."
        )

    if a.product.line != b.product.line:
        reasons.append(f"Product line mismatch: '{a.product.line}' vs '{b.product.line}'.")

    if a.product.jurisdiction.country != b.product.jurisdiction.country or (
        a.product.jurisdiction.state_or_province != b.product.jurisdiction.state_or_province
    ):
        reasons.append(
            f"Jurisdiction mismatch: '{a.product.jurisdiction}' vs '{b.product.jurisdiction}'."
        )

    if a.product.currency != b.product.currency:
        reasons.append(f"Currency mismatch: '{a.product.currency}' vs '{b.product.currency}'.")

    if not _periods_overlap(a, b):
        reasons.append(
            f"Effective periods do not overlap: {a.effective_period} vs {b.effective_period}."
        )

    if not (set(a.transaction_types) & set(b.transaction_types)):
        reasons.append(
            f"No overlapping transaction types: {a.transaction_types} vs {b.transaction_types}."
        )

    return CompatibilityResult(compatible=not reasons, reasons=reasons)
