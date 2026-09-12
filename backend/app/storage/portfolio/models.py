from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery
from pydantic import BaseModel, Field

from app.ipir.enums import TransactionType

# Authoritative BigQuery schemas shared across setup, upload, and repository classes
SYNTHETIC_POLICIES_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("policy_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("product_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("state", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("form", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("transaction_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("effective_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("territory", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("roof_age", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("deductible", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("protection_class", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("construction_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("dwelling_limit", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("multi_policy", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("claims_free", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("claims_free_years", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("canonical_premium", "NUMERIC", mode="REQUIRED"),
]

EXPOSURE_RESULTS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("total_policies", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("exposed_policies", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("behaviorally_affected", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("financially_affected", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("expected_premium", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("target_premium", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("signed_variance", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("absolute_variance", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("decision", "STRING", mode="REQUIRED"),
]


class PortfolioPolicyRecord(BaseModel):
    """Canonical policy record model stored in portfolio repositories."""

    policy_id: str
    product_id: str
    state: str
    form: str
    transaction_type: TransactionType
    effective_date: date
    territory: str
    roof_age: int
    deductible: int
    protection_class: int
    construction_type: str
    dwelling_limit: int
    multi_policy: bool
    claims_free: bool
    claims_free_years: int = 3
    canonical_premium: Decimal

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["transaction_type"] = self.transaction_type.value
        d["effective_date"] = self.effective_date.isoformat()
        d["canonical_premium"] = str(self.canonical_premium)
        return d


class PortfolioSummaryStats(BaseModel):
    """Portfolio distribution summary metrics."""

    total_policies: int
    total_expected_premium: Decimal
    roof_band_21_30_count: int
    territory_counts: dict[str, int] = Field(default_factory=dict)
