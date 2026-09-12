from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from app.models.mission import (
    AgentAction,
    BlastRadiusResult,
    MaterialFinding,
    ReleaseDecision,
    RemediationProposal,
    RevalidationResult,
    RootCauseFinding,
    RuntimeExperiment,
    ValidationIssue,
)


class AnalysisStatus(StrEnum):
    """Execution status for an individual analysis section."""
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


T = TypeVar("T")


class SectionResult[T](BaseModel):
    """Generic wrapper enforcing explicit section status and explanation."""
    status: AnalysisStatus = AnalysisStatus.NOT_RUN
    reason: str | None = None
    error_message: str | None = None
    data: T | None = None


class SemanticAnalysisData(BaseModel):
    difference_count: int = 0
    differences: list[MaterialFinding] = Field(default_factory=list)
    summary: str = ""


class ImpactAnalysisData(BaseModel):
    changed_nodes: list[str] = Field(default_factory=list)
    impacted_calculation_nodes: list[str] = Field(default_factory=list)
    affected_pricing_outputs: list[str] = Field(default_factory=list)
    risk_predicates: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentsData(BaseModel):
    total_generated: int = 0
    total_executed: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    reduction_pct: float = 0.0
    experiments: list[RuntimeExperiment] = Field(default_factory=list)


class ReconciliationData(BaseModel):
    mismatch_count: int = 0
    first_divergent_node: str | None = None
    root_cause: RootCauseFinding | None = None


class AssuranceResultV2(BaseModel):
    """Authoritative result contract for Assurance Mission V2."""
    mission_id: str
    mode: str
    overall_status: str  # QUEUED, RUNNING, COMPLETED, FAILED, NEEDS_REVIEW
    ai_runtime: dict[str, str] = Field(
        default_factory=lambda: {
            "model_id": "gemini-3.7-flash",
            # Names the framework that actually executes structured decisions
            # (google-genai structured function-calling). Must never say "Google
            # ADK" unless the deployed execution path genuinely runs through it.
            "framework": "Google GenAI SDK (google-genai structured output)",
            # Honest default: no live Gemini invocation has occurred until a caller
            # explicitly overrides this with real invocation evidence.
            "model_status": "NOT_INVOKED_DETERMINISTIC_PIPELINE",
        }
    )
    validation: SectionResult[list[ValidationIssue]] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    agent_execution: SectionResult[list[AgentAction]] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    semantic_analysis: SectionResult[SemanticAnalysisData] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    impact_analysis: SectionResult[ImpactAnalysisData] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    experiments: SectionResult[ExperimentsData] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    reconciliation: SectionResult[ReconciliationData] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    blast_radius: SectionResult[BlastRadiusResult] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    remediation: SectionResult[RemediationProposal] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    revalidation: SectionResult[RevalidationResult] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    release_decision: SectionResult[ReleaseDecision] = Field(
        default_factory=lambda: SectionResult(status=AnalysisStatus.NOT_RUN)
    )
    evidence_refs: list[str] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
