"""Consumer-protection analytics computed over the same synthetic portfolio
repricing pass PortfolioAnalyzer already runs (locked doc section 9):

- Cohort impact distribution ("Impact Distribution Review", section 9.3) --
  explicitly synthetic, non-demographic policy-shape cohorts (territory,
  construction type), never protected-class data, with minimum-size
  suppression and the required disclaimer.
- Upcoming 30/60/90-day renewal pipeline impact (section 9.2).

Both are pure functions over `PolicyImpactRecord`s that `PortfolioAnalyzer`
builds during its existing per-policy repricing loop -- no second pricing
pass, no new BigQuery dependency (PortfolioAnalyzer itself already evaluates
the full synthetic CSV in-memory rather than via BigQuery, so this reuses
that same architecture rather than introducing a second one).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.engines.portfolio.models import SyntheticPolicy
from app.ipir.enums import TransactionType

IMPACT_DISTRIBUTION_DISCLAIMER = (
    "This screening identifies uneven outcomes in the supplied test cohorts. "
    "It is not a legal finding of unfair discrimination and does not replace "
    "actuarial, compliance, or legal review."
)

# Synthetic, non-demographic policy-shape dimensions only (locked doc section
# 9.1: the synthetic dataset carries no protected-class attributes at all).
DEFAULT_COHORT_DIMENSIONS: tuple[str, ...] = ("territory", "construction_type")
DEFAULT_MINIMUM_COHORT_SIZE = 30
DEFAULT_PIPELINE_WINDOWS: tuple[tuple[int, int, str], ...] = (
    (0, 30, "0-30"),
    (31, 60, "31-60"),
    (61, 90, "61-90"),
)


@dataclass
class PolicyImpactRecord:
    """One financially-affected policy's repricing outcome, captured during
    PortfolioAnalyzer's existing per-policy loop."""

    policy_id: str
    signed_variance: Decimal
    absolute_variance: Decimal

    @property
    def overcharged(self) -> bool:
        return self.signed_variance > Decimal("0")


class CohortMetrics(BaseModel):
    cohort_dimension: str
    cohort_value: str
    sample_size: int
    suppressed: bool = False
    selection_rate: float | None = None
    affected_rate: float | None = None
    mean_absolute_change: str | None = None
    mean_percentage_change: float | None = None
    overcharge_rate: float | None = None


class CohortDistributionResult(BaseModel):
    minimum_cohort_size: int = DEFAULT_MINIMUM_COHORT_SIZE
    disclaimer: str = IMPACT_DISTRIBUTION_DISCLAIMER
    cohorts: list[CohortMetrics] = Field(default_factory=list)


class RenewalWindowBucket(BaseModel):
    window_label: str
    affected_renewal_count: int
    total_absolute_impact: str


class PipelineImpactResult(BaseModel):
    as_of_date: date
    window_days: int = 90
    buckets: list[RenewalWindowBucket] = Field(default_factory=list)
    total_affected_renewals_next_90_days: int = 0


def compute_cohort_distribution(
    policies: list[SyntheticPolicy],
    impact_by_policy: dict[str, PolicyImpactRecord],
    exposed_policy_ids: set[str],
    dimensions: tuple[str, ...] = DEFAULT_COHORT_DIMENSIONS,
    minimum_cohort_size: int = DEFAULT_MINIMUM_COHORT_SIZE,
) -> CohortDistributionResult:
    """Selection rate = share of the cohort that entered scope (was exposed
    to a changed rule). Affected rate = share financially affected. A cohort
    below `minimum_cohort_size` is listed with its sample size only -- every
    metric field stays null, never a value computed from too few records."""
    cohorts: list[CohortMetrics] = []

    for dim in dimensions:
        totals: dict[str, int] = {}
        exposed: dict[str, int] = {}
        affected: dict[str, int] = {}
        abs_change: dict[str, Decimal] = {}
        pct_changes: dict[str, list[float]] = {}
        overcharged: dict[str, int] = {}

        for pol in policies:
            value = str(getattr(pol, dim))
            totals[value] = totals.get(value, 0) + 1
            if pol.policy_id in exposed_policy_ids:
                exposed[value] = exposed.get(value, 0) + 1
            rec = impact_by_policy.get(pol.policy_id)
            if rec is not None:
                affected[value] = affected.get(value, 0) + 1
                abs_change[value] = abs_change.get(value, Decimal("0")) + rec.absolute_variance
                if pol.canonical_premium:
                    pct_changes.setdefault(value, []).append(
                        float(rec.absolute_variance / pol.canonical_premium * 100)
                    )
                if rec.overcharged:
                    overcharged[value] = overcharged.get(value, 0) + 1

        for value, total in sorted(totals.items()):
            if total < minimum_cohort_size:
                cohorts.append(
                    CohortMetrics(cohort_dimension=dim, cohort_value=value, sample_size=total, suppressed=True)
                )
                continue

            aff = affected.get(value, 0)
            pct_list = pct_changes.get(value, [])
            cohorts.append(
                CohortMetrics(
                    cohort_dimension=dim,
                    cohort_value=value,
                    sample_size=total,
                    suppressed=False,
                    selection_rate=round(exposed.get(value, 0) / total, 4),
                    affected_rate=round(aff / total, 4),
                    mean_absolute_change=(
                        str((abs_change.get(value, Decimal("0")) / aff).quantize(Decimal("0.01"))) if aff else None
                    ),
                    mean_percentage_change=(round(sum(pct_list) / len(pct_list), 2) if pct_list else None),
                    overcharge_rate=(round(overcharged.get(value, 0) / aff, 4) if aff else None),
                )
            )

    return CohortDistributionResult(minimum_cohort_size=minimum_cohort_size, cohorts=cohorts)


def compute_pipeline_impact(
    policies: list[SyntheticPolicy],
    impact_by_policy: dict[str, PolicyImpactRecord],
    as_of: date,
    windows: tuple[tuple[int, int, str], ...] = DEFAULT_PIPELINE_WINDOWS,
) -> PipelineImpactResult:
    """Buckets financially-affected RENEWAL transactions by days-until-renewal
    from `as_of`. New-business transactions are excluded -- this is renewal
    impact specifically (locked doc section 9.2: "30/60/90-day affected
    renewal counts"), not all upcoming new business."""
    pol_by_id = {p.policy_id: p for p in policies}
    buckets: list[RenewalWindowBucket] = []
    total = 0

    for lo, hi, label in windows:
        count = 0
        abs_impact = Decimal("0.00")
        for pid, rec in impact_by_policy.items():
            pol = pol_by_id.get(pid)
            if pol is None or pol.transaction_type != TransactionType.RENEWAL:
                continue
            delta_days = (pol.effective_date - as_of).days
            if lo <= delta_days <= hi:
                count += 1
                abs_impact += rec.absolute_variance
        buckets.append(
            RenewalWindowBucket(
                window_label=label,
                affected_renewal_count=count,
                total_absolute_impact=str(abs_impact.quantize(Decimal("0.01"))),
            )
        )
        total += count

    return PipelineImpactResult(as_of_date=as_of, buckets=buckets, total_affected_renewals_next_90_days=total)
