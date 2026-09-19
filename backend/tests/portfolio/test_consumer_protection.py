from datetime import date
from decimal import Decimal

from app.engines.portfolio.consumer_protection import (
    DEFAULT_MINIMUM_COHORT_SIZE,
    IMPACT_DISTRIBUTION_DISCLAIMER,
    PolicyImpactRecord,
    compute_cohort_distribution,
    compute_pipeline_impact,
)
from app.engines.portfolio.models import SyntheticPolicy
from app.ipir.enums import TransactionType


def _policy(policy_id: str, territory: str, construction_type: str, effective_date: date, txn_type=TransactionType.RENEWAL) -> SyntheticPolicy:
    return SyntheticPolicy(
        policy_id=policy_id,
        product_id="AZ_HO3",
        state="AZ",
        form="HO3",
        transaction_type=txn_type,
        effective_date=effective_date,
        territory=territory,
        roof_age=10,
        deductible=1000,
        protection_class=3,
        construction_type=construction_type,
        dwelling_limit=300000,
        multi_policy=True,
        claims_free=True,
        canonical_premium=Decimal("700.00"),
    )


def test_cohort_below_minimum_size_is_suppressed():
    policies = [_policy(f"P{i}", "T01", "FRAME", date(2026, 12, 1)) for i in range(5)]
    result = compute_cohort_distribution(policies, {}, set())
    territory_cohort = next(c for c in result.cohorts if c.cohort_dimension == "territory" and c.cohort_value == "T01")
    assert territory_cohort.suppressed is True
    assert territory_cohort.sample_size == 5
    assert territory_cohort.selection_rate is None
    assert territory_cohort.affected_rate is None
    assert result.disclaimer == IMPACT_DISTRIBUTION_DISCLAIMER


def test_cohort_above_minimum_computes_real_metrics():
    n = DEFAULT_MINIMUM_COHORT_SIZE + 10
    policies = [_policy(f"P{i}", "T02", "MASONRY", date(2026, 12, 1)) for i in range(n)]
    exposed = {p.policy_id for p in policies[:20]}
    impact = {
        p.policy_id: PolicyImpactRecord(policy_id=p.policy_id, signed_variance=Decimal("45.00"), absolute_variance=Decimal("45.00"))
        for p in policies[:10]
    }
    result = compute_cohort_distribution(policies, impact, exposed)
    cohort = next(c for c in result.cohorts if c.cohort_dimension == "territory" and c.cohort_value == "T02")
    assert cohort.suppressed is False
    assert cohort.sample_size == n
    assert cohort.selection_rate == round(20 / n, 4)
    assert cohort.affected_rate == round(10 / n, 4)
    assert cohort.mean_absolute_change == "45.00"
    assert cohort.overcharge_rate == 1.0


def test_pipeline_impact_buckets_only_renewals_within_90_days():
    as_of = date(2026, 9, 17)
    policies = [
        _policy("P1", "T01", "FRAME", date(2026, 9, 25), TransactionType.RENEWAL),  # 8 days -> 0-30
        _policy("P2", "T01", "FRAME", date(2026, 10, 30), TransactionType.RENEWAL),  # 43 days -> 31-60
        _policy("P3", "T01", "FRAME", date(2026, 12, 10), TransactionType.RENEWAL),  # 84 days -> 61-90
        _policy("P4", "T01", "FRAME", date(2027, 3, 1), TransactionType.RENEWAL),  # out of window
        _policy("P5", "T01", "FRAME", date(2026, 9, 25), TransactionType.NEW_BUSINESS),  # excluded: not a renewal
    ]
    impact = {
        pid: PolicyImpactRecord(policy_id=pid, signed_variance=Decimal("10.00"), absolute_variance=Decimal("10.00"))
        for pid in ("P1", "P2", "P3", "P4", "P5")
    }
    result = compute_pipeline_impact(policies, impact, as_of)
    counts = {b.window_label: b.affected_renewal_count for b in result.buckets}
    assert counts == {"0-30": 1, "31-60": 1, "61-90": 1}
    assert result.total_affected_renewals_next_90_days == 3


def test_pipeline_impact_excludes_unaffected_policies():
    as_of = date(2026, 9, 17)
    policies = [_policy("P1", "T01", "FRAME", date(2026, 9, 20), TransactionType.RENEWAL)]
    result = compute_pipeline_impact(policies, {}, as_of)
    assert result.total_affected_renewals_next_90_days == 0
