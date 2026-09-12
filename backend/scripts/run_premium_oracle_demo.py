import sys
from datetime import date
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.engines.oracle import PremiumOracle, RiskInput  # noqa: E402
from app.ipir.enums import TransactionType  # noqa: E402
from app.ipir.package import IPIRPackage  # noqa: E402


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_file = (
        root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )

    if not canonical_file.exists():
        print(f"Error: Canonical rate plan file not found at {canonical_file}")
        sys.exit(1)

    with open(canonical_file, encoding="utf-8") as f:
        package = IPIRPackage.model_validate_json(f.read())

    # Scenario A Risk Input
    risk = RiskInput(
        values={
            "territory": "T17",
            "roof_age": 24,
            "deductible": 1000,
            "protection_class": 6,
            "construction_type": "FRAME",
            "dwelling_limit": 400000,
            "multi_policy": True,
            "claims_free": True,
        }
    )

    effective_date = date(2026, 9, 15)
    oracle = PremiumOracle()

    result = oracle.calculate(
        package=package,
        risk=risk,
        effective_date=effective_date,
        transaction_type=TransactionType.NEW_BUSINESS,
    )

    print("\n========================================================")
    print("               RateGuard Premium Oracle                 ")
    print("========================================================\n")
    print(f"Package ID:       {result.package_id}")
    print(f"Package Version:  {result.package_version}")
    print(f"Effective Date:   {result.effective_date}")
    print(f"Transaction:      {result.transaction_type.value}\n")

    print("Resolved Factors & Table Lookups")
    print("--------------------------------------------------------")
    factor_nodes = [
        "base_rate",
        "territory_factor",
        "roof_age_factor",
        "deductible_factor",
        "protection_class_factor",
        "construction_factor",
        "dwelling_limit_factor",
        "territory_construction_adjustment",
    ]
    for fn in factor_nodes:
        val = result.resolved_values.get(fn)
        print(f"  {fn:<36} = {val}")

    print("\nCalculation Trace")
    print("--------------------------------------------------------")
    calc_nodes = [
        "gross_risk_premium",
        "multi_policy_discount",
        "claims_free_discount",
        "premium_after_discounts",
        "policy_minimum",
        "policy_fee",
        "total_policy_premium",
    ]
    for cn in calc_nodes:
        val = result.resolved_values.get(cn)
        print(f"  {cn:<36} = {val}")

    print("\nFinal Result")
    print("--------------------------------------------------------")
    print(f"  {result.final_output_id:<36} = ${result.final_premium} {result.currency}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
