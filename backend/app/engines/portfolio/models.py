from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.ipir.enums import TransactionType


class SyntheticPolicy(BaseModel):
    """Represents a single policy record in a synthetic portfolio."""

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
    target_premium: Decimal | None = None


class DefectExposure(BaseModel):
    """Exposure breakdown for a single conceptual defect/issue."""

    issue_id: str
    issue_name: str
    exposed_count: int
    behaviorally_affected_count: int
    financially_affected_count: int
    total_expected_premium: Decimal
    total_target_premium: Decimal
    signed_variance: Decimal
    absolute_variance: Decimal
    exposed_policy_pct: float
    affected_policy_pct: float


class PortfolioExposureResult(BaseModel):
    """Summary of 50,000 policy portfolio blast radius and financial exposure."""

    portfolio_id: str = "AZ_HO3_2026_SYNTHETIC_50K"
    total_policies: int
    exposed_policy_count: int
    exposed_policy_pct: float
    behaviorally_affected_count: int = 0
    behaviorally_affected_pct: float = 0.0
    financially_affected_count: int
    financially_affected_pct: float
    total_expected_premium: Decimal
    total_target_premium: Decimal
    total_signed_variance: Decimal
    total_absolute_variance: Decimal
    undercharged_policy_count: int = 0
    total_undercharge_amount: Decimal = Decimal("0.00")
    overcharged_policy_count: int = 0
    total_overcharge_amount: Decimal = Decimal("0.00")
    average_variance_per_affected_policy: Decimal = Decimal("0.00")
    max_single_policy_variance: Decimal = Decimal("0.00")
    multi_defect_policy_count: int = 0
    defect_exposures: list[DefectExposure] = Field(default_factory=list)
    issue_breakdown: list[DefectExposure] = Field(default_factory=list)
    performance_telemetry: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def undercharged_count(self) -> int:
        return self.undercharged_policy_count

    @property
    def total_undercharge(self) -> Decimal:
        return self.total_undercharge_amount

    @property
    def overcharged_count(self) -> int:
        return self.overcharged_policy_count

    @property
    def total_overcharge(self) -> Decimal:
        return self.total_overcharge_amount
