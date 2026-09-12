import csv
import json
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.engines.oracle import PremiumOracle, RiskInput  # noqa: E402
from app.ipir.enums import TransactionType  # noqa: E402
from app.ipir.package import IPIRPackage  # noqa: E402


def generate_portfolio(
    package: IPIRPackage, seed: int = 42, count: int = 50000
) -> tuple[list[dict], dict]:
    """Generates a deterministic synthetic portfolio of policies with canonical premiums.

    Restores original seed-42 PRNG sequence while enforcing 2-decimal monetary scale
    directly from PremiumOracle.final_premium.
    """
    random.seed(seed)
    oracle = PremiumOracle()

    territories = [f"T{i:02d}" for i in range(1, 21)]
    deductibles = [500, 1000, 2500, 5000]
    constructions = ["FRAME", "MASONRY", "SUPERIOR"]
    transaction_types = [TransactionType.NEW_BUSINESS.value, TransactionType.RENEWAL.value]

    pkg_start = package.effective_period.start

    policies: list[dict] = []
    territory_counts: dict[str, int] = {}
    roof_band_21_30_count = 0
    temporal_window_count = 0

    for i in range(1, count + 1):
        policy_id = f"POL-AZ-{i:06d}"

        # 1. Territory
        territory = random.choice(territories)
        territory_counts[territory] = territory_counts.get(territory, 0) + 1

        # 2. Roof age distribution (0 to 50, ~20% in 21..30)
        if random.random() < 0.20:
            roof_age = random.randint(21, 30)
            roof_band_21_30_count += 1
        else:
            roof_age = random.choice(
                [random.randint(0, 20) for _ in range(4)] + [random.randint(31, 50)]
            )

        # 3. Deductible
        deductible = random.choices(deductibles, weights=[15, 60, 20, 5])[0]

        # 4. Protection Class
        protection_class = random.randint(1, 10)

        # 5. Construction Type
        construction_type = random.choices(constructions, weights=[40, 45, 15])[0]

        # 6. Dwelling Limit
        dwelling_limit = random.randint(15, 100) * 10000  # 150k to 1.0M

        # 7. Multi Policy
        multi_policy = random.random() < 0.35

        # 8. Claims Free
        claims_free = random.random() < 0.70

        # Derive claims_free_years deterministically without consuming extra PRNG calls
        claims_free_years = 3 if claims_free else 0

        # 9. Effective Date distribution (Sep 1, 2026 to Dec 31, 2026)
        day_offset = random.randint(0, 120)
        eff_date = pkg_start + timedelta(days=day_offset)

        if date(2026, 9, 1) <= eff_date < date(2026, 9, 15):
            temporal_window_count += 1

        # 10. Transaction Type
        tx_type_str = random.choices(transaction_types, weights=[70, 30])[0]

        # Calculate authoritative expected premium via Premium Oracle
        risk = RiskInput(
            policy_id=policy_id,
            effective_date=eff_date,
            transaction_type=TransactionType(tx_type_str),
            values={
                "territory": territory,
                "roof_age": roof_age,
                "deductible": str(deductible),
                "protection_class": protection_class,
                "construction_type": construction_type,
                "dwelling_limit": dwelling_limit,
                "multi_policy": multi_policy,
                "claims_free": claims_free,
                "claims_free_years": claims_free_years,
            },
        )
        oracle_trace = oracle.calculate(
            package, risk, effective_date=eff_date, transaction_type=TransactionType(tx_type_str)
        )
        expected_premium = oracle_trace.final_premium

        if not isinstance(expected_premium, Decimal):
            raise ValueError(f"Oracle final_premium for policy {policy_id} is not Decimal: {type(expected_premium)}")

        if expected_premium.as_tuple().exponent < -2:
            raise ValueError(
                f"Oracle final_premium for policy {policy_id} has excessive decimal precision: "
                f"{expected_premium} (exponent: {expected_premium.as_tuple().exponent}). "
                "Oracle rounding rules must enforce scale <= 2."
            )

        formatted_premium = f"{expected_premium:.2f}"

        row = {
            "policy_id": policy_id,
            "product_id": package.product.id,
            "state": package.product.jurisdiction.state_or_province or "AZ",
            "form": package.product.form or "HO3",
            "transaction_type": tx_type_str,
            "effective_date": eff_date.isoformat(),
            "territory": territory,
            "roof_age": roof_age,
            "deductible": deductible,
            "protection_class": protection_class,
            "construction_type": construction_type,
            "dwelling_limit": dwelling_limit,
            "multi_policy": multi_policy,
            "claims_free": claims_free,
            "claims_free_years": claims_free_years,
            "canonical_premium": formatted_premium,
        }
        policies.append(row)

    summary = {
        "portfolio_id": "AZ_HO3_2026_SYNTHETIC_50K",
        "seed": seed,
        "policy_count": len(policies),
        "portfolio_size": count,
        "roof_band_21_30_population": roof_band_21_30_count,
        "roof_band_21_30_count": roof_band_21_30_count,
        "roof_band_21_30_pct": round(roof_band_21_30_count / count * 100.0, 2),
        "temporal_window_population": temporal_window_count,
        "temporal_window_count": temporal_window_count,
        "temporal_window_pct": round(temporal_window_count / count * 100.0, 2),
        "territory_distribution": territory_counts,
        "total_expected_premium": f"{sum(Decimal(p['canonical_premium']) for p in policies):.2f}",
    }

    return policies, summary


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_file = (
        root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )

    with open(canonical_file, encoding="utf-8") as f:
        package = IPIRPackage.model_validate_json(f.read())

    policies, summary = generate_portfolio(package, seed=42, count=50000)

    portfolio_dir = root_dir / "data" / "portfolio"
    portfolio_dir.mkdir(parents=True, exist_ok=True)

    csv_file = portfolio_dir / "az_ho3_2026_synthetic_50k.csv"
    summary_file = portfolio_dir / "az_ho3_2026_synthetic_50k_summary.json"

    fieldnames = list(policies[0].keys())
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(policies)

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    print(f"Successfully generated 50,000 synthetic policies CSV: {csv_file}")
    print(f"Summary saved to: {summary_file}")


if __name__ == "__main__":
    main()
