from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.testing.models import PricingTestPlan


class TraceDifference(BaseModel):
    """Represents a divergence detected between expected and actual calculation steps."""

    node_id: str
    node_type: str
    expected_value: Any
    actual_value: Any
    absolute_difference: Decimal | None = None
    percentage_difference: Decimal | None = None
    sequence_position: int
    related_semantic_difference_ids: list[str] = Field(default_factory=list)


class RootCauseFinding(BaseModel):
    """Deterministic root cause explanation for a reconciled premium mismatch."""

    node_id: str
    category: str
    title: str
    explanation: str
    expected_value: Any
    actual_value: Any
    semantic_path: str
    difference_type: DifferenceType
    severity: DifferenceSeverity
    semantic_issue_group_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    downstream_effects: list[str] = Field(default_factory=list)
    contributing_root_causes: list[str] = Field(default_factory=list)


class PremiumReconciliationResult(BaseModel):
    """Reconciliation result for a single test scenario execution."""

    scenario_id: str
    scenario_name: str
    expected_premium: Decimal
    actual_premium: Decimal
    absolute_variance: Decimal
    percentage_variance: Decimal
    premium_matches: bool
    trace_diverged: bool = False
    trace_differences: list[TraceDifference] = Field(default_factory=list)
    first_divergent_node: str | None = None
    root_cause: RootCauseFinding | None = None
    contributing_root_causes: list[RootCauseFinding] = Field(default_factory=list)
    related_semantic_difference_ids: list[str] = Field(default_factory=list)
    status: str  # "MATCH" or "MISMATCH"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PricingAssuranceRun(BaseModel):
    """End-to-end assurance run summary aggregating test plan, execution, and RCA findings."""

    test_plan: PricingTestPlan
    execution_results: list[PremiumReconciliationResult] = Field(default_factory=list)
    passed_count: int
    failed_count: int
    matched_count: int
    mismatch_count: int
    candidate_reduction_pct: float
    semantic_difference_coverage_pct: float
    behavioral_difference_coverage_pct: float
    premium_difference_reproduction_rate_pct: float
    discovered_root_causes: list[RootCauseFinding] = Field(default_factory=list)
    overall_status: str  # "PASS", "REVIEW", or "FAIL"
    run_metadata: dict[str, Any] = Field(default_factory=dict)
