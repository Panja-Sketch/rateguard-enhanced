from copy import deepcopy
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.ipir.common import EffectivePeriod
from app.ipir.package import IPIRPackage


class ScenarioLabParams(BaseModel):
    """Judge-configurable pricing parameters for the RateGuard Scenario Lab."""

    name: str = "Custom Scenario"
    roof_age_21_30_factor: Decimal | None = Field(
        default=None, description="Override for Roof Age 21-30 factor (Canonical: 1.35)"
    )
    deductible_1000_factor: Decimal | None = Field(
        default=None, description="Override for Deductible $1000 factor (Canonical: 0.90)"
    )
    territory_t05_factor: Decimal | None = Field(
        default=None, description="Override for Territory T05 factor (Canonical: 1.05)"
    )
    claims_free_discount_pct: Decimal | None = Field(
        default=None, description="Override for Claims-Free discount percent (Canonical: 5.0%)"
    )
    claims_free_effective_date: str | None = Field(
        default=None, description="Override for Claims-Free effective start date (Canonical: 2026-09-01)"
    )
    minimum_premium: Decimal | None = Field(
        default=None, description="Override for Policy Minimum Premium (Canonical: $500.00)"
    )
    policy_fee: Decimal | None = Field(
        default=None, description="Override for Policy Fee (Canonical: $25.00)"
    )
    async_execution: bool | None = None


DEMO_SCENARIOS_CATALOG = [
    {
        "id": "SCENARIO_A",
        "name": "Scenario A: Golden Multi-Defect (Filing vs Buggy Engine)",
        "description": "Critical roof age factor mismatch (1.35 vs 1.25), claims-free discount effective date drift, and policy minimum/fee sequence swap.",
        "left_package_id": "AZ_HO3_2026_09",
        "right_package_id": "AZ_HO3_2026_09_DEFECTIVE",
        "expected_decision": "BLOCK_DEPLOYMENT",
        "tags": ["Multi-Defect", "Financial Exposure", "High Risk"],
        "category": "Critical Regression",
    },
    {
        "id": "SCENARIO_B",
        "name": "Scenario B: Clean Baseline (No Drift / Perfect Equivalence)",
        "description": "Filing intent compared against a compliant, bug-free target rating engine implementation. 100% equivalence verified.",
        "left_package_id": "AZ_HO3_2026_09",
        "right_package_id": "AZ_HO3_2026_09_CLEAN",
        "expected_decision": "PASS",
        "tags": ["No Drift", "Compliant", "Green Path"],
        "category": "Clean Release",
    },
    {
        "id": "SCENARIO_C",
        "name": "Scenario C: Deductible Factor Table Drift",
        "description": "Target engine contains an isolated factor drift on the $1,000 deductible tier (0.80 target vs 0.90 intent).",
        "left_package_id": "AZ_HO3_2026_09",
        "right_package_id": "AZ_HO3_2026_09_DEDUCTIBLE_DRIFT",
        "expected_decision": "BLOCK_DEPLOYMENT",
        "tags": ["Table Drift", "Deductible", "Single Defect"],
        "category": "Table Divergence",
    },
    {
        "id": "SCENARIO_D",
        "name": "Scenario D: Claims-Free Discount Effective-Date Drift",
        "description": "Target engine implements claims-free discount with an effective date of 2026-09-20 instead of filing date 2026-09-01.",
        "left_package_id": "AZ_HO3_2026_09",
        "right_package_id": "AZ_HO3_2026_09_EFFDATE_DRIFT",
        "expected_decision": "BLOCK_DEPLOYMENT",
        "tags": ["Temporal Drift", "Discount", "SERFF Compliance"],
        "category": "Temporal Divergence",
    },
    {
        "id": "SCENARIO_E",
        "name": "Scenario E: Rating Territory Factor Drift",
        "description": "Target engine misconfigures Territory T05 base rate multiplier (1.15 target vs 1.05 intent).",
        "left_package_id": "AZ_HO3_2026_09",
        "right_package_id": "AZ_HO3_2026_09_TERRITORY_DRIFT",
        "expected_decision": "BLOCK_DEPLOYMENT",
        "tags": ["Territory", "Base Factor", "Regional Drift"],
        "category": "Territory Divergence",
    },
]


def _matches_roof_21_30(row_match: Any) -> bool:
    if hasattr(row_match, "minimum") or hasattr(row_match, "maximum"):
        mn = getattr(row_match, "minimum", None)
        mx = getattr(row_match, "maximum", None)
        if mn is not None and mn >= 21:
            return True
        if mx is not None and mx >= 21:
            return True
    if hasattr(row_match, "value"):
        v = str(row_match.value)
        return "21" in v or "30" in v
    return False


def _matches_deductible_1000(row_match: Any) -> bool:
    if hasattr(row_match, "value"):
        return str(row_match.value) in ("1000", "$1,000", "1000.0", "1000.00")
    if hasattr(row_match, "minimum"):
        return str(row_match.minimum) in ("1000", "1000.0", "1000.00")
    return False


