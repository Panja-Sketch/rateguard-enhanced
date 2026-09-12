from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MissionStatus(StrEnum):
    """Lifecycle status for an Assurance Mission."""
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class ComparisonMode(StrEnum):
    """Assurance Mission comparison modes."""
    EQUIVALENCE = "EQUIVALENCE"
    RELEASE_CONFORMANCE = "RELEASE_CONFORMANCE"


class ValidationIssue(BaseModel):
    """Field-level or source-level validation issue."""
    field: str
    code: str
    message: str
    severity: str = "ERROR"  # ERROR, WARNING, INFO


class MissionObjective(BaseModel):
    """Configuration objectives for an assurance mission."""
    product: str
    jurisdiction: str
    effective_period_start: str
    effective_period_end: str | None = None
    portfolio_dataset: str = "az_ho3_2026_synthetic_50k.csv"
    gating_policy: str = "STRICT_ZERO_DRIFT"  # STRICT_ZERO_DRIFT, TOLERATE_LOW_RISK, MANUAL_APPROVAL
    require_portfolio_analysis: bool = True
    max_portfolio_sample_size: int = 50000


class PricingSourceRef(BaseModel):
    """Reference to a registered or uploaded pricing source."""
    source_id: str
    source_type: str  # FILE, REGISTERED_ID, API_CONNECTOR, SAMPLE_RELEASE
    name: str
    format: str | None = None
    hash_checksum: str | None = None
    compiled_package_id: str | None = None
    # Carried through from the compile-time AdapterResult so the release
    # decision can see it: a source extracted with low confidence and
    # flagged for human review must never silently support a PASS.
    requires_human_review: bool = False


class AgentAction(BaseModel):
    """Log record of a dynamic supervisor agent action."""
    action_id: str
    agent_role: str
    action_type: str  # REASONING, TOOL_INVOCATION, EXPERIMENT, DECISION
    summary: str
    rationale: str | None = None
    selected_tool: str | None = None
    latency_ms: float = 0.0
    # `model_id` MUST stay None unless this specific action is backed by a real,
    # completed Gemini invocation — never stamp a model onto a deterministic step.
    model_id: str | None = None
    invocation_id: str | None = None
    decision_type: str | None = None
    is_gemini_decision: bool = False
    is_fallback: bool = False
    fallback_reason: str | None = None
    needs_human_review: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ToolInvocation(BaseModel):
    """Log record of a deterministic Python tool execution."""
    tool_name: str
    input_args: dict[str, Any]
    output_summary: dict[str, Any]
    execution_time_ms: float
    status: str = "SUCCESS"


class MaterialFinding(BaseModel):
    """Discovered semantic, structural, or pricing drift finding."""
    finding_id: str
    category: str  # SEMANTIC_DIFF, DEPENDENCY_IMPACT, TRACE_DIVERGENCE, FINANCIAL_EXPOSURE
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    title: str
    description: str
    intent_value: str | None = None
    target_value: str | None = None
    affected_node: str | None = None


class RuntimeExperiment(BaseModel):
    """Targeted boundary or risk-directed test scenario probe."""
    experiment_id: str
    probe_name: str
    category: str
    risk_inputs: dict[str, Any]
    expected_premium: str
    actual_premium: str
    matches: bool
    first_divergent_node: str | None = None


class RootCauseFinding(BaseModel):
    """Confirmed calculation node root cause finding."""
    node_id: str
    title: str
    explanation: str
    expected_value: str
    actual_value: str
    divergence_type: str


class BlastRadiusResult(BaseModel):
    """Evaluated 50K portfolio blast radius and telemetry."""
    total_policies_analyzed: int = 0
    semantically_exposed_count: int = 0
    behaviorally_affected_count: int = 0
    financially_affected_count: int = 0
    undercharged_policy_count: int = 0
    overcharged_policy_count: int = 0
    total_undercharge_amount: str = "0.00"
    total_overcharge_amount: str = "0.00"
    signed_net_variance: str = "0.00"
    absolute_financial_exposure: str = "0.00"
    multi_defect_policy_count: int = 0
    portfolio_execution_seconds: float = 0.0
    measured_throughput_policies_per_sec: float = 0.0


class RemediationProposal(BaseModel):
    """Proposed isolated patch for a confirmed pricing defect."""
    remediation_id: str
    title: str
    rationale: str
    derived_package_id: str
    proposed_changes: dict[str, Any]
    source_evidence_ref: str


class RevalidationResult(BaseModel):
    """Revalidation analysis comparing BEFORE vs AFTER financial exposure."""
    revalidation_id: str
    remediation_id: str
    before_absolute_exposure: str
    after_absolute_exposure: str
    before_affected_policies: int
    after_affected_policies: int
    exposure_eliminated_pct: float
    new_release_decision: str
    targeted_tests_rerun: int = 0
    targeted_tests_passed: int = 0
    regression_tests_rerun: int = 0
    regression_tests_passed: int = 0


class ReleaseDecision(BaseModel):
    """Final release decision record."""
    status: str  # PASS, REVIEW_REQUIRED, BLOCK_DEPLOYMENT
    confidence_score: float = 1.0
    summary: str
    blocking_reasons: list[str] = Field(default_factory=list)
    recommendation: str


class AssuranceMission(BaseModel):
    """Assurance Mission V2 Root Domain Model."""
    mission_id: str
    name: str
    mode: ComparisonMode
    status: MissionStatus = MissionStatus.DRAFT
    objective: MissionObjective
    source_a: PricingSourceRef
    source_b: PricingSourceRef | None = None
    disposable_sample_run: bool = False
    is_demo_sample: bool = Field(
        default=False,
        description="True only when the user explicitly opted into a built-in demo/sample source or connector.",
    )
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
