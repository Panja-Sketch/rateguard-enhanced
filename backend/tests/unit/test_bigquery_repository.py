from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.engines.impact.models import ImpactPredicate, PredicateClause
from app.engines.portfolio.models import PortfolioExposureResult
from app.ipir.enums import ComparisonOperator
from app.storage.portfolio import (
    BigQueryPortfolioRepository,
    LocalPortfolioRepository,
    get_portfolio_repository,
    translate_predicates_to_bigquery_where,
)
from app.storage.portfolio.models import SYNTHETIC_POLICIES_SCHEMA
from scripts.upload_synthetic_portfolio_bigquery import upload_portfolio
from scripts.verify_bigquery_portfolio_parity import compare_metrics, verify_parity


def test_sql_translator_parameterized_query():
    """Verifies sql_translator builds safe parameterized SQL filters."""
    predicates = [
        ImpactPredicate(
            id="PRED-01",
            description="Roof age 21 to 30",
            clauses=[
                PredicateClause(field="roof_age", operator=ComparisonOperator.GTE, value=21),
                PredicateClause(field="roof_age", operator=ComparisonOperator.LTE, value=30),
            ],
        ),
        ImpactPredicate(
            id="PRED-02",
            description="Territory T17",
            clauses=[
                PredicateClause(field="territory", operator=ComparisonOperator.EQ, value="T17"),
            ],
        ),
    ]

    p_filter = translate_predicates_to_bigquery_where(predicates)

    assert "WHERE" in p_filter.where_clause
    assert "roof_age >= @p_1" in p_filter.where_clause
    assert "roof_age <= @p_2" in p_filter.where_clause
    assert "territory = @p_3" in p_filter.where_clause
    assert len(p_filter.query_params) == 3

    # Check parameters
    assert p_filter.query_params[0]["name"] == "p_1"
    assert p_filter.query_params[0]["value"] == 21
    assert p_filter.query_params[1]["name"] == "p_2"
    assert p_filter.query_params[1]["value"] == 30
    assert p_filter.query_params[2]["name"] == "p_3"
    assert p_filter.query_params[2]["value"] == "T17"


def test_sql_translator_ignores_unallowed_fields():
    """Verifies sql_translator ignores unallowed field names to prevent SQL injection."""
    predicates = [
        ImpactPredicate(
            id="PRED-INJECT",
            description="SQL Injection attempt",
            clauses=[
                PredicateClause(
                    field="DROP TABLE synthetic_policies; --",
                    operator=ComparisonOperator.EQ,
                    value="test",
                ),
            ],
        )
    ]

    p_filter = translate_predicates_to_bigquery_where(predicates)
    assert p_filter.where_clause == ""
    assert len(p_filter.query_params) == 0


def test_local_portfolio_repository():
    """Tests LocalPortfolioRepository loading and query filtering."""
    repo = LocalPortfolioRepository()
    policies = repo.load_policies(limit=10)
    assert len(policies) == 10
    assert policies[0].policy_id.startswith("POL-AZ-")

    summary = repo.get_portfolio_summary()
    assert summary.total_policies >= 1000


def test_csv_canonical_premium_is_decimal():
    """Verifies CSV canonical premium parsing produces exact Python Decimal instances without float conversion."""
    repo = LocalPortfolioRepository()
    policies = repo.load_policies(limit=5)
    for pol in policies:
        assert isinstance(pol.canonical_premium, Decimal)
        assert not isinstance(pol.canonical_premium, float)


def test_schema_numeric_definitions():
    """Verifies explicit BigQuery schema defines
    canonical_premium as NUMERIC."""
    prem_field = next(f for f in SYNTHETIC_POLICIES_SCHEMA if f.name == "canonical_premium")
    assert prem_field.field_type == "NUMERIC"
    assert prem_field.mode == "REQUIRED"



@patch("scripts.upload_synthetic_portfolio_bigquery.bigquery.Client")
def test_upload_partial_count_guard(mock_bq_client_cls):
    """Verifies duplicate count guard stops execution
    when partial count exists."""
    mock_client = MagicMock()
    mock_bq_client_cls.return_value = mock_client
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [{"cnt": 1000}]
    mock_client.query.return_value = mock_query_job

    import scripts.upload_synthetic_portfolio_bigquery as upload_script

    with pytest.raises(SystemExit):
        upload_script.main()


@patch("scripts.upload_synthetic_portfolio_bigquery.bigquery.Client")
def test_upload_duplicate_50k_guard_skips(mock_bq_client_cls):
    """Verifies duplicate guard skips upload when 50,000
    records are already present."""
    mock_client = MagicMock()
    mock_bq_client_cls.return_value = mock_client

    # Mock query returning full count of 50,000 records
    mock_row = {"cnt": 50000}
    mock_client.query.return_value.result.return_value = [mock_row]

    # Should skip upload without error and without calling load_table_from_json
    upload_portfolio(replace_demo_data=False)
    assert not mock_client.load_table_from_json.called


