"""Strict Pydantic response schemas for every real Gemini decision point.

Gemini is only ever asked to choose among IDs/tools that the deterministic
engines already produced. These schemas exist so a malformed, over-budget, or
out-of-vocabulary response can be rejected by validation *before* any tool
executes — Gemini proposes, the supervisor validates, the deterministic
engines act.
"""

from typing import Literal

from pydantic import BaseModel, Field

# Hard budgets enforced both in the schema (so Gemini cannot even request more)
# and again defensively by the supervisor after parsing.
MAX_SELECTED_DIFFERENCES = 10
MAX_SELECTED_TESTS = 8
MAX_ADDITIONAL_PROBE_TESTS = 5
MAX_REMEDIATION_FINDINGS = 20
MAX_REGRESSION_TESTS = 5


class GeminiDecisionBase(BaseModel):
    """Common fields required on every structured Gemini decision.

    `rationale` is the only free-text field allowed — it is a concise,
    user-facing justification, never a hidden chain-of-thought dump.
    """

    rationale: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_review: bool = False


class DifferencePrioritizationDecision(GeminiDecisionBase):
    """Selects which semantic differences deserve investigative priority."""

    decision_type: Literal["PRIORITIZE_DIFFERENCES"] = "PRIORITIZE_DIFFERENCES"
    selected_difference_ids: list[str] = Field(max_length=MAX_SELECTED_DIFFERENCES)


class TestSelectionDecision(GeminiDecisionBase):
    """Selects which deterministically-generated candidate boundary tests to run."""

    decision_type: Literal["SELECT_BOUNDARY_TESTS"] = "SELECT_BOUNDARY_TESTS"
    selected_test_ids: list[str] = Field(min_length=1, max_length=MAX_SELECTED_TESTS)


class EvidenceSufficiencyDecision(GeminiDecisionBase):
    """Decides whether enough evidence has been gathered, or one more bounded
    probe round is warranted against the untested candidate pool."""

    decision_type: Literal["EVIDENCE_SUFFICIENCY"] = "EVIDENCE_SUFFICIENCY"
    stop_condition: Literal["SUFFICIENT", "NEEDS_MORE_EVIDENCE"]
    additional_test_ids: list[str] = Field(default_factory=list, max_length=MAX_ADDITIONAL_PROBE_TESTS)


class PortfolioAnalysisDecision(GeminiDecisionBase):
    """Justifies whether the (costly) 50K-policy portfolio scan should run.

    Only consulted when zero mismatches were reproduced — the supervisor
    always forces the scan when a mismatch exists, regardless of this vote.
    """

    decision_type: Literal["PORTFOLIO_JUSTIFICATION"] = "PORTFOLIO_JUSTIFICATION"
    should_run_portfolio: bool


class RemediationProposalDecision(GeminiDecisionBase):
    """Selects which confirmed findings a remediation candidate should correct.

    Gemini may only choose from finding IDs the deterministic diff engine
    already produced — it cannot invent a value or a finding.
    """

    decision_type: Literal["PROPOSE_REMEDIATION"] = "PROPOSE_REMEDIATION"
    selected_finding_ids: list[str] = Field(min_length=1, max_length=MAX_REMEDIATION_FINDINGS)


class AlignmentOptionsDecision(GeminiDecisionBase):
    """Symmetric-Equivalence counterpart to RemediationProposalDecision.

    Neither Source A nor Source B is presumed authoritative here, so this
    decision never proposes a correction or a "restored intent" value -- it
    only flags which confirmed differences are material enough that a human
    should weigh them before choosing an alignment reference. The concrete
    directional patch is generated on demand, only after that human choice,
    via POST /missions/{id}/alignment-options -- never automatically here.
    """

    decision_type: Literal["PROPOSE_ALIGNMENT_OPTIONS"] = "PROPOSE_ALIGNMENT_OPTIONS"
    selected_finding_ids: list[str] = Field(min_length=1, max_length=MAX_REMEDIATION_FINDINGS)


class RemediationRevalidationSelectionDecision(GeminiDecisionBase):
    """Selects targeted (previously-mismatched) and regression (control)
    scenario IDs to rerun deterministically against the patched candidate."""

    decision_type: Literal["SELECT_REVALIDATION_TESTS"] = "SELECT_REVALIDATION_TESTS"
    targeted_test_ids: list[str] = Field(default_factory=list, max_length=MAX_SELECTED_TESTS)
    regression_test_ids: list[str] = Field(default_factory=list, max_length=MAX_REGRESSION_TESTS)


class ExtractionStrategyDecision(GeminiDecisionBase):
    """Chooses an extraction strategy for an unstructured source (e.g. PDF)."""

    decision_type: Literal["CHOOSE_EXTRACTION_STRATEGY"] = "CHOOSE_EXTRACTION_STRATEGY"
    requested_tool: str
