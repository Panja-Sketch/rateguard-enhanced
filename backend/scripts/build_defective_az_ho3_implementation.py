import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ipir.common import EffectivePeriod, NodeReference  # noqa: E402
from app.ipir.enums import ProvenanceSourceType  # noqa: E402
from app.ipir.package import IPIRPackage  # noqa: E402
from app.ipir.provenance import Provenance, SourceReference  # noqa: E402
from app.ipir.tables import RangeMatch  # noqa: E402
from scripts.build_az_ho3_rate_plan import build_canonical_package  # noqa: E402


def build_defective_package() -> IPIRPackage:
    """Loads canonical package and injects 3 controlled defects for RateGuard testing."""
    pkg = build_canonical_package()

    # Define defective package metadata
    pkg.id = "AZ_HO3_2026_09_DEFECTIVE"
    pkg.name = "Arizona Homeowners HO3 Synthetic Defective Implementation Rate Plan"
    pkg.version = "2026.09-defective"

    # Defect 1: Critical Roof Factor (roof_age 21-30: 1.35 -> 1.25)
    roof_table = next(t for t in pkg.tables if t.id == "roof_age_factor")
    for row in roof_table.rows:
        if (
            isinstance(row.matches[0], RangeMatch)
            and row.matches[0].minimum == Decimal("21")
            and row.matches[0].maximum == Decimal("30")
        ):
            row.value = Decimal("1.25")

    # Defect 2: Effective Date Drift on claims_free_discount (2026-09-01 -> 2026-09-15)
    claims_mod = next(m for m in pkg.modifiers if m.id == "claims_free_discount")
    claims_mod.effective_period = EffectivePeriod(start=date(2026, 9, 15))

    # Defect 3: Calculation Order / Sequence Drift
    # Swap sequence numbers of policy_minimum (1 -> 2) and policy_fee (2 -> 1)
    policy_min = next(c for c in pkg.constraints if c.id == "policy_minimum")
    policy_fee = next(f for f in pkg.fees if f.id == "policy_fee")
    policy_min.sequence = 2
    policy_fee.sequence = 1

    calc_min = next(n for n in pkg.calculations if n.id == "premium_after_minimum")
    calc_total = next(n for n in pkg.calculations if n.id == "total_policy_premium")

    # Swapped order: policy_fee (seq 1) applies to premium_after_discounts in calc_min node.
    # policy_minimum (seq 2) applies to total_policy_premium node.
    policy_fee.applies_to = "premium_after_discounts"
    policy_min.applies_to = "premium_after_minimum"

    calc_min.depends_on = ["premium_after_discounts", "policy_fee"]
    calc_total.expression = NodeReference(ref="premium_after_minimum")
    calc_total.depends_on = ["premium_after_minimum", "policy_minimum"]

    # Defective Provenance
    pkg.provenance = Provenance(
        sources=[
            SourceReference(
                source_type=ProvenanceSourceType.RATING_ENGINE,
                source_id="synthetic-rating-engine-implementation",
                source_name="Synthetic Rating Engine Implementation",
                source_version="2026.09-defective",
            )
        ],
        extraction_confidence=Decimal("1.0"),
        interpretation_confidence=Decimal("1.0"),
        notes="Synthetic defective Arizona HO3 rate plan built for RateGuard hackathon demo.",
    )

    return pkg


def main() -> None:
    pkg = build_defective_package()

    root_dir = Path(__file__).resolve().parent.parent.parent
    defective_dir = root_dir / "data" / "implementations" / "defective"
    defective_dir.mkdir(parents=True, exist_ok=True)

    defective_file = defective_dir / "AZ_HO3_2026_09_ipir.json"

    pkg_json = pkg.model_dump_json(indent=2) + "\n"

    with open(defective_file, "w", encoding="utf-8") as f:
        f.write(pkg_json)

    print(f"Successfully wrote defective IPIR package to: {defective_file}")


if __name__ == "__main__":
    main()
