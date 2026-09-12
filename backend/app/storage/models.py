from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AssuranceRunStatus(str, Enum):  # noqa: UP042
    """Workflow status for assurance run tracking supporting both legacy and V2 lifecycle."""

    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    CREATED = "CREATED"
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    RUNNING = "RUNNING"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_RETRY = "WAITING_RETRY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class EvidenceType(str, Enum):  # noqa: UP042
    """Categorized evidence type for auditability and evidence lineage."""

    SOURCE = "SOURCE"
    IPIR_PACKAGE = "IPIR_PACKAGE"
    SEMANTIC_DIFF = "SEMANTIC_DIFF"
    IMPACT_ANALYSIS = "IMPACT_ANALYSIS"
    TEST_PLAN = "TEST_PLAN"
    EXECUTION_RESULT = "EXECUTION_RESULT"
    ROOT_CAUSE = "ROOT_CAUSE"
    PORTFOLIO_EXPOSURE = "PORTFOLIO_EXPOSURE"
    ASSURANCE_DECISION = "ASSURANCE_DECISION"
    GEMINI_INVOCATION = "GEMINI_INVOCATION"


class EvidenceRecord(BaseModel):
    """Typed evidence record connecting inputs to findings and decision."""

    evidence_id: str
    run_id: str
    evidence_type: EvidenceType
    title: str
    description: str
    source_ref: str | None = None
    target_ref: str | None = None
    data_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunEvent(BaseModel):
    """Audit log entry for an agentic assurance run step or milestone."""

    event_id: str
    run_id: str
    stage: str
    agent_name: str = "AssuranceWorker"
    action: str = "STAGE_CHANGED"
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssuranceRunRecord(BaseModel):
    """Persistence record for a complete assurance run state."""

    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AssuranceRunStatus = AssuranceRunStatus.QUEUED
    workflow_stage: str = "QUEUED"
    left_package_id: str | None = None
    right_package_id: str | None = None
    include_portfolio_analysis: bool = True
    semantic_diff_summary: dict[str, Any] = Field(default_factory=dict)
    impact_summary: dict[str, Any] = Field(default_factory=dict)
    test_plan_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    portfolio_summary: dict[str, Any] = Field(default_factory=dict)
    assurance_decision: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    agent_activity: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    summary: str | None = None
    report: Any = None

    # Lifecycle tracking fields (optional for backward compatibility with records
    # persisted before these fields existed; absent on old records defaults apply).
    status_reason: str | None = None
    current_stage: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_number: int = 1
    cancellation_requested: bool = False