def derive_scenario_package(package_id: str, canonical_pkg: IPIRPackage) -> IPIRPackage:
    """Derives target IPIR packages in-memory from canonical package without disk mutations."""
    pkg = deepcopy(canonical_pkg)
    pkg.id = package_id

    if package_id == "AZ_HO3_2026_09_CLEAN":
        pkg.name = f"{canonical_pkg.name} (Clean Target Implementation)"
        return pkg

    if package_id == "AZ_HO3_2026_09_DEDUCTIBLE_DRIFT":
        pkg.name = f"{canonical_pkg.name} (Deductible Factor Drift Target)"
        for table in pkg.tables:
            if table.id == "deductible_factor":
                for row in table.rows:
                    if row.matches and _matches_deductible_1000(row.matches[0]):
                        row.value = Decimal("0.80")
        return pkg

    if package_id == "AZ_HO3_2026_09_EFFDATE_DRIFT":
        pkg.name = f"{canonical_pkg.name} (Effective Date Drift Target)"
        for mod in pkg.modifiers:
            if mod.id == "claims_free_discount":
                mod.effective_period = EffectivePeriod(start="2026-09-20", end=None)
        return pkg

    if package_id == "AZ_HO3_2026_09_TERRITORY_DRIFT":
        pkg.name = f"{canonical_pkg.name} (Territory Factor Drift Target)"
        for table in pkg.tables:
            if table.id == "territory_factor":
                for row in table.rows:
                    if row.matches and hasattr(row.matches[0], "value") and row.matches[0].value == "T05":
                        row.value = Decimal("1.15")
        return pkg

    return pkg


def build_custom_lab_package(
    canonical_pkg: IPIRPackage, params: ScenarioLabParams, lab_id: str
) -> tuple[IPIRPackage, dict[str, Any]]:
    """Builds a derived IPIR package from user-specified Scenario Lab parameters."""
    pkg = deepcopy(canonical_pkg)
    pkg.id = lab_id
    pkg.name = f"Scenario Lab: {params.name}"

    changes: dict[str, Any] = {}

    if params.roof_age_21_30_factor is not None:
        for table in pkg.tables:
            if table.id == "roof_age_factor":
                for row in table.rows:
                    if row.matches and any(_matches_roof_21_30(m) for m in row.matches):
                        orig = row.value
                        row.value = params.roof_age_21_30_factor
                        changes["roof_age_21_30_factor"] = {
                            "canonical": str(orig),
                            "target": str(params.roof_age_21_30_factor),
                        }

    if params.deductible_1000_factor is not None:
        for table in pkg.tables:
            if table.id == "deductible_factor":
                for row in table.rows:
                    if row.matches and any(_matches_deductible_1000(m) for m in row.matches):
                        orig = row.value
                        row.value = params.deductible_1000_factor
                        changes["deductible_1000_factor"] = {
                            "canonical": str(orig),
                            "target": str(params.deductible_1000_factor),
                        }

    if params.territory_t05_factor is not None:
        for table in pkg.tables:
            if table.id == "territory_factor":
                for row in table.rows:
                    if row.matches and hasattr(row.matches[0], "value") and row.matches[0].value == "T05":
                        orig = row.value
                        row.value = params.territory_t05_factor
                        changes["territory_t05_factor"] = {
                            "canonical": str(orig),
                            "target": str(params.territory_t05_factor),
                        }

    if params.claims_free_discount_pct is not None:
        for mod in pkg.modifiers:
            if mod.id == "claims_free_discount":
                # discount is stored as Decimal e.g. -0.05
                rate_dec = -(params.claims_free_discount_pct / Decimal("100.0"))
                orig = getattr(mod, "value", Decimal("-0.05"))
                mod.value = rate_dec
                changes["claims_free_discount_pct"] = {
                    "canonical": "5.0%",
                    "target": f"{params.claims_free_discount_pct}%",
                }

    if params.claims_free_effective_date is not None:
        for mod in pkg.modifiers:
            if mod.id == "claims_free_discount":
                orig_start = mod.effective_period.start if mod.effective_period else "2026-09-01"
                mod.effective_period = EffectivePeriod(
                    start=params.claims_free_effective_date, end=None
                )
                changes["claims_free_effective_date"] = {
                    "canonical": str(orig_start),
                    "target": params.claims_free_effective_date,
                }

    if params.minimum_premium is not None:
        for constraint in pkg.constraints:
            if constraint.id == "policy_minimum_premium":
                orig = constraint.amount
                constraint.amount = params.minimum_premium
                changes["minimum_premium"] = {
                    "canonical": str(orig),
                    "target": str(params.minimum_premium),
                }

    if params.policy_fee is not None:
        for fee in pkg.fees:
            if fee.id == "policy_fee":
                orig = fee.amount
                fee.amount = params.policy_fee
                changes["policy_fee"] = {
                    "canonical": str(orig),
                    "target": str(params.policy_fee),
                }

    return pkg, changes
