import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.engines.portfolio.models import SyntheticPolicy
from app.ipir.enums import TransactionType


def load_synthetic_portfolio_csv(csv_path: Path) -> list[SyntheticPolicy]:
    """Loads synthetic CSV portfolio into strongly typed SyntheticPolicy list."""
    policies: list[SyntheticPolicy] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            policies.append(
                SyntheticPolicy(
                    policy_id=row["policy_id"],
                    product_id=row.get("product_id", "AZ_HO3"),
                    state=row.get("state", "AZ"),
                    form=row.get("form", "HO3"),
                    transaction_type=TransactionType(row["transaction_type"]),
                    effective_date=date.fromisoformat(row["effective_date"]),
                    territory=row["territory"],
                    roof_age=int(row["roof_age"]),
                    deductible=int(row["deductible"]),
                    protection_class=int(row["protection_class"]),
                    construction_type=row["construction_type"],
                    dwelling_limit=int(row["dwelling_limit"]),
                    multi_policy=row["multi_policy"].lower() == "true",
                    claims_free=row["claims_free"].lower() == "true",
                    claims_free_years=int(row.get("claims_free_years", 3)),
                    canonical_premium=Decimal(
                        row.get(
                            "canonical_premium",
                            row.get("expected_canonical_premium", "0"),
                        )
                    ),
                )
            )
    return policies