def test_bigquery_repository_mocked():
    """Tests BigQueryPortfolioRepository using a mocked BigQuery client."""
    mock_client = MagicMock()

    # Mock query result for load_policies
    mock_row = {
        "policy_id": "POL-AZ-000001",
        "product_id": "AZ_HO3",
        "state": "AZ",
        "form": "HO3",
        "transaction_type": "NEW_BUSINESS",
        "effective_date": "2026-09-15",
        "territory": "T01",
        "roof_age": 10,
        "deductible": 1000,
        "protection_class": 3,
        "construction_type": "FRAME",
        "dwelling_limit": 300000,
        "multi_policy": True,
        "claims_free": True,
        "claims_free_years": 5,
        "canonical_premium": "1200.50",
    }

    mock_row_obj = MagicMock()
    mock_row_obj.items.return_value = mock_row.items()
    mock_client.query.return_value.result.return_value = [mock_row_obj]

    repo = BigQueryPortfolioRepository(client=mock_client)
    policies = repo.load_policies(limit=1)

    assert len(policies) == 1
    assert policies[0].policy_id == "POL-AZ-000001"
    assert policies[0].canonical_premium == Decimal("1200.50")


def test_factory_returns_local_by_default():
    """Verifies factory returns LocalPortfolioRepository when BigQuery disabled."""
    repo = get_portfolio_repository()
    assert isinstance(repo, LocalPortfolioRepository)


@patch("scripts.verify_bigquery_portfolio_parity.BigQueryPortfolioRepository")
def test_verify_parity_script_mocked_success(mock_bq_repo_cls):
    """Verifies parity script compares Local and BigQuery repositories cleanly using current PortfolioAnalyzer API."""
    local_repo = LocalPortfolioRepository()
    sample_policies = local_repo.load_policies(limit=10)

    # Reverse order to verify order independence sorting
    reversed_policies = list(reversed(sample_policies))
    mock_instance = MagicMock()
    mock_instance.load_policies.return_value = reversed_policies
    mock_bq_repo_cls.return_value = mock_instance

    result = verify_parity(limit=10)
    assert result is True


@patch("scripts.verify_bigquery_portfolio_parity.BigQueryPortfolioRepository")
def test_verify_parity_script_detects_mismatch(mock_bq_repo_cls):
    """Verifies parity script detects financial mismatches and returns False status."""
    local_repo = LocalPortfolioRepository()
    sample_policies = local_repo.load_policies(limit=10)

    # Alter canonical_premium on one policy to simulate data drift
    corrupted = []
    for idx, p in enumerate(sample_policies):
        p_dict = p.model_dump()
        if idx == 0:
            p_dict["canonical_premium"] = Decimal("99999.99")
        corrupted.append(
            LocalPortfolioRepository()._parse_row(
                {
                    "policy_id": p_dict["policy_id"],
                    "product_id": p_dict["product_id"],
                    "state": p_dict["state"],
                    "form": p_dict["form"],
                    "transaction_type": p_dict["transaction_type"].value,
                    "effective_date": p_dict["effective_date"].isoformat(),
                    "territory": "T20" if idx == 0 else p_dict["territory"],
                    "roof_age": str(p_dict["roof_age"]),
                    "deductible": str(p_dict["deductible"]),
                    "protection_class": str(p_dict["protection_class"]),
                    "construction_type": p_dict["construction_type"],
                    "dwelling_limit": str(p_dict["dwelling_limit"]),
                    "multi_policy": str(p_dict["multi_policy"]),
                    "claims_free": str(p_dict["claims_free"]),
                    "claims_free_years": str(p_dict["claims_free_years"]),
                    "canonical_premium": str(p_dict["canonical_premium"]),
                }
            )
        )

    mock_instance = MagicMock()
    mock_instance.load_policies.return_value = corrupted
    mock_bq_repo_cls.return_value = mock_instance

    result = verify_parity(limit=10)
    assert result is False


def test_compare_metrics_exact_decimal():
    """Verifies compare_metrics enforces exact Decimal equality without float tolerance."""
    res1 = PortfolioExposureResult(
        total_policies=1,
        exposed_policy_count=1,
        exposed_policy_pct=100.0,
        behaviorally_affected_count=1,
        behaviorally_affected_pct=100.0,
        financially_affected_count=1,
        financially_affected_pct=100.0,
        total_expected_premium=Decimal("100.00"),
        total_target_premium=Decimal("90.00"),
        total_signed_variance=Decimal("-10.00"),
        total_absolute_variance=Decimal("10.00"),
        undercharged_count=1,
        total_undercharge=Decimal("10.00"),
        overcharged_count=0,
        total_overcharge=Decimal("0.00"),
    )

    res2 = PortfolioExposureResult(
        total_policies=1,
        exposed_policy_count=1,
        exposed_policy_pct=100.0,
        behaviorally_affected_count=1,
        behaviorally_affected_pct=100.0,
        financially_affected_count=1,
        financially_affected_pct=100.0,
        total_expected_premium=Decimal("100.00"),
        total_target_premium=Decimal("90.00"),
        total_signed_variance=Decimal("-10.00"),
        total_absolute_variance=Decimal("10.00000000001"),  # Minor float-like difference
        undercharged_count=1,
        total_undercharge=Decimal("10.00"),
        overcharged_count=0,
        total_overcharge=Decimal("0.00"),
    )

    results = compare_metrics(res1, res2)
    abs_var_match = next(m[3] for m in results if m[0] == "Absolute Variance ($)")
    assert abs_var_match is False
