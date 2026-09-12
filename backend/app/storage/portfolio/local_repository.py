import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import get_data_dir
from app.engines.impact.models import ImpactPredicate
from app.engines.portfolio.models import PortfolioExposureResult
from app.ipir.enums import TransactionType
from app.storage.portfolio.interfaces import BasePortfolioRepository
from app.storage.portfolio.models import PortfolioPolicyRecord, PortfolioSummaryStats


class LocalPortfolioRepository(BasePortfolioRepository):
    """Local CSV-backed portfolio repository."""

    def __init__(self, csv_file_path: Path | None = None) -> None:
        if csv_file_path is None:
            data_dir = get_data_dir()
            csv_file_path = data_dir / "portfolio" / "az_ho3_2026_synthetic_50k.csv"
        self.csv_file_path = csv_file_path
        self._saved_results: dict[str, PortfolioExposureResult] = {}

    def load_policies(self, limit: int | None = None) -> list[PortfolioPolicyRecord]:
        if not self.csv_file_path.exists():
            return []

        records: list[PortfolioPolicyRecord] = []
        with open(self.csv_file_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                if limit is not None and idx >= limit:
                    break
                records.append(self._parse_row(row))
        return records

    def query_impacted_policies(
        self,
        predicates: list[ImpactPredicate],
        limit: int | None = None,
    ) -> list[PortfolioPolicyRecord]:
        policies = self.load_policies()
        if not predicates:
            return policies if limit is None else policies[:limit]

        matching: list[PortfolioPolicyRecord] = []
        for pol in policies:
            pol_dict = pol.model_dump()
            pol_dict["effective_date"] = pol.effective_date
            pol_dict["transaction_type"] = pol.transaction_type.value

            # Evaluate predicates against policy attributes
            matches_any_pred = False
            for pred in predicates:
                pred_matches = True
                for clause in pred.clauses:
                    val = pol_dict.get(clause.field)
                    if val is None:
                        pred_matches = False
                        break

                    target_val = clause.value
                    op = clause.operator.value

                    if op in ("GTE", ">="):
                        if not (val >= target_val):
                            pred_matches = False
                    elif op in ("GT", ">"):
                        if not (val > target_val):
                            pred_matches = False
                    elif op in ("LTE", "<="):
                        if not (val <= target_val):
                            pred_matches = False
                    elif op in ("LT", "<"):
                        if not (val < target_val):
                            pred_matches = False
                    elif op in ("EQ", "=="):
                        if not (str(val) == str(target_val)):
                            pred_matches = False
                    elif op in ("NE", "!="):
                        if not (str(val) != str(target_val)):
                            pred_matches = False

                if pred_matches:
                    matches_any_pred = True
                    break

            if matches_any_pred:
                matching.append(pol)
                if limit is not None and len(matching) >= limit:
                    break

        return matching

    def save_exposure_result(
        self,
        result: PortfolioExposureResult,
        run_id: str,
    ) -> bool:
        self._saved_results[run_id] = result
        return True

    def get_portfolio_summary(self) -> PortfolioSummaryStats:
        policies = self.load_policies()
        total = len(policies)
        total_prem = sum((p.canonical_premium for p in policies), Decimal("0.00"))
        roof_21_30 = sum(1 for p in policies if 21 <= p.roof_age <= 30)

        terr_counts: dict[str, int] = {}
        for p in policies:
            terr_counts[p.territory] = terr_counts.get(p.territory, 0) + 1

        return PortfolioSummaryStats(
            total_policies=total,
            total_expected_premium=total_prem,
            roof_band_21_30_count=roof_21_30,
            territory_counts=terr_counts,
        )

    def _parse_row(self, row: dict[str, Any]) -> PortfolioPolicyRecord:
        return PortfolioPolicyRecord(
            policy_id=row["policy_id"],
            product_id=row["product_id"],
            state=row["state"],
            form=row["form"],
            transaction_type=TransactionType(row["transaction_type"]),
            effective_date=date.fromisoformat(row["effective_date"]),
            territory=row["territory"],
            roof_age=int(row["roof_age"]),
            deductible=int(row["deductible"]),
            protection_class=int(row["protection_class"]),
            construction_type=row["construction_type"],
            dwelling_limit=int(row["dwelling_limit"]),
            multi_policy=str(row["multi_policy"]).lower() in ("true", "1"),
            claims_free=str(row["claims_free"]).lower() in ("true", "1"),
            claims_free_years=int(row.get("claims_free_years", 3)),
            canonical_premium=Decimal(str(row["canonical_premium"])),
        )
