from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

from app.core.config import get_settings
from app.engines.impact.models import ImpactPredicate
from app.engines.portfolio.models import PortfolioExposureResult
from app.ipir.enums import TransactionType
from app.storage.portfolio.interfaces import BasePortfolioRepository
from app.storage.portfolio.models import PortfolioPolicyRecord, PortfolioSummaryStats
from app.storage.portfolio.sql_translator import translate_predicates_to_bigquery_where


class BigQueryPortfolioRepository(BasePortfolioRepository):
    """Google BigQuery-backed portfolio repository."""

    def __init__(
        self,
        client: bigquery.Client | None = None,
        dataset_id: str | None = None,
        portfolio_table: str | None = None,
        results_table: str | None = None,
        project_id: str | None = None,
    ) -> None:
        settings = get_settings()
        self.project_id = project_id or settings.google_cloud_project
        self.dataset_id = dataset_id or settings.bigquery_dataset
        self.portfolio_table = portfolio_table or settings.bigquery_portfolio_table
        self.results_table = results_table or settings.bigquery_results_table

        self._client = client
        self._portfolio_fqn = f"`{self.project_id}.{self.dataset_id}.{self.portfolio_table}`"
        self._results_fqn = f"`{self.project_id}.{self.dataset_id}.{self.results_table}`"

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=self.project_id)
        return self._client

    def load_policies(self, limit: int | None = None) -> list[PortfolioPolicyRecord]:
        query = f"SELECT * FROM {self._portfolio_fqn} ORDER BY policy_id"
        if limit is not None:
            query += f" LIMIT {int(limit)}"

        query_job = self.client.query(query)
        rows = query_job.result()

        records: list[PortfolioPolicyRecord] = []
        for r in rows:
            records.append(self._parse_row(dict(r.items())))
        return records

    def query_impacted_policies(
        self,
        predicates: list[ImpactPredicate],
        limit: int | None = None,
    ) -> list[PortfolioPolicyRecord]:
        p_filter = translate_predicates_to_bigquery_where(predicates)

        query = f"SELECT * FROM {self._portfolio_fqn} {p_filter.where_clause} ORDER BY policy_id"
        if limit is not None:
            query += f" LIMIT {int(limit)}"

        job_config = bigquery.QueryJobConfig()
        if p_filter.query_params:
            job_config.query_parameters = [
                bigquery.ScalarQueryParameter(p["name"], p["type"], p["value"])
                for p in p_filter.query_params
            ]

        query_job = self.client.query(query, job_config=job_config)
        rows = query_job.result()

        records: list[PortfolioPolicyRecord] = []
        for r in rows:
            records.append(self._parse_row(dict(r.items())))
        return records

    def save_exposure_result(
        self,
        result: PortfolioExposureResult,
        run_id: str,
    ) -> bool:
        row = {
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "total_policies": result.total_policies,
            "exposed_policies": result.exposed_policy_count,
            "behaviorally_affected": result.behaviorally_affected_count,
            "financially_affected": result.financially_affected_count,
            "expected_premium": str(result.total_expected_premium),
            "target_premium": str(result.total_target_premium),
            "signed_variance": str(result.total_signed_variance),
            "absolute_variance": str(result.total_absolute_variance),
            "decision": result.metadata.get("assurance_decision", "BLOCK_DEPLOYMENT"),
        }

        errors = self.client.insert_rows_json(self._results_fqn, [row])
        return len(errors) == 0

    def get_portfolio_summary(self) -> PortfolioSummaryStats:
        query = f"""
        SELECT 
            COUNT(1) as total_policies,
            SUM(canonical_premium) as total_expected_premium,
            COUNTIF(roof_age >= 21 AND roof_age <= 30) as roof_21_30
        FROM {self._portfolio_fqn}
        """
        query_job = self.client.query(query)
        row = list(query_job.result())[0]

        total = row.get("total_policies", 0)
        total_prem = Decimal(str(row.get("total_expected_premium") or "0.00"))
        roof_21_30 = row.get("roof_21_30", 0)

        # Territory count aggregation
        terr_query = (
            f"SELECT territory, COUNT(1) as cnt FROM {self._portfolio_fqn} GROUP BY territory"
        )
        terr_job = self.client.query(terr_query)
        terr_counts = {r["territory"]: r["cnt"] for r in terr_job.result()}

        return PortfolioSummaryStats(
            total_policies=total,
            total_expected_premium=total_prem,
            roof_band_21_30_count=roof_21_30,
            territory_counts=terr_counts,
        )

    def _parse_row(self, r: dict[str, Any]) -> PortfolioPolicyRecord:
        eff_date = r["effective_date"]
        if isinstance(eff_date, str):
            eff_date = date.fromisoformat(eff_date)

        return PortfolioPolicyRecord(
            policy_id=r["policy_id"],
            product_id=r["product_id"],
            state=r["state"],
            form=r["form"],
            transaction_type=TransactionType(r["transaction_type"]),
            effective_date=eff_date,
            territory=r["territory"],
            roof_age=int(r["roof_age"]),
            deductible=int(r["deductible"]),
            protection_class=int(r["protection_class"]),
            construction_type=r["construction_type"],
            dwelling_limit=int(r["dwelling_limit"]),
            multi_policy=bool(r["multi_policy"]),
            claims_free=bool(r["claims_free"]),
            claims_free_years=int(r.get("claims_free_years", 3)),
            canonical_premium=Decimal(str(r["canonical_premium"])),
        )
