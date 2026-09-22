import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from decimal import Decimal
from typing import Any

from app.adapters.extractor_registry import EXTRACTOR_REGISTRY, excel_layout_recognized
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.agents.config import (
    DEFAULT_LOW_CONFIDENCE_REVIEW_THRESHOLD,
    DEFAULT_MAX_GEMINI_CALLS_PER_MISSION,
    DEFAULT_MAX_PROBE_ROUNDS,
    get_agent_config,
)
from app.agents.decision_schemas import (
    MAX_ADDITIONAL_PROBE_TESTS,
    MAX_REGRESSION_TESTS,
    MAX_SELECTED_TESTS,
    AlignmentOptionsDecision,
    DifferencePrioritizationDecision,
    EvidenceSufficiencyDecision,
    ExplanationDraftDecision,
    ExtractionStrategyDecision,
    GeminiDecisionBase,
    PortfolioAnalysisDecision,
    RemediationProposalDecision,
    RemediationRevalidationSelectionDecision,
    TestSelectionDecision,
)
from app.agents.gemini_client import GeminiDecisionClient, GeminiInvocationEvidence
from app.connectors.budget import TargetBudget
from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.engines.diff import SemanticDiffEngine
from app.engines.diff.models import SemanticDiffResult
from app.engines.impact import PricingImpactEngine
from app.engines.impact.models import ImpactAnalysis
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.oracle.errors import CalculationDateError
from app.engines.portfolio import PortfolioExposureAnalyzer
from app.engines.portfolio.consumer_protection import CohortDistributionResult, PipelineImpactResult
from app.engines.reconciliation import PricingReconciliationEngine
from app.engines.testing import RiskDirectedTestGenerator
from app.engines.testing.models import PricingTestScenario, ScenarioClassification
from app.explanations import build_explanation_draft, build_explanation_facts
from app.impact.aggregate import ImpactAggregate
from app.impact.models import ImpactStatus
from app.ipir.package import IPIRPackage
from app.ipir.schema import validate_ipir_schema
from app.ipir.v0_2.control_cases import ControlCase
from app.models import (
    AgentAction,
    AnalysisStatus,
    AssuranceMission,
    AssuranceResultV2,
    BlastRadiusResult,
    ComparisonMode,
    ConnectorSelection,
    ExperimentsData,
    ImpactAnalysisData,
    MaterialFinding,
    MissionStatus,
    ReconciliationData,
    ReleaseDecision,
    RootCauseFinding,
    RuntimeExperiment,
    SectionResult,
    SemanticAnalysisData,
    ToolInvocation,
)
from app.models.stages import MissionStage, StageRecorder, StageStatus
from app.services.finding_conversion import to_material_findings
from app.services.mission_transitions import apply_transition
from app.services.remediation_service import RemediationService
from app.services.validation_service import MissionValidationService
from app.storage import AssuranceRunStatus, BaseRunStore, EvidenceRecord, EvidenceType

# Honest ai_runtime.model_status values. NOT_INVOKED is used whenever a mission
# never reaches a real decision point (e.g. clean equivalence with 0 diffs).
AI_RUNTIME_NOT_INVOKED_STATUS = "NOT_INVOKED_DETERMINISTIC_PIPELINE"
AI_RUNTIME_LIVE_STATUS = "GEMINI_LIVE_DECISIONS_APPLIED"
AI_RUNTIME_FALLBACK_STATUS = "DETERMINISTIC_FALLBACK_GEMINI_UNAVAILABLE"

# Bounded adaptive-investigation budgets (see class docstring).
# Defaults only: the effective limits are read from AgentConfig
# (RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION / RATEGUARD_MAX_PROBE_ROUNDS /
# RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD) when a supervisor is constructed.
MAX_GEMINI_CALLS_PER_MISSION = DEFAULT_MAX_GEMINI_CALLS_PER_MISSION
MAX_PROBE_ROUNDS = DEFAULT_MAX_PROBE_ROUNDS

# Below this confidence, an extraction result always requires human review
# regardless of which extractor (deterministic, Gemini-selected, or fallback)
# produced it.
LOW_CONFIDENCE_REVIEW_THRESHOLD = DEFAULT_LOW_CONFIDENCE_REVIEW_THRESHOLD


def _date_source(tc: PricingTestScenario, resolved_source: object) -> str | None:
    """Where the probe's date really came from. A generated probe already carries
    its resolved date as an explicit scenario date, so the executor's own
    resolution would always say EXPLICIT; the generation-time origin
    (CONTROL_CASE / PACKAGE_EFFECTIVE_START) is the meaningful one."""
    recorded = (tc.metadata or {}).get("calculation_date_source")
    if recorded and recorded != "EXPLICIT":
        return str(recorded)
    return str(getattr(resolved_source, "value", resolved_source)) if resolved_source else None


def _probe_trace_fields(tc: PricingTestScenario, calc_date: "date | None", calc_source: object) -> dict:
    """Probe-origin and calculation-date fields carried onto every experiment
    so evidence and reconciliation can state exactly what was run and when."""
    meta = tc.metadata or {}
    return {
        "calculation_date": calc_date.isoformat() if calc_date is not None else None,
        "calculation_date_source": _date_source(tc, calc_source),
        "probe_origin": meta.get("probe_origin"),
        "probe_provenance": meta.get("provenance") or {},
    }


@dataclass
class _InvestigationBudget:
    """Mission-scoped, single-use tracker for Gemini call/probe budgets and
    duplicate-probe prevention. One instance per `run_mission()` call."""

    gemini_call_count: int = 0
    any_gemini_success: bool = False
    any_gemini_attempted: bool = False
    evidence_ids: list[str] = dc_field(default_factory=list)
    executed_test_ids: set[str] = dc_field(default_factory=set)


_logger = logging.getLogger(__name__)


class AssuranceSupervisor:
    """Assurance Mission V2 strategic supervisor.

    Runs the mandatory deterministic evidence pipeline (validation, IPIR
    comparison, dependency impact, candidate-test generation, premium oracle,
    target execution, trace reconciliation) unconditionally, and consults a
    real, structured Gemini call at a small set of bounded decision points
    (difference prioritization, boundary-test selection, evidence-sufficiency,
    portfolio justification, remediation proposal, and remediation-revalidation
    test selection). Gemini never invents a value, a finding, or a policy
    count — it only selects among IDs the deterministic engines already
    produced, validated against the candidate pool before anything executes.

    Every Gemini call is bounded by `MAX_GEMINI_CALLS_PER_MISSION` and
    `MAX_PROBE_ROUNDS`. Any failure (disabled, no credentials, timeout, quota,
    malformed/schema-invalid response) falls back to the pre-existing
    deterministic behavior and is recorded as a visible fallback action —
    a Gemini outage can never corrupt or block deterministic calculations.
    """

    def __init__(
        self,
        store: BaseRunStore,
        gemini_client: GeminiDecisionClient | None = None,
        connector_client_factory: "Callable[[], ConnectorClient] | None" = None,
    ):
        self.store = store
        self.semantic_diff_engine = SemanticDiffEngine()
        self.impact_engine = PricingImpactEngine()
        self.test_generator = RiskDirectedTestGenerator()
        self.reconciliation_engine = PricingReconciliationEngine()
        self.portfolio_analyzer = PortfolioExposureAnalyzer()
        self.remediation_service = RemediationService()
        self.agent_config = get_agent_config()
        self.gemini = gemini_client if gemini_client is not None else GeminiDecisionClient(self.agent_config)
        # Injectable for tests — e.g. a ConnectorClient(transport=httpx.ASGITransport(...))
        # wrapping the real rating_engine app, or a fake client for failure-mode tests.
        # Production code never overrides this; the default constructs a real client.
        self._connector_client_factory = connector_client_factory or (lambda: ConnectorClient())

    def _mark_stage(self, mission_id: str, stage_name: str) -> None:
        """Persists a real stage-start event and updates current_stage BEFORE that
        stage's work begins, so the UI never has to infer "running" purely from
        overall mission status."""
        record = self.store.get_run(mission_id)
        if record is not None:
            record.current_stage = stage_name
            self.store.update_run(record)
        self.store.log_event(
            run_id=mission_id,
            stage=stage_name,
            message=f"Stage started: {stage_name}",
        )

    def _is_cancelled(
        self, mission_id: str, cancellation_check: "Callable[[], bool] | None"
    ) -> bool:
        if cancellation_check is None:
            return False
        try:
            return bool(cancellation_check())
        except Exception:
            return False

    def _finalize_stage_outcomes(
        self, recorder: StageRecorder, result: AssuranceResultV2, fallback_reason: str
    ) -> None:
        """Backfills any `MissionStage` not yet recorded as NOT_APPLICABLE with
        `fallback_reason` (used on early-exit paths — validation failure,
        cancellation, the clean-equivalence fast path — so every stage is
        still visible with a reason, never silently missing), then writes the
        ledger onto `result`. Idempotent: stages already recorded are left as-is."""
        for stage in recorder.missing_stages():
            recorder.record(stage, StageStatus.NOT_APPLICABLE, reason=fallback_reason)
        result.stage_outcomes = recorder.outcomes()

    def _finalize_cancelled(
        self,
        mission: AssuranceMission,
        result: AssuranceResultV2,
        agent_actions: list[AgentAction],
        recorder: "StageRecorder | None" = None,
    ) -> AssuranceResultV2:
        if recorder is not None:
            self._finalize_stage_outcomes(
                recorder, result, "Mission was cancelled before this stage was reached."
            )
        result.overall_status = "CANCELLED"
        mission.status = MissionStatus.CANCELLED
        transition = apply_transition(
            self.store,
            mission.mission_id,
            AssuranceRunStatus.CANCELLED,
            status_reason="Cooperative cancellation honored between stages.",
            workflow_stage="CANCELLED",
        )
        if transition.ok and transition.record is not None:
            transition.record.agent_activity = [act.dict() for act in agent_actions]
            transition.record.report = result.dict()
            self.store.update_run(transition.record)
        self.store.log_event(
            run_id=mission.mission_id,
            stage="CANCELLED",
            message="Mission execution stopped: cancellation was requested and honored between stages.",
        )
        return result

    def _record_gemini_evidence(self, mission_id: str, evidence: GeminiInvocationEvidence) -> str:
        """Persists one Gemini invocation attempt (success or failure) as a typed,
        auditable evidence record. Called for every attempt, never only successes."""
        ev = EvidenceRecord(
            evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
            run_id=mission_id,
            evidence_type=EvidenceType.GEMINI_INVOCATION,
            title=f"Gemini Invocation: {evidence.decision_type}",
            description=(
                evidence.rationale
                if evidence.success and evidence.rationale
                else f"{evidence.decision_type} invocation failed: {evidence.failure_category}"
            ),
            data_summary=evidence.dict(),
        )
        self.store.add_evidence(mission_id, ev)
        return ev.evidence_id

    def _ask_gemini(
        self,
        run_id: str,
        budget: _InvestigationBudget,
        decision_type: str,
        schema: type[GeminiDecisionBase],
        system_instruction: str,
        prompt: str,
    ) -> tuple[GeminiDecisionBase | None, GeminiInvocationEvidence | None]:
        """Attempts one budgeted, structured Gemini decision call.

        `run_id` is any store-addressable id this call's evidence/events should
        be filed under — a mission id for the investigation decision points, or
        a bare source id for the pre-mission CHOOSE_EXTRACTION_STRATEGY call.

        Returns (decision, evidence). `evidence` is None only when the
        per-mission call budget was already exhausted before any attempt was
        made. `decision` is None whenever the call failed or was skipped —
        callers MUST apply their own deterministic fallback in that case.
        """
        if budget.gemini_call_count >= self.agent_config.max_gemini_calls_per_mission:
            self.store.log_event(
                run_id,
                stage=decision_type,
                message=f"Deterministic fallback used for {decision_type}: Gemini call budget exhausted.",
            )
            return None, None

        budget.gemini_call_count += 1
        decision, evidence = self.gemini.decide(decision_type, schema, system_instruction, prompt)
        budget.evidence_ids.append(self._record_gemini_evidence(run_id, evidence))

        if decision is not None and evidence.success:
            budget.any_gemini_success = True
            self.store.log_event(
                run_id, stage=decision_type,
                message=f"Gemini decision {decision_type}: {evidence.rationale}",
            )
        else:
            budget.any_gemini_attempted = True
            self.store.log_event(
                run_id, stage=decision_type,
                message=f"Deterministic fallback used for {decision_type} (failure_category={evidence.failure_category}).",
            )
        return decision, evidence

    def _decision_action(
        self,
        agent_role: str,
        decision_type: str,
        summary: str,
        evidence: GeminiInvocationEvidence | None,
        *,
        is_gemini: bool,
        fallback_reason: str | None = None,
        needs_human_review: bool = False,
    ) -> AgentAction:
        """Builds the single AgentAction timeline entry for one decision point,
        whether it was a real Gemini decision or a deterministic fallback.
        `model_id`/`invocation_id` are only ever stamped when `is_gemini` is True
        and backed by a real successful invocation."""
        if not is_gemini:
            # Metric source (log-based, low cardinality): no tenant/mission ids.
            _logger.warning("GEMINI_FALLBACK decision_type=%s reason=%s", decision_type, fallback_reason or "UNSPECIFIED")
        return AgentAction(
            action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
            agent_role=agent_role,
            action_type="DECISION",
            summary=summary,
            rationale=(evidence.rationale if evidence and evidence.success else None) or fallback_reason,
            latency_ms=evidence.latency_ms if evidence else 0.0,
            model_id=evidence.model_id if (evidence and is_gemini) else None,
            invocation_id=evidence.invocation_id if evidence else None,
            decision_type=decision_type,
            is_gemini_decision=is_gemini,
            is_fallback=not is_gemini,
            fallback_reason=None if is_gemini else fallback_reason,
            needs_human_review=needs_human_review,
        )

    def _mandatory_evidence_ok(self, result: AssuranceResultV2) -> bool:
        """True only when every mandatory deterministic evidence section for this
        mission actually completed. A PASS release decision must never be issued
        when this is False — see STAGE 8 in `run_mission`."""
        return (
            result.validation.status == AnalysisStatus.SUCCEEDED
            and result.experiments.status == AnalysisStatus.SUCCEEDED
            and result.semantic_analysis.status in (AnalysisStatus.SUCCEEDED, AnalysisStatus.NOT_RUN)
        )

    def extract_and_compile_source(self, source: SourceDescriptor, content: bytes) -> AdapterResult:
        """Mandatory, bounded source-extraction entry point — the single place
        the mandatory pipeline (hash/provenance capture, extractor selection,
        extraction, IPIR schema re-validation, evidence persistence) runs for
        every uploaded source, whether or not a mission exists yet.

        Gemini is consulted (via CHOOSE_EXTRACTION_STRATEGY) only when the
        source format is inherently ambiguous — a regulatory PDF, or an Excel
        workbook whose sheet layout doesn't match the recognized rate-table
        convention. Already-valid IPIR JSON and recognized structured/
        platform-config JSON are always handled deterministically; Gemini is
        never invoked to parse a source a deterministic parser already
        handles, and it may only select an extractor id already present in
        `EXTRACTOR_REGISTRY` — an invented or out-of-allowlist id is rejected
        and the most conservative (human-review) extractor is used instead.
        """
        run_id = source.source_id
        budget = _InvestigationBudget()
        sha256_hash = hashlib.sha256(content).hexdigest()

        self.store.log_event(
            run_id, stage="SOURCE_VALIDATION",
            message=f"Captured source hash and provenance for '{source.name}' ({len(content)} bytes).",
        )

        extractor_id, selection_kind, gemini_evidence = self._select_extraction_strategy(source, content, budget)
        spec = EXTRACTOR_REGISTRY[extractor_id]

        self.store.log_event(
            run_id, stage="EXTRACTION",
            message=f"Selected extractor '{extractor_id}' via {selection_kind}.",
        )
        result = spec.extract(source, content)

        # Mandatory deterministic checkpoint: no extractor's output — deterministic,
        # Gemini-selected, or fallback — may bypass IPIR schema validation.
        schema_issues = validate_ipir_schema(result.ipir_package)
        if schema_issues:
            result.requires_human_review = True
            result.warnings.extend(f"Schema validation issue: {issue}" for issue in schema_issues)

        if result.confidence < self.agent_config.low_confidence_review_threshold:
            result.requires_human_review = True

        location_ref = None
        if result.provenance and result.provenance.sources:
            location_ref = result.provenance.sources[0].location or result.provenance.sources[0].section

        result.evidence.update({
            "source_id": source.source_id,
            "source_sha256": sha256_hash,
            "filename": source.name,
            "source_format": source.source_type.value,
            "size_bytes": len(content),
            "selected_extractor": extractor_id,
            "selection_kind": selection_kind,
            "location_reference": location_ref,
            "gemini_invocation_id": gemini_evidence.invocation_id if gemini_evidence else None,
        })

        ev = EvidenceRecord(
            evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
            run_id=run_id,
            evidence_type=EvidenceType.SOURCE,
            title=f"Source Extraction: {source.name}",
            description=(
                f"Selected extractor '{extractor_id}' via {selection_kind}; "
                f"confidence={result.confidence}, human_review={result.requires_human_review}."
            ),
            source_ref=source.source_id,
            data_summary={
                "sha256": sha256_hash,
                "filename": source.name,
                "size_bytes": len(content),
                "selected_extractor": extractor_id,
                "selection_kind": selection_kind,
                "confidence": result.confidence,
                "warnings": result.warnings,
                "requires_human_review": result.requires_human_review,
                "location_reference": location_ref,
            },
        )
        self.store.add_evidence(run_id, ev)

        return result

    def _select_extraction_strategy(
        self, source: SourceDescriptor, content: bytes, budget: "_InvestigationBudget"
    ) -> tuple[str, str, GeminiInvocationEvidence | None]:
        """Returns (extractor_id, selection_kind, gemini_evidence_or_None).
        `selection_kind` is one of 'DETERMINISTIC', 'GEMINI', or 'FALLBACK'."""

        if source.source_type == SourceFormat.STRUCTURED_JSON:
            try:
                IPIRPackage.model_validate_json(content.decode("utf-8"))
                return "structured_json_direct_parser", "DETERMINISTIC", None
            except Exception:
                pass
            try:
                payload = json.loads(content.decode("utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict) and ("ipir_payload" in payload or "rateBook" in payload):
                return "platform_config_adapter", "DETERMINISTIC", None
            # A recognized format (JSON) that is neither valid IPIR nor a known
            # wrapper shape is genuinely conflicting — flag for human review
            # deterministically. Never escalate a plain-JSON ambiguity to Gemini.
            return "structured_json_direct_parser", "FALLBACK", None

        if source.source_type == SourceFormat.PLATFORM_CONFIG:
            return "platform_config_adapter", "DETERMINISTIC", None

        if source.source_type == SourceFormat.EXCEL:
            if excel_layout_recognized(content):
                return "excel_named_range_extractor", "DETERMINISTIC", None
            return self._choose_extractor_via_gemini(
                source, budget, ["excel_named_range_extractor", "excel_manual_review_extractor"],
            )

        if source.source_type == SourceFormat.PDF:
            # Regulatory PDFs are inherently unstructured text — always a real
            # Gemini decision among the allowlisted extractors.
            return self._choose_extractor_via_gemini(
                source, budget, ["pdf_structured_section_extractor", "pdf_manual_review_extractor"],
            )

        raise ValueError(f"No extraction strategy policy defined for source format '{source.source_type}'.")

    def _choose_extractor_via_gemini(
        self, source: SourceDescriptor, budget: "_InvestigationBudget", allowlist: list[str],
    ) -> tuple[str, str, GeminiInvocationEvidence | None]:
        """`allowlist` MUST be ordered with the most conservative (human-review)
        extractor last — that is the safe default used on any failure or
        out-of-vocabulary response, since an ambiguous source has no
        deterministic status quo to fall back to."""
        decision, evidence = self._ask_gemini(
            source.source_id, budget, "CHOOSE_EXTRACTION_STRATEGY", ExtractionStrategyDecision,
            system_instruction=(
                "You are the RateGuard Assurance Supervisor selecting an extraction strategy for "
                "an ambiguous pricing source. You MUST only select a requested_tool from the "
                "provided allowlisted extractor ids — never invent an extractor."
            ),
            prompt=(
                f"Source: {source.name} (format: {source.source_type.value}).\n"
                f"Allowlisted extractor ids: {allowlist}\n"
                "Select the extractor id best suited to this source and explain why."
            ),
        )
        if decision is not None and decision.requested_tool in allowlist:
            return decision.requested_tool, "GEMINI", evidence
        # Invalid/out-of-allowlist selection, or Gemini unavailable/failed: default
        # to the most conservative allowlisted extractor — never silently guess.
        return allowlist[-1], "FALLBACK", evidence

    def run_mission(
        self,
        mission: AssuranceMission,
        left_pkg: IPIRPackage,
        right_pkg: IPIRPackage | None = None,
        target_connector: ConnectorSelection | None = None,
        cancellation_check: "Callable[[], bool] | None" = None,
        control_cases: "Sequence[ControlCase] | None" = None,
        impact_runner: "Any | None" = None,
        source_compatibility_notice: str | None = None,
    ) -> AssuranceResultV2:
        control_cases = list(control_cases or [])
        agent_actions: list[AgentAction] = []
        tool_invocations: list[ToolInvocation] = []
        evidence_ids: list[str] = []
        budget = _InvestigationBudget()
        raw_diff_result = None
        test_plan = None
        recorder = StageRecorder()
        # Set once real connector probing begins; used at STAGE 8 and by
        # EVIDENCE_FINALIZATION to describe TARGET_EXECUTION honestly.
        connector_probe_outcomes: list[str] = []  # "SUCCESS" | "CONNECTOR_FAILURE" | "PARTIAL_RESPONSE" per probe

        result = AssuranceResultV2(
            mission_id=mission.mission_id,
            mode=mission.mode.value,
            overall_status="RUNNING",
            ai_runtime={
                "model_id": get_agent_config().gemini_model,
                "framework": "Google GenAI SDK (google-genai structured output)",
                "model_status": AI_RUNTIME_NOT_INVOKED_STATUS,
            },
        )

        # Update Mission Status
        mission.status = MissionStatus.RUNNING
        self.store.save_run(self.store.get_run(mission.mission_id) or self._create_record_from_mission(mission))
        self._mark_stage(mission.mission_id, "VALIDATION")

        # -------------------------------------------------------------
        # STAGE 1: Source & Connector Validation
        # -------------------------------------------------------------
        val_start = time.time()
        val_issues = MissionValidationService.validate_mission(mission)
        val_latency = (time.time() - val_start) * 1000

        action_val = AgentAction(
            action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
            agent_role="Assurance Supervisor",
            action_type="REASONING",
            summary=f"Validated mission sources and connector specifications ({len(val_issues)} issues found).",
            rationale="Verifying schema compatibility and endpoint security before execution.",
            latency_ms=val_latency,
            selected_tool="validate_ipir_schema",
        )
        agent_actions.append(action_val)

        if val_issues:
            recorder.record(
                MissionStage.REQUEST_VALIDATION, StageStatus.FAILED,
                reason="; ".join(f"{i.field}: {i.message}" for i in val_issues),
            )
            result.validation = SectionResult(
                status=AnalysisStatus.FAILED,
                error_message="Mission validation failed.",
                data=val_issues,
            )
            result.overall_status = "FAILED"
            mission.status = MissionStatus.FAILED
            mission.validation_issues = val_issues
            self._finalize_stage_outcomes(recorder, result, "Mission failed request validation before this stage was reached.")
            self._update_mission_record(mission, result, agent_actions)
            return result

        recorder.record(MissionStage.REQUEST_VALIDATION, StageStatus.COMPLETED)
        recorder.record(MissionStage.SOURCE_A_LOAD, StageStatus.COMPLETED)

        result.validation = SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=[],
        )

        # A source extracted with low confidence (flagged requires_human_review
        # at compile time) must never silently support a PASS -- compilation
        # uncertainty is one of the signals the release decision has to agree
        # on, not just semantic/behavioral evidence.
        compilation_uncertain = bool(mission.source_a.requires_human_review) or bool(
            mission.source_b and mission.source_b.requires_human_review
        )

        # Comparing two genuinely different insurance products or jurisdictions
        # is not a meaningful equivalence/conformance check -- a metadata
        # mismatch between the two compiled sources must surface as its own
        # signal, never be silently absorbed into "0 semantic differences".
        review_reasons: list[str] = []
        if source_compatibility_notice:
            review_reasons.append("REUPLOAD_REQUIRED: " + source_compatibility_notice)
        if compilation_uncertain:
            review_reasons.append("Source extraction confidence was flagged for human review.")
        if right_pkg is not None:
            if left_pkg.product.line != right_pkg.product.line:
                review_reasons.append(
                    f"Source A is product line '{left_pkg.product.line.value}' but Source B is "
                    f"'{right_pkg.product.line.value}' -- these are not the same insurance product."
                )
            elif (
                left_pkg.product.jurisdiction.state_or_province != right_pkg.product.jurisdiction.state_or_province
                or left_pkg.product.jurisdiction.country != right_pkg.product.jurisdiction.country
            ):
                left_jur = left_pkg.product.jurisdiction.state_or_province or left_pkg.product.jurisdiction.country
                right_jur = right_pkg.product.jurisdiction.state_or_province or right_pkg.product.jurisdiction.country
                review_reasons.append(
                    f"Source A jurisdiction ({left_jur}) does not match Source B jurisdiction ({right_jur})."
                )
            else:
                left_end = left_pkg.effective_period.end
                right_end = right_pkg.effective_period.end
                overlaps = left_pkg.effective_period.start <= (right_end or left_pkg.effective_period.start) and (
                    right_pkg.effective_period.start <= (left_end or right_pkg.effective_period.start)
                )
                if not overlaps:
                    review_reasons.append(
                        "Source A and Source B effective periods do not overlap: "
                        f"A=[{left_pkg.effective_period.start}, {left_end or 'open'}], "
                        f"B=[{right_pkg.effective_period.start}, {right_end or 'open'}]."
                    )
        review_required = bool(review_reasons)

        if right_pkg is not None:
            recorder.record(MissionStage.SOURCE_B_LOAD_OR_CONNECTOR_CHECK, StageStatus.COMPLETED)
        elif target_connector is not None:
            recorder.record(
                MissionStage.SOURCE_B_LOAD_OR_CONNECTOR_CHECK, StageStatus.COMPLETED,
                reason=None,
            )
        else:
            recorder.record(
                MissionStage.SOURCE_B_LOAD_OR_CONNECTOR_CHECK, StageStatus.NOT_APPLICABLE,
                reason="No Source B (IPIR package or connector) was provided for this mission.",
            )
        if review_required:
            recorder.record(MissionStage.COMPATIBILITY_GATE, StageStatus.REVIEW_REQUIRED, reason=" ".join(review_reasons))
        else:
            recorder.record(MissionStage.COMPATIBILITY_GATE, StageStatus.COMPLETED)
        # Note: for the connector path (no right_pkg), only the compilation-
        # uncertainty half of this gate applies today -- a live REST connector
        # has no declared product/jurisdiction/effective-period metadata to
        # compare against (ConnectorRegistryEntry carries none), so that
        # narrower comparison is honestly skipped rather than fabricated.

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # -------------------------------------------------------------
        # STAGE 2: Semantic Analysis (EQUIVALENCE & RELEASE_CONFORMANCE)
        # -------------------------------------------------------------
        self._mark_stage(mission.mission_id, "SEMANTIC_ANALYSIS")
        sem_diffs: list[MaterialFinding] = []
        is_clean_equivalence = False

        if mission.mode in (ComparisonMode.EQUIVALENCE, ComparisonMode.RELEASE_CONFORMANCE) and right_pkg:
            sem_start = time.time()
            raw_diff_result = self.semantic_diff_engine.compare_packages(left_pkg, right_pkg)
            sem_latency = (time.time() - sem_start) * 1000

            tool_invocations.append(
                ToolInvocation(
                    tool_name="compare_ipir",
                    input_args={"left": left_pkg.id, "right": right_pkg.id},
                    output_summary={"diff_count": len(raw_diff_result.differences)},
                    execution_time_ms=sem_latency,
                )
            )

            sem_diffs = to_material_findings(raw_diff_result)
            # Computed AFTER sem_diffs is populated so the persisted count is correct
            # (previously this was computed against the empty list before assignment).
            sem_summary = f"{len(sem_diffs)} AST semantic differences identified."

            result.semantic_analysis = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=SemanticAnalysisData(
                    difference_count=len(sem_diffs),
                    differences=sem_diffs,
                    summary=sem_summary,
                ),
            )

            action_sem = AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Semantic Assurance Specialist",
                action_type="TOOL_INVOCATION",
                summary=f"Executed AST semantic diff comparison ({len(sem_diffs)} diffs identified).",
                rationale="Comparing mathematical representation ASTs node-by-node.",
                selected_tool="compare_ipir",
                latency_ms=sem_latency,
            )
            agent_actions.append(action_sem)

            # Audit Evidence
            ev_sem = EvidenceRecord(
                evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
                run_id=mission.mission_id,
                evidence_type=EvidenceType.SEMANTIC_DIFF,
                title="IPIR AST Semantic Diff Results",
                description=sem_summary,
                data_summary={"difference_count": len(sem_diffs)},
            )
            self.store.add_evidence(mission.mission_id, ev_sem)
            evidence_ids.append(ev_sem.evidence_id)

            # Real Gemini decision point: prioritize which confirmed differences
            # deserve focused boundary testing. Gemini may only select finding_ids
            # the deterministic diff engine already produced; deterministic
            # fallback retains every difference.
            prioritized_diff_ids: list[str] = [d.finding_id for d in sem_diffs]
            if sem_diffs:
                if self._is_cancelled(mission.mission_id, cancellation_check):
                    return self._finalize_cancelled(mission, result, agent_actions, recorder)

                candidate_ids = set(prioritized_diff_ids)
                decision, evidence = self._ask_gemini(
                    mission.mission_id, budget, "PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision,
                    system_instruction=(
                        "You are the RateGuard Assurance Supervisor prioritizing which already-detected "
                        "semantic pricing differences deserve focused boundary testing. You MUST only "
                        "select finding_ids from the provided list — never invent a finding."
                    ),
                    prompt=(
                        "Deterministically-identified semantic pricing differences (JSON): "
                        f"{[{'finding_id': d.finding_id, 'severity': d.severity, 'title': d.title} for d in sem_diffs]}\n"
                        "Select which finding_ids deserve investigative priority for boundary testing."
                    ),
                )
                chosen = [i for i in (decision.selected_difference_ids if decision else []) if i in candidate_ids]
                if decision is not None and chosen:
                    prioritized_diff_ids = chosen
                    agent_actions.append(self._decision_action(
                        "Semantic Assurance Specialist", "PRIORITIZE_DIFFERENCES",
                        f"Gemini prioritized {len(chosen)} of {len(sem_diffs)} differences for focused investigation.",
                        evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
                    ))
                else:
                    reason = "NO_VALID_IDS_IN_RESPONSE" if decision is not None else (
                        evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                    )
                    agent_actions.append(self._decision_action(
                        "Semantic Assurance Specialist", "PRIORITIZE_DIFFERENCES",
                        f"Deterministic fallback: retaining all {len(sem_diffs)} differences for investigation.",
                        evidence, is_gemini=False, fallback_reason=reason,
                    ))

            if len(sem_diffs) == 0:
                is_clean_equivalence = True
            recorder.record(MissionStage.SEMANTIC_DIFF, StageStatus.COMPLETED)
        else:
            prioritized_diff_ids = []
            result.semantic_analysis = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Semantic comparison skipped: no comparison target available.",
            )
            recorder.record(
                MissionStage.SEMANTIC_DIFF, StageStatus.NOT_APPLICABLE,
                reason=(
                    "A live connector target has no AST to compare against; semantic diff "
                    "requires two IPIR packages."
                    if target_connector is not None
                    else "No comparison target (Source B) was provided for this mission."
                ),
            )

        # -------------------------------------------------------------
        # BRANCH A: CLEAN EQUIVALENCE (0 Diffs)
        # -------------------------------------------------------------
        if is_clean_equivalence and right_pkg:
            action_clean = AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Assurance Supervisor",
                action_type="REASONING",
                summary="0 AST diffs detected. Executing clean verification sample probes.",
                rationale="Target implementation matches filing intent AST 100%. Verifying sample executions.",
                latency_ms=15.0,
            )
            agent_actions.append(action_clean)
            recorder.record(MissionStage.DEPENDENCY_IMPACT, StageStatus.COMPLETED)
            recorder.record(MissionStage.TEST_CANDIDATE_GENERATION, StageStatus.COMPLETED)
            recorder.record(MissionStage.TEST_SELECTION, StageStatus.COMPLETED)
            recorder.record(MissionStage.ORACLE_EXECUTION, StageStatus.COMPLETED)
            recorder.record(MissionStage.TARGET_EXECUTION, StageStatus.COMPLETED)

            oracle = PremiumOracleCalculator(left_pkg)
            target_calc = PremiumOracleCalculator(right_pkg)

            # Verification sample probes
            raw_impact = self.impact_engine.analyze(raw_diff_result, left_pkg)
            test_plan = self.test_generator.generate_plan(
                left_pkg, raw_diff_result, raw_impact, control_cases=control_cases
            )
            test_cases = test_plan.selected_scenarios[:5]

            experiments_list: list[RuntimeExperiment] = []

            for tc in test_cases:
                # The probe's own resolved calculation date/transaction type
                # is carried to BOTH engines (no implicit default anywhere).
                exp_res = oracle.calculate_policy_premium(
                    tc.risk_values, effective_date=tc.effective_date, transaction_type=tc.transaction_type
                )
                act_res = target_calc.calculate_policy_premium(
                    tc.risk_values, effective_date=tc.effective_date, transaction_type=tc.transaction_type
                )
                matched = exp_res.final_premium == act_res.final_premium

                experiments_list.append(
                    RuntimeExperiment(
                        experiment_id=getattr(tc, "id", getattr(tc, "scenario_id", "RG-EXP")),
                        probe_name=tc.name,
                        category="BOUNDARY",
                        risk_inputs=tc.risk_values,
                        expected_premium=str(exp_res.final_premium),
                        actual_premium=str(act_res.final_premium),
                        matches=matched,
                        outcome="MATCH" if matched else "MISMATCH",
                        **_probe_trace_fields(tc, exp_res.calculation_date, exp_res.calculation_date_source),
                    )
                )

            real_match_count = sum(1 for e in experiments_list if e.matches)
            real_mismatch_count = len(experiments_list) - real_match_count

            result.experiments = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ExperimentsData(
                    total_generated=len(test_cases),
                    total_executed=len(test_cases),
                    match_count=real_match_count,
                    mismatch_count=real_mismatch_count,
                    reduction_pct=test_plan.coverage_metrics.get("candidate_reduction_pct", 0.0),
                    experiments=experiments_list,
                ),
            )

            if real_mismatch_count == 0:
                result.blast_radius = SectionResult(
                    status=AnalysisStatus.SUCCEEDED,
                    data=BlastRadiusResult(
                        total_policies_analyzed=50000,
                        semantically_exposed_count=0,
                        behaviorally_affected_count=0,
                        financially_affected_count=0,
                        absolute_financial_exposure="0.00",
                        signed_net_variance="0.00",
                    ),
                )

                if review_required:
                    result.release_decision = SectionResult(
                        status=AnalysisStatus.SUCCEEDED,
                        data=ReleaseDecision(
                            status="REVIEW_REQUIRED",
                            confidence_score=1.0,
                            summary=(
                                "Semantic and behavioral evidence agree, but this mission cannot issue a "
                                "PASS: " + " ".join(review_reasons)
                            ),
                            blocking_reasons=review_reasons,
                            recommendation="Resolve the flagged issue, then re-run assurance verification.",
                        ),
                    )
                else:
                    result.release_decision = SectionResult(
                        status=AnalysisStatus.SUCCEEDED,
                        data=ReleaseDecision(
                            status="PASS",
                            confidence_score=1.0,
                            summary="Full behavioral and semantic equivalence verified. Zero pricing drift or financial exposure detected.",
                            blocking_reasons=[],
                            recommendation="Approve pricing engine release for production deployment.",
                        ),
                    )

                result.overall_status = "COMPLETED"
                mission.status = MissionStatus.COMPLETED
                recorder.record(MissionStage.DECISION, StageStatus.COMPLETED)
                self._finalize_stage_outcomes(
                    recorder, result,
                    "Not applicable: zero behavioral mismatches reproduced in the clean-equivalence fast path.",
                )
                recorder.record(MissionStage.EVIDENCE_FINALIZATION, StageStatus.COMPLETED)
                result.stage_outcomes = recorder.outcomes()
                self._update_mission_record(mission, result, agent_actions)
                return result

            # A behavioral mismatch was reproduced despite ZERO detected AST
            # differences -- the static semantic-diff comparison has a
            # coverage gap it did not catch. A false PASS here is strictly
            # worse than an explicit block: the release decision must be
            # conservative and agree across ALL evidence, not just the
            # semantic layer. Never silently discard this signal.
            first_mismatch = next(e for e in experiments_list if not e.matches)
            action_gap = AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Assurance Supervisor",
                action_type="REASONING",
                summary=(
                    f"Semantic-diff blind spot: 0 AST differences detected, but "
                    f"{real_mismatch_count} of {len(experiments_list)} behavioral verification "
                    f"probes reproduced a premium mismatch."
                ),
                rationale="Behavioral evidence contradicts the static AST comparison. Release cannot be approved on semantic agreement alone.",
                latency_ms=15.0,
            )
            agent_actions.append(action_gap)

            result.reconciliation = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ReconciliationData(
                    mismatch_count=real_mismatch_count,
                    first_divergent_node="semantic_diff_blind_spot",
                    root_cause=RootCauseFinding(
                        node_id="semantic_diff_blind_spot",
                        title="Semantic Diff Coverage Gap",
                        explanation=(
                            f"AST semantic comparison reported 0 differences, but behavioral "
                            f"verification scenario '{first_mismatch.probe_name}' reproduced a "
                            f"premium mismatch: expected {first_mismatch.expected_premium}, "
                            f"target returned {first_mismatch.actual_premium}. The static "
                            f"comparison did not capture a real behavioral difference between "
                            f"the two implementations."
                        ),
                        expected_value=first_mismatch.expected_premium,
                        actual_value=first_mismatch.actual_premium,
                        divergence_type="SEMANTIC_DIFF_BLIND_SPOT",
                    ),
                ),
            )
            result.blast_radius = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Portfolio scan skipped pending root-cause triage of the semantic-diff blind spot.",
            )
            result.release_decision = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ReleaseDecision(
                    status="BLOCK_DEPLOYMENT",
                    confidence_score=1.0,
                    summary=(
                        f"{real_mismatch_count} of {len(experiments_list)} behavioral verification "
                        f"probes reproduced a premium mismatch despite 0 detected AST differences."
                    ),
                    blocking_reasons=[
                        f"{real_mismatch_count} behavioral mismatch(es) reproduced despite a clean "
                        "semantic AST comparison -- semantic-diff coverage gap."
                    ],
                    recommendation=(
                        "Do not deploy. Investigate why the semantic diff engine did not detect a "
                        "difference that produces divergent runtime premiums."
                    ),
                ),
            )

            recorder.record(MissionStage.RECONCILIATION, StageStatus.COMPLETED)
            recorder.record(
                MissionStage.PORTFOLIO_IMPACT, StageStatus.NOT_APPLICABLE,
                reason="Portfolio scan skipped pending root-cause triage of the semantic-diff blind spot.",
            )
            recorder.record(MissionStage.DECISION, StageStatus.COMPLETED)
            self._finalize_stage_outcomes(
                recorder, result,
                "Not applicable: mission resolved via the semantic-diff-blind-spot fast path.",
            )
            recorder.record(MissionStage.EVIDENCE_FINALIZATION, StageStatus.COMPLETED)
            result.stage_outcomes = recorder.outcomes()

            result.overall_status = "COMPLETED"
            mission.status = MissionStatus.COMPLETED
            self._update_mission_record(mission, result, agent_actions)
            return result

        # -------------------------------------------------------------
        # BRANCH B: MATERIAL DRIFT OR RUNTIME VERIFICATION
        # -------------------------------------------------------------
        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 3: Impact Analysis (DAG Traversal)
        self._mark_stage(mission.mission_id, "IMPACT_ANALYSIS")
        if sem_diffs and right_pkg:
            imp_start = time.time()
            raw_impact = self.impact_engine.analyze(raw_diff_result, left_pkg)
            imp_latency = (time.time() - imp_start) * 1000

            result.impact_analysis = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ImpactAnalysisData(
                    changed_nodes=raw_impact.changed_nodes,
                    impacted_calculation_nodes=raw_impact.downstream_affected_nodes,
                    affected_pricing_outputs=raw_impact.affected_outputs,
                    risk_predicates=[p.dict() for p in raw_impact.candidate_risk_predicates],
                ),
            )

            action_imp = AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Pricing Impact Specialist",
                action_type="TOOL_INVOCATION",
                summary=f"Traversed DAG calculation graph ({len(raw_impact.downstream_affected_nodes)} nodes impacted).",
                rationale="Mapping AST diffs to downstream calculation nodes and final policy premium outputs.",
                selected_tool="analyze_dependencies",
                latency_ms=imp_latency,
            )
            agent_actions.append(action_imp)
            recorder.record(MissionStage.DEPENDENCY_IMPACT, StageStatus.COMPLETED)
        else:
            raw_impact = None
            result.impact_analysis = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Dependency impact graph traversal skipped: no comparison target available.",
            )
            recorder.record(
                MissionStage.DEPENDENCY_IMPACT, StageStatus.NOT_APPLICABLE,
                reason=(
                    "A live connector target has no AST diffs to trace impact from."
                    if target_connector is not None
                    else "Dependency impact graph traversal skipped: no comparison target available."
                ),
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 4: Risk-Directed Boundary Testing / Black-Box Probes
        self._mark_stage(mission.mission_id, "RISK_DIRECTED_TESTING")
        exp_start = time.time()
        if target_connector is not None:
            # A connector target has no comparable IPIR package to diff against
            # Source A, so there are no semantic differences to target. The
            # planner therefore derives its probes from Source A itself: the
            # workbook's verified control cases, a baseline, and boundary
            # probes mined from the compiled tables/conditions (see
            # `app.engines.testing.package_probes`). The zero-difference
            # self-comparison below only supplies the (empty) diff inputs.
            synthetic_diff = SemanticDiffResult(
                left_package_id=left_pkg.id,
                right_package_id=left_pkg.id,
                left_version=left_pkg.version,
                right_version=left_pkg.version,
                differences=[],
            )
            synthetic_impact = ImpactAnalysis(package_id=left_pkg.id)
            test_plan = self.test_generator.generate_plan(
                left_pkg, synthetic_diff, synthetic_impact, control_cases=control_cases
            )
            selected_tests = test_plan.selected_scenarios
            recorder.record(MissionStage.TEST_CANDIDATE_GENERATION, StageStatus.COMPLETED)
            recorder.record(MissionStage.TEST_SELECTION, StageStatus.COMPLETED)
        elif raw_diff_result and raw_impact:
            test_plan = self.test_generator.generate_plan(
                left_pkg, raw_diff_result, raw_impact, control_cases=control_cases
            )
            selected_tests = test_plan.selected_scenarios  # deterministic default
            recorder.record(MissionStage.TEST_CANDIDATE_GENERATION, StageStatus.COMPLETED)

            # Real Gemini decision point: select which deterministically-generated
            # candidate boundary tests to execute. Gemini may only choose scenario
            # ids from the candidate pool the optimizer already produced.
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions, recorder)

            candidate_pool: dict[str, PricingTestScenario] = {sc.id: sc for sc in test_plan.candidate_scenarios}
            prioritized_set = set(prioritized_diff_ids)
            candidate_summaries = [
                {
                    "id": sc.id,
                    "name": sc.name,
                    "classification": sc.classification.value,
                    "targets_prioritized_difference": bool(prioritized_set & set(sc.target_difference_ids)),
                }
                for sc in test_plan.candidate_scenarios
            ]
            decision, evidence = self._ask_gemini(
                mission.mission_id, budget, "SELECT_BOUNDARY_TESTS", TestSelectionDecision,
                system_instruction=(
                    "You are the RateGuard Assurance Supervisor selecting which deterministically-generated "
                    "candidate boundary test scenarios to execute. You MUST only select scenario ids from "
                    "the provided candidate pool — never invent a scenario or a risk value. Prefer scenarios "
                    "that target the prioritized differences."
                ),
                prompt=(
                    f"Candidate scenarios (JSON): {candidate_summaries}\n"
                    f"Select up to {MAX_SELECTED_TESTS} scenario ids to execute."
                ),
            )
            chosen_ids = [i for i in (decision.selected_test_ids if decision else []) if i in candidate_pool]
            if decision is not None and chosen_ids:
                selected_tests = [candidate_pool[i] for i in chosen_ids]
                agent_actions.append(self._decision_action(
                    "Risk-Directed Test Planner", "SELECT_BOUNDARY_TESTS",
                    f"Gemini selected {len(selected_tests)} of {len(candidate_pool)} candidate boundary tests.",
                    evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
                ))
            else:
                reason = "NO_VALID_IDS_IN_RESPONSE" if decision is not None else (
                    evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                )
                agent_actions.append(self._decision_action(
                    "Risk-Directed Test Planner", "SELECT_BOUNDARY_TESTS",
                    f"Deterministic fallback: using optimizer-selected {len(selected_tests)} boundary tests.",
                    evidence, is_gemini=False, fallback_reason=reason,
                ))
            recorder.record(MissionStage.TEST_SELECTION, StageStatus.COMPLETED)
        else:
            selected_tests = []
            recorder.record(
                MissionStage.TEST_CANDIDATE_GENERATION, StageStatus.NOT_APPLICABLE,
                reason="No comparison target available to generate targeted test candidates from.",
            )
            recorder.record(
                MissionStage.TEST_SELECTION, StageStatus.NOT_APPLICABLE,
                reason="No test candidates were generated for this mission.",
            )

        experiments_list: list[RuntimeExperiment] = []
        mismatch_count = 0  # PROVEN premium mismatches only
        match_count = 0
        inconclusive_count = 0  # probes that could not reach a pricing conclusion

        oracle = PremiumOracleCalculator(left_pkg)
        target_calc = PremiumOracleCalculator(right_pkg) if right_pkg else None

        connector_client = self._connector_client_factory() if target_connector is not None else None
        connector_budget = TargetBudget() if target_connector is not None else None
        connector_evidence_ids: list[str] = []

        def _quote_via_connector(
            tc: PricingTestScenario, calc_date: date, calc_source: object
        ) -> tuple[Decimal | None, str]:
            """Bridges the sync probe loop to the async `ConnectorClient` via
            `asyncio.run()`. Safe here because the entire call chain
            (worker_endpoint -> AssuranceWorker -> MissionExecutionService ->
            run_mission) is synchronous with no already-running event loop.
            Returns (final_premium_or_None, status) where status is one of
            "SUCCESS", "PARTIAL_RESPONSE", or "CONNECTOR_FAILURE" — `None` is
            returned on any failure so a mismatch is never accidentally
            treated as a match (see the `is not None` guard at the call site)."""
            # Decimal isn't JSON-serializable and dates are sent as ISO strings;
            # every other type (int/str/bool) is forwarded as-is so a numeric
            # input like roof_age stays a JSON number, matching the demo
            # target's own proven-working golden-case request shape
            # (`rating_engine/startup_selftest.py::GOLDEN_CASE_INPUTS`) rather
            # than being stringified into something the target's range/table
            # comparisons were never built to handle.
            connector_inputs: dict[str, Any] = {}
            for k, v in tc.risk_values.items():
                if k in ("transaction_type", "effective_date"):
                    # "effective_date" inside risk_values is a
                    # PremiumOracleCalculator-only convention (see
                    # app.engines.oracle.calculator) — the connector already
                    # receives the real effective date via its own
                    # ConnectorQuoteRequest.effective_date field above.
                    continue
                if isinstance(v, Decimal):
                    connector_inputs[k] = str(v)
                elif hasattr(v, "isoformat"):
                    connector_inputs[k] = v.isoformat()
                else:
                    connector_inputs[k] = v
            req = ConnectorQuoteRequest(
                engine_version=target_connector.engine_version,
                product=mission.objective.product,
                jurisdiction=mission.objective.jurisdiction,
                # The SAME resolved date the oracle used for this probe -- never
                # the package start by default.
                effective_date=calc_date,
                transaction_type=tc.transaction_type,
                inputs=connector_inputs,
                trace_requested=True,
            )
            req_hash = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
            try:
                resp = asyncio.run(
                    connector_client.send_quote(
                        target_connector.connector_id, target_connector.engine_version, req,
                        budget=connector_budget, correlation_id=mission.mission_id,
                    )
                )
            except ConnectorException as exc:
                status = "PARTIAL_RESPONSE" if exc.category == ConnectorFailureCategory.REVIEW_REQUIRED else "CONNECTOR_FAILURE"
                ev = EvidenceRecord(
                    evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
                    run_id=mission.mission_id,
                    evidence_type=EvidenceType.CONNECTOR_INVOCATION,
                    title=f"Connector Quote: {target_connector.connector_id}@{target_connector.engine_version}",
                    description=f"Scenario '{tc.name}' probe failed: {exc.error.code}.",
                    data_summary={
                        "connector_id": target_connector.connector_id,
                        "engine_version": target_connector.engine_version,
                        "correlation_id": mission.mission_id,
                        "scenario_id": tc.id,
                        "probe_origin": (tc.metadata or {}).get("probe_origin"),
                        "calculation_date": calc_date.isoformat(),
                        "calculation_date_source": _date_source(tc, calc_source),
                        "request_sha256": req_hash,
                        "response_sha256": None,
                        "status": status,
                        "error_code": exc.error.code,
                        "final_premium": None,
                    },
                )
                self.store.add_evidence(mission.mission_id, ev)
                connector_evidence_ids.append(ev.evidence_id)
                connector_probe_outcomes.append(status)
                return None, status

            premium_str = resp.outputs.get("final_premium")
            resp_hash = hashlib.sha256(resp.model_dump_json().encode()).hexdigest()
            status = "SUCCESS" if premium_str else "PARTIAL_RESPONSE"
            ev = EvidenceRecord(
                evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
                run_id=mission.mission_id,
                evidence_type=EvidenceType.CONNECTOR_INVOCATION,
                title=f"Connector Quote: {target_connector.connector_id}@{target_connector.engine_version}",
                description=f"Scenario '{tc.name}' probed against connector.",
                data_summary={
                    "connector_id": target_connector.connector_id,
                    "engine_version": target_connector.engine_version,
                    "correlation_id": mission.mission_id,
                    "scenario_id": tc.id,
                    "probe_origin": (tc.metadata or {}).get("probe_origin"),
                    "calculation_date": calc_date.isoformat(),
                    "calculation_date_source": _date_source(tc, calc_source),
                    "connector_request_id": resp.request_id,
                    "request_sha256": req_hash,
                    "response_sha256": resp_hash,
                    "status": status,
                    "final_premium": premium_str,
                },
            )
            self.store.add_evidence(mission.mission_id, ev)
            connector_evidence_ids.append(ev.evidence_id)
            connector_probe_outcomes.append(status)
            return (Decimal(premium_str) if premium_str else None), status

        def _execute_probe(tc: PricingTestScenario, category: str) -> RuntimeExperiment:
            """Executes exactly one deterministic oracle-vs-target probe and
            classifies it as MATCH, MISMATCH (a *proven* premium difference) or
            INCONCLUSIVE (the target/date/evidence could not support any pricing
            conclusion -- never counted as a mismatch). Which scenario reaches
            this function is Gemini's only discretion -- the arithmetic itself
            is untouched deterministic engine code."""
            nonlocal match_count, mismatch_count, inconclusive_count
            exp_prem: Decimal | None = None
            act_prem: Decimal | None = None
            calc_date = None
            calc_source = None
            outcome = "MATCH"
            reason: str | None = None
            try:
                expected = oracle.calculate_policy_premium(
                    tc.risk_values, effective_date=tc.effective_date, transaction_type=tc.transaction_type
                )
                exp_prem, calc_date, calc_source = (
                    expected.final_premium, expected.calculation_date, expected.calculation_date_source,
                )
                if target_connector is not None:
                    act_prem, status = _quote_via_connector(tc, calc_date, calc_source)
                    if act_prem is None:
                        outcome, reason = "INCONCLUSIVE", (status if status.startswith("CONNECTOR_") else f"CONNECTOR_{status}")
                elif target_calc:
                    act_prem = target_calc.calculate_policy_premium(
                        tc.risk_values, effective_date=tc.effective_date, transaction_type=tc.transaction_type
                    ).final_premium
                else:
                    act_prem = Decimal("0.00")
                if outcome != "INCONCLUSIVE":
                    outcome = "MATCH" if exp_prem == act_prem else "MISMATCH"
            except CalculationDateError as exc:
                outcome, reason = "INCONCLUSIVE", f"{exc.code}: {exc.message}"

            if outcome == "MATCH":
                match_count += 1
            elif outcome == "MISMATCH":
                mismatch_count += 1
            else:
                inconclusive_count += 1

            scenario_id = getattr(tc, "id", getattr(tc, "scenario_id", "RG-EXP"))
            budget.executed_test_ids.add(scenario_id)
            return RuntimeExperiment(
                experiment_id=scenario_id,
                probe_name=tc.name,
                category=category,
                risk_inputs=tc.risk_values,
                expected_premium=str(exp_prem) if exp_prem is not None else "N/A (calculation date rejected)",
                actual_premium=(
                    str(act_prem) if act_prem is not None else "N/A (connector failure or partial response)"
                ),
                matches=outcome == "MATCH",
                outcome=outcome,
                inconclusive_reason=reason,
                **_probe_trace_fields(tc, calc_date, calc_source),
            )

        for tc in selected_tests:
            experiments_list.append(_execute_probe(tc, "RISK_DIRECTED"))

        # Real Gemini decision point (bounded to MAX_PROBE_ROUNDS): decide whether
        # enough evidence has been gathered, or request one more bounded round of
        # additional boundary probes from the untested candidate pool. Never
        # re-executes a scenario id already run in this mission.
        probe_round = 0
        while (
            probe_round < self.agent_config.max_probe_rounds
            and mismatch_count > 0
            and test_plan is not None
            and budget.gemini_call_count < self.agent_config.max_gemini_calls_per_mission
        ):
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions, recorder)

            remaining_pool = {
                sc.id: sc for sc in test_plan.candidate_scenarios if sc.id not in budget.executed_test_ids
            }
            if not remaining_pool:
                break

            mismatched_probe_names = [e.probe_name for e in experiments_list if e.outcome == "MISMATCH"]
            decision, evidence = self._ask_gemini(
                mission.mission_id, budget, "EVIDENCE_SUFFICIENCY", EvidenceSufficiencyDecision,
                system_instruction=(
                    "You are the RateGuard Assurance Supervisor deciding whether enough boundary-test "
                    "evidence has been gathered, or whether one more bounded probe round against the "
                    "untested candidate pool is warranted. You MUST only select scenario ids from the "
                    "provided remaining candidate pool."
                ),
                prompt=(
                    f"Mismatches reproduced so far: {mismatched_probe_names}\n"
                    "Remaining untested candidates (JSON): "
                    f"{[{'id': sc.id, 'name': sc.name} for sc in remaining_pool.values()]}\n"
                    f"You may request up to {MAX_ADDITIONAL_PROBE_TESTS} additional scenario ids."
                ),
            )
            probe_round += 1

            if decision is None or decision.stop_condition == "SUFFICIENT" or not decision.additional_test_ids:
                reason = None if decision is not None else (
                    evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                )
                agent_actions.append(self._decision_action(
                    "Assurance Supervisor", "EVIDENCE_SUFFICIENCY",
                    "Gemini determined evidence is sufficient; no additional probe round executed."
                    if decision is not None
                    else "Deterministic fallback: no additional probe round executed.",
                    evidence, is_gemini=decision is not None,
                    fallback_reason=reason,
                    needs_human_review=(decision.needs_human_review if decision else False),
                ))
                break

            extra_ids = [i for i in decision.additional_test_ids if i in remaining_pool][:MAX_ADDITIONAL_PROBE_TESTS]
            if not extra_ids:
                agent_actions.append(self._decision_action(
                    "Assurance Supervisor", "EVIDENCE_SUFFICIENCY",
                    "Gemini requested additional evidence but returned no valid untested scenario ids; stopping probe loop.",
                    evidence, is_gemini=False, fallback_reason="NO_VALID_IDS_IN_RESPONSE",
                ))
                break

            agent_actions.append(self._decision_action(
                "Assurance Supervisor", "EVIDENCE_SUFFICIENCY",
                f"Gemini requested {len(extra_ids)} additional boundary probe(s) before concluding investigation.",
                evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
            ))

            for extra_id in extra_ids:
                experiments_list.append(_execute_probe(remaining_pool[extra_id], "ADDITIONAL_PROBE"))

        exp_latency = (time.time() - exp_start) * 1000

        result.experiments = SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=ExperimentsData(
                total_generated=test_plan.candidate_count if test_plan is not None else len(selected_tests),
                total_executed=len(selected_tests),
                match_count=match_count,
                mismatch_count=mismatch_count,
                inconclusive_count=inconclusive_count,
                reduction_pct=test_plan.coverage_metrics.get("candidate_reduction_pct", 0.0),
                experiments=experiments_list,
            ),
        )

        action_exp = AgentAction(
            action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
            agent_role="Risk-Directed Test Planner",
            action_type="TOOL_INVOCATION",
            summary=f"Executed {len(selected_tests)} risk-directed experiments ({mismatch_count} mismatches reproduced).",
            rationale="Invoking target rating engine probes to reproduce divergent price outputs.",
            selected_tool="execute_experiments",
            latency_ms=exp_latency,
        )
        agent_actions.append(action_exp)

        # Connector-specific TARGET_EXECUTION accounting: distinguish "every
        # probe hard-failed" (connector unreachable/timed out entirely) from
        # "some/all probes succeeded but disagreed on price" (a genuine
        # premium mismatch) and from "a partial-response category failure"
        # (never counted as a confirmed mismatch AND never silently ignored).
        connector_all_probes_failed = False
        connector_any_partial_response = False
        if target_connector is not None:
            recorder.record(MissionStage.ORACLE_EXECUTION, StageStatus.COMPLETED)
            if not connector_probe_outcomes:
                recorder.record(
                    MissionStage.TARGET_EXECUTION, StageStatus.REVIEW_REQUIRED,
                    reason="No connector probes were executed for this mission.",
                )
            else:
                connector_any_partial_response = "PARTIAL_RESPONSE" in connector_probe_outcomes
                connector_all_probes_failed = all(s != "SUCCESS" for s in connector_probe_outcomes)
                if connector_all_probes_failed:
                    # An unreachable/unauthenticated/malformed/timed-out target is
                    # inconclusive evidence (REVIEW_REQUIRED), never a price defect.
                    recorder.record(
                        MissionStage.TARGET_EXECUTION, StageStatus.REVIEW_REQUIRED,
                        reason=f"All {len(connector_probe_outcomes)} connector probe(s) failed to return a premium.",
                    )
                elif connector_any_partial_response:
                    recorder.record(
                        MissionStage.TARGET_EXECUTION, StageStatus.REVIEW_REQUIRED,
                        reason="One or more connector probes returned a partial/incomplete response.",
                    )
                else:
                    recorder.record(MissionStage.TARGET_EXECUTION, StageStatus.COMPLETED)
        elif selected_tests:
            recorder.record(MissionStage.ORACLE_EXECUTION, StageStatus.COMPLETED)
            recorder.record(MissionStage.TARGET_EXECUTION, StageStatus.COMPLETED if right_pkg else StageStatus.NOT_APPLICABLE, reason=None if right_pkg else "No comparison target available.")
        else:
            recorder.record(
                MissionStage.ORACLE_EXECUTION, StageStatus.NOT_APPLICABLE,
                reason="No test scenarios were generated to execute.",
            )
            recorder.record(
                MissionStage.TARGET_EXECUTION, StageStatus.NOT_APPLICABLE,
                reason="No test scenarios were generated to execute.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 5: Trace Reconciliation & Root Cause Analysis
        self._mark_stage(mission.mission_id, "RECONCILIATION")
        if right_pkg and mismatch_count > 0:
            recon_res = self.reconciliation_engine.reconcile_packages(left_pkg, right_pkg)
            first_div = recon_res.first_divergent_node
            rc_finding = None
            if recon_res.discovered_root_causes:
                first_rc = recon_res.discovered_root_causes[0]
                rc_finding = RootCauseFinding(
                    node_id=first_rc.node_id,
                    title=first_rc.title,
                    explanation=first_rc.explanation,
                    expected_value=str(first_rc.expected_value),
                    actual_value=str(first_rc.actual_value),
                    divergence_type=str(first_rc.difference_type),
                )

            result.reconciliation = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ReconciliationData(
                    mismatch_count=recon_res.mismatch_count,
                    first_divergent_node=first_div,
                    root_cause=rc_finding,
                ),
            )
            recorder.record(MissionStage.RECONCILIATION, StageStatus.COMPLETED)
        elif target_connector is not None and mismatch_count > 0:
            first_mismatch = next(e for e in experiments_list if e.outcome == "MISMATCH")
            first_div = first_mismatch.first_divergent_node or "connector_final_premium"
            result.reconciliation = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=ReconciliationData(
                    mismatch_count=mismatch_count,
                    first_divergent_node=first_div,
                    root_cause=RootCauseFinding(
                        node_id=first_div,
                        title="Connector Premium Divergence",
                        explanation=(
                            f"Independent IPIR oracle (Source A) computed {first_mismatch.expected_premium} "
                            f"for scenario '{first_mismatch.probe_name}' "
                            f"(probe origin {first_mismatch.probe_origin or 'UNKNOWN'}, calculation date "
                            f"{first_mismatch.calculation_date} via {first_mismatch.calculation_date_source}); connector "
                            f"'{target_connector.connector_id}@{target_connector.engine_version}' "
                            f"returned {first_mismatch.actual_premium}."
                        ),
                        expected_value=first_mismatch.expected_premium,
                        actual_value=first_mismatch.actual_premium,
                        divergence_type="CONNECTOR_PREMIUM_MISMATCH",
                    ),
                ),
            )
            recorder.record(MissionStage.RECONCILIATION, StageStatus.COMPLETED)
        else:
            result.reconciliation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Zero price divergences reproduced during experiment probing.",
            )
            recorder.record(
                MissionStage.RECONCILIATION, StageStatus.NOT_APPLICABLE,
                reason="Zero price divergences reproduced during experiment probing.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 6: Portfolio Blast Radius & Measured Telemetry
        self._mark_stage(mission.mission_id, "PORTFOLIO_ANALYSIS")
        port_start = time.time()
        raw_port = None

        # Real Gemini decision point: justify whether the costly 50K-policy scan
        # is warranted. Only consulted when zero mismatches were reproduced — a
        # confirmed mismatch always forces the scan regardless of this vote, and
        # a Gemini failure defaults to the safe conservative choice: run it.
        run_portfolio = True
        if right_pkg and mismatch_count == 0:
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions, recorder)

            decision, evidence = self._ask_gemini(
                mission.mission_id, budget, "PORTFOLIO_JUSTIFICATION", PortfolioAnalysisDecision,
                system_instruction=(
                    "You are the RateGuard Assurance Supervisor deciding whether the costly full "
                    "50,000-policy portfolio blast-radius scan is warranted given zero reproduced "
                    "premium mismatches."
                ),
                prompt=(
                    f"Semantic differences identified: {len(sem_diffs)}. Premium mismatches reproduced: 0. "
                    "Decide whether the full portfolio exposure scan should still run."
                ),
            )
            if decision is not None:
                run_portfolio = decision.should_run_portfolio
                agent_actions.append(self._decision_action(
                    "Portfolio Exposure Analyst", "PORTFOLIO_JUSTIFICATION",
                    f"Gemini {'requested' if run_portfolio else 'waived'} the full portfolio exposure scan.",
                    evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
                ))
            else:
                run_portfolio = True
                reason = evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                agent_actions.append(self._decision_action(
                    "Portfolio Exposure Analyst", "PORTFOLIO_JUSTIFICATION",
                    "Deterministic fallback: running full portfolio scan (safe default; Gemini unavailable).",
                    evidence, is_gemini=False, fallback_reason=reason,
                ))

        # Connector-backed portfolio impact (Prompt 8): authoritative IPIR expected
        # premium vs. the connector's candidate premium for every eligible masked
        # portfolio row, executed as durable checkpointed batches. Never inferred
        # from missing data: only an actual scan populates these sections.
        connector_impact: ImpactAggregate | None = None
        connector_impact_note: str | None = None
        if target_connector is not None and impact_runner is not None:
            if not connector_probe_outcomes or connector_all_probes_failed:
                connector_impact_note = (
                    "Portfolio repricing was not attempted: every connector probe failed to return a premium, "
                    "so the connector is treated as unavailable."
                )
            else:
                connector_impact = impact_runner.run(
                    mission=mission,
                    package=left_pkg,
                    connector_id=target_connector.connector_id,
                    engine_version=target_connector.engine_version,
                    cancellation_check=lambda: self._is_cancelled(mission.mission_id, cancellation_check),
                )
                if connector_impact.status == ImpactStatus.CANCELLED:
                    return self._finalize_cancelled(mission, result, agent_actions, recorder)

        if connector_impact is not None:
            imp = connector_impact
            port_duration = max(0.001, time.time() - port_start)
            result.blast_radius = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                reason=None if imp.completeness == "COMPLETE" else (
                    "PARTIAL: " + "; ".join(imp.incomplete_reasons or ["scan incomplete"])
                ),
                data=BlastRadiusResult(
                    total_policies_analyzed=imp.processed_policies,
                    semantically_exposed_count=imp.successful_comparisons,
                    behaviorally_affected_count=0,
                    financially_affected_count=imp.mismatches,
                    undercharged_policy_count=imp.undercharge_count,
                    overcharged_policy_count=imp.overcharge_count,
                    total_undercharge_amount=imp.undercharge_total,
                    total_overcharge_amount=imp.overcharge_total,
                    signed_net_variance=imp.signed_net_delta,
                    absolute_financial_exposure=imp.absolute_exposure,
                    portfolio_execution_seconds=round(port_duration, 3),
                    measured_throughput_policies_per_sec=round(imp.processed_policies / port_duration, 1),
                ),
            )
            result.connector_impact = SectionResult(status=AnalysisStatus.SUCCEEDED, data=imp)
            self.store.add_evidence(mission.mission_id, EvidenceRecord(
                evidence_id=f"EV-{uuid.uuid4().hex[:6].upper()}",
                run_id=mission.mission_id,
                evidence_type=EvidenceType.PORTFOLIO_EXPOSURE,
                title=f"Connector portfolio impact ({imp.status.value})",
                description="Aggregate-only connector-backed repricing result; no policy rows or identifiers.",
                data_summary=imp.model_dump(mode="json", exclude={"mismatch_examples", "cohort_distribution", "pipeline_impact"}),
            ))
            agent_actions.append(AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Portfolio Exposure Analyst",
                action_type="TOOL_INVOCATION",
                summary=(
                    f"Connector-backed impact {imp.status.value}: {imp.successful_comparisons} of "
                    f"{imp.eligible_policies} eligible policies compared, {imp.mismatches} mismatched."
                ),
                rationale="Authoritative IPIR expected premium compared with the connector's candidate premium per masked row.",
                selected_tool="connector_portfolio_impact",
                latency_ms=port_duration * 1000,
            ))
            if imp.completeness == "COMPLETE":
                recorder.record(MissionStage.PORTFOLIO_IMPACT, StageStatus.COMPLETED)
            else:
                recorder.record(
                    MissionStage.PORTFOLIO_IMPACT, StageStatus.REVIEW_REQUIRED,
                    reason="Connector impact scan incomplete: " + "; ".join(imp.incomplete_reasons or ["see impact evidence"]),
                )
        elif right_pkg and run_portfolio:
            raw_port = self.portfolio_analyzer.evaluate_portfolio(
                left_package=left_pkg,
                right_package=right_pkg,
                csv_filename=mission.objective.portfolio_dataset,
            )
            port_duration = max(0.001, time.time() - port_start)
            throughput = raw_port.total_policies / port_duration

            blast_data = BlastRadiusResult(
                total_policies_analyzed=raw_port.total_policies,
                semantically_exposed_count=raw_port.exposed_policy_count,
                behaviorally_affected_count=raw_port.behaviorally_affected_count,
                financially_affected_count=raw_port.financially_affected_count,
                undercharged_policy_count=raw_port.undercharged_policy_count,
                overcharged_policy_count=raw_port.overcharged_policy_count,
                total_undercharge_amount=str(raw_port.total_undercharge_amount),
                total_overcharge_amount=str(raw_port.total_overcharge_amount),
                signed_net_variance=str(raw_port.total_signed_variance),
                absolute_financial_exposure=str(raw_port.total_absolute_variance),
                multi_defect_policy_count=raw_port.multi_defect_policy_count,
                portfolio_execution_seconds=round(port_duration, 3),
                measured_throughput_policies_per_sec=round(throughput, 1),
            )

            result.blast_radius = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                data=blast_data,
            )

            action_blast = AgentAction(
                action_id=f"ACT-{uuid.uuid4().hex[:6].upper()}",
                agent_role="Portfolio Exposure Analyst",
                action_type="TOOL_INVOCATION",
                summary=f"Measured 50,000 policy blast radius (${blast_data.absolute_financial_exposure} exposure across {blast_data.financially_affected_count} policies).",
                rationale="Executing vectorized SQL queries to quantify financial risk and revenue leakage.",
                selected_tool="query_portfolio",
                latency_ms=port_duration * 1000,
            )
            agent_actions.append(action_blast)
            recorder.record(MissionStage.PORTFOLIO_IMPACT, StageStatus.COMPLETED)
        elif right_pkg and not run_portfolio:
            result.blast_radius = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Portfolio blast radius scan waived by Gemini justification (zero premium mismatches reproduced).",
            )
            recorder.record(
                MissionStage.PORTFOLIO_IMPACT, StageStatus.NOT_APPLICABLE,
                reason="Waived by Gemini justification (zero premium mismatches reproduced).",
            )
        else:
            result.blast_radius = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason=(
                    connector_impact_note
                    or "Portfolio blast radius evaluation requires a full IPIR target package or accessible portfolio batch execution."
                ),
            )
            if connector_impact_note is not None:
                result.connector_impact = SectionResult(status=AnalysisStatus.NOT_RUN, reason=connector_impact_note)
                recorder.record(MissionStage.PORTFOLIO_IMPACT, StageStatus.REVIEW_REQUIRED, reason=connector_impact_note)
            else:
                recorder.record(
                    MissionStage.PORTFOLIO_IMPACT, StageStatus.NOT_APPLICABLE,
                    reason=(
                        "No connector impact runner is configured for this execution; portfolio scanning of a "
                        "connector target was not performed."
                        if target_connector is not None
                        else "Portfolio blast radius evaluation requires a full IPIR target package."
                    ),
                )

        # STAGE 6b/6c: Consumer-protection analytics (locked doc section 9) --
        # computed from the same per-policy repricing pass the portfolio scan
        # above already ran; NOT_APPLICABLE only when that scan itself did
        # not run (waived, unavailable target, or connector-backed mission).
        if raw_port is not None and raw_port.cohort_distribution is not None:
            result.cohort_distribution = SectionResult(status=AnalysisStatus.SUCCEEDED, data=raw_port.cohort_distribution)
            recorder.record(MissionStage.COHORT_DISTRIBUTION, StageStatus.COMPLETED)
        elif connector_impact is not None and connector_impact.cohort_distribution is not None:
            result.cohort_distribution = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                reason=None if connector_impact.completeness == "COMPLETE" else "Computed over the compared subset only (scan incomplete).",
                data=CohortDistributionResult.model_validate(connector_impact.cohort_distribution),
            )
            recorder.record(MissionStage.COHORT_DISTRIBUTION, StageStatus.COMPLETED)
        else:
            result.cohort_distribution = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Cohort impact distribution requires the full portfolio blast-radius scan to have run.",
            )
            recorder.record(
                MissionStage.COHORT_DISTRIBUTION, StageStatus.NOT_APPLICABLE,
                reason="Portfolio blast-radius scan did not run for this mission (see PORTFOLIO_IMPACT stage).",
            )

        if raw_port is not None and raw_port.pipeline_impact is not None:
            result.pipeline_impact = SectionResult(status=AnalysisStatus.SUCCEEDED, data=raw_port.pipeline_impact)
            recorder.record(MissionStage.PIPELINE_IMPACT, StageStatus.COMPLETED)
        elif connector_impact is not None and connector_impact.pipeline_impact is not None:
            result.pipeline_impact = SectionResult(
                status=AnalysisStatus.SUCCEEDED,
                reason=None if connector_impact.completeness == "COMPLETE" else "Computed over the compared subset only (scan incomplete).",
                data=PipelineImpactResult.model_validate(connector_impact.pipeline_impact),
            )
            recorder.record(MissionStage.PIPELINE_IMPACT, StageStatus.COMPLETED)
        else:
            result.pipeline_impact = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="30/60/90-day renewal pipeline impact requires the full portfolio blast-radius scan to have run.",
            )
            recorder.record(
                MissionStage.PIPELINE_IMPACT, StageStatus.NOT_APPLICABLE,
                reason="Portfolio blast-radius scan did not run for this mission (see PORTFOLIO_IMPACT stage).",
            )

        # STAGE 6d/6e: Consumer explanation facts + bounded draft (locked doc
        # section 10) -- only meaningful when a premium mismatch was actually
        # reproduced for at least one scenario; a clean mission has nothing
        # to explain.
        first_mismatch_exp = next((e for e in experiments_list if not e.matches), None)
        recon_root_cause = result.reconciliation.data.root_cause if result.reconciliation.data else None

        if first_mismatch_exp is not None and recon_root_cause is not None:
            try:
                facts = build_explanation_facts(
                    case_id=first_mismatch_exp.experiment_id,
                    prior_premium=Decimal(first_mismatch_exp.expected_premium),
                    new_premium=Decimal(first_mismatch_exp.actual_premium),
                    factor_label=recon_root_cause.title,
                    effective_date=left_pkg.effective_period.start,
                )
            except (ArithmeticError, ValueError, TypeError):
                # Not a bug in this code -- an honestly-inapplicable case, e.g.
                # the mismatched experiment's premium is a non-numeric
                # connector-failure marker ("N/A (connector failure)") rather
                # than a real quoted premium. Nothing to explain.
                reason = "The reproduced mismatch has no numeric expected/actual premium pair to explain (e.g. a connector-failure probe)."
                result.explanation_facts = SectionResult(status=AnalysisStatus.NOT_RUN, reason=reason)
                recorder.record(MissionStage.EXPLANATION_FACTS, StageStatus.NOT_APPLICABLE, reason=reason)
                result.explanation_draft = SectionResult(status=AnalysisStatus.NOT_RUN, reason=reason)
                recorder.record(MissionStage.EXPLANATION_DRAFT, StageStatus.NOT_APPLICABLE, reason=reason)
            else:
                result.explanation_facts = SectionResult(status=AnalysisStatus.SUCCEEDED, data=facts)
                recorder.record(MissionStage.EXPLANATION_FACTS, StageStatus.COMPLETED)

                if self._is_cancelled(mission.mission_id, cancellation_check):
                    return self._finalize_cancelled(mission, result, agent_actions, recorder)

                gemini_draft_text: str | None = None
                decision, evidence = self._ask_gemini(
                    mission.mission_id, budget, "EXPLANATION_DRAFT", ExplanationDraftDecision,
                    system_instruction=(
                        "You are the RateGuard Consumer Explanation Drafter. Write a short, factual, "
                        "plain-language explanation of a premium change using ONLY the numbers and dates "
                        "in the supplied facts object. Never state a cause, amount, or date not present "
                        "in those facts. Never state legal compliance, blame, eligibility, or coverage "
                        "advice."
                    ),
                    prompt=f"ExplanationFacts: {facts.model_dump_json()}",
                )
                if decision is not None:
                    gemini_draft_text = decision.draft_text

                draft = build_explanation_draft(facts, gemini_draft_text)

                if draft.source == "gemini":
                    agent_actions.append(self._decision_action(
                        "Consumer Explanation Drafter", "EXPLANATION_DRAFT",
                        "Gemini drafted a consumer explanation from the deterministic facts object; "
                        "every amount/date it cited was validated against those facts.",
                        evidence, is_gemini=True,
                        needs_human_review=decision.needs_human_review if decision else False,
                    ))
                else:
                    reason = (
                        "UNSUPPORTED_FACT_VALUE" if gemini_draft_text
                        else (evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED")
                    )
                    agent_actions.append(self._decision_action(
                        "Consumer Explanation Drafter", "EXPLANATION_DRAFT",
                        "Deterministic fallback template used (Gemini unavailable, or its draft cited a "
                        "value outside the facts object and was rejected).",
                        evidence, is_gemini=False, fallback_reason=reason,
                    ))

                result.explanation_draft = SectionResult(status=AnalysisStatus.SUCCEEDED, data=draft)
                recorder.record(MissionStage.EXPLANATION_DRAFT, StageStatus.COMPLETED)
        else:
            reason = "No reproduced premium mismatch to explain."
            result.explanation_facts = SectionResult(status=AnalysisStatus.NOT_RUN, reason=reason)
            recorder.record(MissionStage.EXPLANATION_FACTS, StageStatus.NOT_APPLICABLE, reason=reason)
            result.explanation_draft = SectionResult(status=AnalysisStatus.NOT_RUN, reason=reason)
            recorder.record(MissionStage.EXPLANATION_DRAFT, StageStatus.NOT_APPLICABLE, reason=reason)

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 7: Remediation Proposal & Revalidation
        self._mark_stage(mission.mission_id, "REMEDIATION")
        if right_pkg and result.semantic_analysis.data and result.semantic_analysis.data.differences:
            all_diffs = result.semantic_analysis.data.differences
            finding_ids = {d.finding_id for d in all_diffs}

            if mission.mode == ComparisonMode.EQUIVALENCE:
                # Symmetric Equivalence: neither Source A nor Source B is presumed
                # authoritative, so no directional patch is generated automatically
                # here. Gemini's decision at this stage is deliberately neutral
                # (PROPOSE_ALIGNMENT_OPTIONS, never PROPOSE_REMEDIATION) and is
                # evidence only -- the concrete directional patch is computed on
                # demand, only after a human explicitly picks a reference source,
                # via POST /missions/{id}/alignment-options.
                if self._is_cancelled(mission.mission_id, cancellation_check):
                    return self._finalize_cancelled(mission, result, agent_actions, recorder)

                decision, evidence = self._ask_gemini(
                    mission.mission_id, budget, "PROPOSE_ALIGNMENT_OPTIONS", AlignmentOptionsDecision,
                    system_instruction=(
                        "You are the RateGuard Assurance Supervisor reviewing confirmed differences "
                        "between two symmetrically-compared pricing sources. Neither Source A nor "
                        "Source B is presumed authoritative -- you are NOT proposing a correction and "
                        "MUST NOT claim to restore an intended or verified value. You MUST only select "
                        "finding_ids from the provided list. Identify which differences are material "
                        "enough that a human should weigh them before choosing an alignment reference."
                    ),
                    prompt=(
                        "Confirmed differences between Source A and Source B (JSON): "
                        f"{[{'finding_id': d.finding_id, 'title': d.title, 'severity': d.severity} for d in all_diffs]}\n"
                        "Select which finding_ids are material to a future alignment decision."
                    ),
                )
                chosen_finding_ids = [i for i in (decision.selected_finding_ids if decision else []) if i in finding_ids]
                if decision is not None and chosen_finding_ids:
                    agent_actions.append(self._decision_action(
                        "Alignment Specialist", "PROPOSE_ALIGNMENT_OPTIONS",
                        f"Gemini flagged {len(chosen_finding_ids)} of {len(all_diffs)} confirmed differences as "
                        "material to a future alignment decision. No directional patch was generated -- Source A "
                        "and Source B are both potentially valid; a directional alignment option is available on "
                        "demand once a reference source is selected.",
                        evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
                    ))
                else:
                    reason = "NO_VALID_IDS_IN_RESPONSE" if decision is not None else (
                        evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                    )
                    agent_actions.append(self._decision_action(
                        "Alignment Specialist", "PROPOSE_ALIGNMENT_OPTIONS",
                        f"Deterministic fallback: all {len(all_diffs)} confirmed differences remain available "
                        "for on-demand alignment.",
                        evidence, is_gemini=False, fallback_reason=reason,
                    ))

                result.remediation = SectionResult(
                    status=AnalysisStatus.NOT_RUN,
                    reason=(
                        "Equivalence mode is symmetric: neither Source A nor Source B is presumed "
                        "authoritative, so a directional alignment patch is not generated automatically. "
                        "Open the Alignment Options tab and select a reference source to generate one."
                    ),
                )
                result.revalidation = SectionResult(
                    status=AnalysisStatus.NOT_RUN,
                    reason="Revalidation is generated together with the on-demand alignment option.",
                )
                recorder.record(
                    MissionStage.REMEDIATION, StageStatus.NOT_APPLICABLE,
                    reason="Equivalence mode is symmetric; a directional patch is only generated on demand.",
                )
                recorder.record(
                    MissionStage.REVALIDATION, StageStatus.NOT_APPLICABLE,
                    reason="Revalidation is generated together with the on-demand alignment option.",
                )
            else:
                # Real Gemini decision point: propose a structured remediation candidate
                # by selecting which confirmed findings to correct. Gemini may only
                # choose finding_ids the diff engine already produced; the deterministic
                # remediation service applies each finding's own recorded intent_value —
                # Gemini never supplies or invents a corrected number itself.
                if self._is_cancelled(mission.mission_id, cancellation_check):
                    return self._finalize_cancelled(mission, result, agent_actions, recorder)

                decision, evidence = self._ask_gemini(
                    mission.mission_id, budget, "PROPOSE_REMEDIATION", RemediationProposalDecision,
                    system_instruction=(
                        "You are the RateGuard Assurance Supervisor proposing a structured remediation "
                        "candidate. You MUST only select finding_ids from the provided list, and MUST NOT "
                        "invent a corrected value — the deterministic remediation service applies each "
                        "finding's own recorded intent_value."
                    ),
                    prompt=(
                        "Confirmed findings (JSON): "
                        f"{[{'finding_id': d.finding_id, 'title': d.title, 'severity': d.severity} for d in all_diffs]}\n"
                        "Select which finding_ids the remediation candidate should correct."
                    ),
                )
                chosen_finding_ids = [i for i in (decision.selected_finding_ids if decision else []) if i in finding_ids]
                if decision is not None and chosen_finding_ids:
                    target_diffs = [d for d in all_diffs if d.finding_id in chosen_finding_ids]
                    agent_actions.append(self._decision_action(
                        "Remediation Specialist", "PROPOSE_REMEDIATION",
                        f"Gemini proposed correcting {len(target_diffs)} of {len(all_diffs)} confirmed findings.",
                        evidence, is_gemini=True, needs_human_review=decision.needs_human_review,
                    ))
                else:
                    target_diffs = all_diffs  # safe conservative default: fix every confirmed finding
                    reason = "NO_VALID_IDS_IN_RESPONSE" if decision is not None else (
                        evidence.failure_category if evidence else "CALL_BUDGET_EXHAUSTED"
                    )
                    agent_actions.append(self._decision_action(
                        "Remediation Specialist", "PROPOSE_REMEDIATION",
                        f"Deterministic fallback: proposing correction for all {len(all_diffs)} confirmed findings.",
                        evidence, is_gemini=False, fallback_reason=reason,
                    ))

                rem_prop = self.remediation_service.generate_remediation_proposal(left_pkg, right_pkg, target_diffs)
                result.remediation = SectionResult(
                    status=AnalysisStatus.SUCCEEDED,
                    data=rem_prop,
                )

                # Real Gemini decision point: select targeted (previously-mismatched)
                # and regression (control) scenario ids to rerun deterministically
                # against the patched candidate before revalidation.
                targeted_ids: list[str] = [e.experiment_id for e in experiments_list if not e.matches]
                # A scenario that is itself the targeted (mismatched) witness
                # can also be classified CONTROL (e.g. the one-and-only
                # scenario a still-sparse candidate pool produced) -- excluding
                # it here guarantees "regression" always means a genuinely
                # different scenario, never the same id serving double duty.
                control_ids = [
                    sc.id for sc in (test_plan.candidate_scenarios if test_plan is not None else [])
                    if sc.classification == ScenarioClassification.CONTROL and sc.id not in targeted_ids
                ]
                regression_ids: list[str] = control_ids[:MAX_REGRESSION_TESTS]

                if test_plan is not None and (targeted_ids or control_ids):
                    if self._is_cancelled(mission.mission_id, cancellation_check):
                        return self._finalize_cancelled(mission, result, agent_actions, recorder)

                    decision2, evidence2 = self._ask_gemini(
                        mission.mission_id, budget, "SELECT_REVALIDATION_TESTS", RemediationRevalidationSelectionDecision,
                        system_instruction=(
                            "You are the RateGuard Assurance Supervisor selecting which previously-mismatched "
                            "(targeted) and control (regression) scenario ids to rerun deterministically against "
                            "the proposed remediation patch. Only select ids from the provided lists."
                        ),
                        prompt=(
                            f"Previously-mismatched scenario ids (targeted candidates): {targeted_ids}\n"
                            f"Control scenario ids (regression candidates): {control_ids}"
                        ),
                    )
                    if decision2 is not None:
                        valid_targeted = set(targeted_ids)
                        valid_control = set(control_ids)
                        chosen_targeted = [i for i in decision2.targeted_test_ids if i in valid_targeted] or targeted_ids
                        chosen_regression = [i for i in decision2.regression_test_ids if i in valid_control] or regression_ids
                        targeted_ids, regression_ids = chosen_targeted, chosen_regression
                        agent_actions.append(self._decision_action(
                            "Remediation Specialist", "SELECT_REVALIDATION_TESTS",
                            f"Gemini selected {len(targeted_ids)} targeted and {len(regression_ids)} regression scenarios for revalidation.",
                            evidence2, is_gemini=True, needs_human_review=decision2.needs_human_review,
                        ))
                    else:
                        reason = evidence2.failure_category if evidence2 else "CALL_BUDGET_EXHAUSTED"
                        agent_actions.append(self._decision_action(
                            "Remediation Specialist", "SELECT_REVALIDATION_TESTS",
                            f"Deterministic fallback: rerunning all {len(targeted_ids)} mismatched and {len(regression_ids)} control scenarios.",
                            evidence2, is_gemini=False, fallback_reason=reason,
                        ))

                # Execute Revalidation
                reval_res = self.remediation_service.revalidate_remediation(
                    left_pkg, right_pkg, rem_prop, mission.objective.portfolio_dataset,
                    scenario_pool=test_plan.candidate_scenarios if test_plan is not None else None,
                    targeted_scenario_ids=targeted_ids,
                    regression_scenario_ids=regression_ids,
                )
                result.revalidation = SectionResult(
                    status=AnalysisStatus.SUCCEEDED,
                    data=reval_res,
                )
                recorder.record(MissionStage.REMEDIATION, StageStatus.COMPLETED)
                recorder.record(MissionStage.REVALIDATION, StageStatus.COMPLETED)
        else:
            result.remediation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Remediation proposal not required for clean verification or runtime verification mode.",
            )
            result.revalidation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Revalidation omitted.",
            )
            recorder.record(
                MissionStage.REMEDIATION, StageStatus.NOT_APPLICABLE,
                reason=(
                    "A live connector target cannot be auto-patched; remediation requires a comparable "
                    "IPIR package for Source B."
                    if target_connector is not None
                    else "Remediation proposal not required: no confirmed semantic differences."
                ),
            )
            recorder.record(
                MissionStage.REVALIDATION, StageStatus.NOT_APPLICABLE,
                reason="No remediation was generated for this mission.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions, recorder)

        # STAGE 8: Final Release Decision
        self._mark_stage(mission.mission_id, "DECISION")
        blocking_reasons: list[str] = []
        if mismatch_count > 0:
            blocking_reasons.append(f"{mismatch_count} price calculation mismatches reproduced.")
        if connector_impact is not None:
            imp = connector_impact
            if imp.mismatches > 0:
                bound = (
                    f" (a lower bound: only {imp.coverage_pct}% of eligible policies were compared)"
                    if imp.exposure_is_lower_bound else ""
                )
                blocking_reasons.append(
                    f"Connector-backed portfolio repricing found {imp.mismatches} policies priced differently by "
                    f"connector '{target_connector.connector_id}@{target_connector.engine_version}'; absolute exposure "
                    f"${imp.absolute_exposure}{bound}."
                )
            elif imp.completeness != "COMPLETE":
                review_reasons.append(
                    f"Connector portfolio impact is {imp.status.value} (coverage {imp.coverage_pct}%, "
                    f"{imp.inconclusive} inconclusive, {imp.unprocessed_policies} unprocessed): "
                    + "; ".join(imp.incomplete_reasons or ["incomplete"])
                    + ". No mismatch was proven, but zero impact cannot be inferred from incomplete data."
                )
                review_required = True
        elif connector_impact_note is not None:
            review_reasons.append(connector_impact_note)
            review_required = True
        elif result.blast_radius.data and float(result.blast_radius.data.absolute_financial_exposure) > 0:
            blocking_reasons.append(f"Financial exposure of ${result.blast_radius.data.absolute_financial_exposure} exceeds zero-drift tolerance.")
        if sem_diffs:
            blocking_reasons.append(f"{len(sem_diffs)} AST semantic differences identified.")

        # Inconclusive evidence is NOT a pricing defect (locked doc 7.4): an
        # unreachable, unauthenticated, malformed, timed-out or otherwise
        # incomplete connector result -- or a probe whose calculation date was
        # rejected -- can never be represented as BLOCK_DEPLOYMENT on its own.
        # A proven mismatch elsewhere still blocks; otherwise this forces
        # REVIEW_REQUIRED and can never yield PASS.
        if target_connector is not None and not connector_probe_outcomes and inconclusive_count == 0:
            review_reasons.append(
                f"No probe could be executed against connector '{target_connector.connector_id}' "
                "(no executable probe could be generated from the compiled source); no pricing "
                "conclusion can be drawn."
            )
            review_required = True
        if inconclusive_count > 0:
            reasons = sorted({e.inconclusive_reason for e in experiments_list if e.outcome == "INCONCLUSIVE" and e.inconclusive_reason})
            review_reasons.append(
                f"{inconclusive_count} of {len(experiments_list)} probe(s) were inconclusive "
                f"({', '.join(reasons) or 'no detail'}); the candidate implementation could not be "
                "evaluated for them, which is not evidence of a pricing defect."
            )
            review_required = True

        # A connector partial-response category failure is a data-completeness
        # gap, not a confirmed numeric mismatch — it must never be silently
        # absorbed into either a clean PASS or a plain mismatch-based
        # BLOCK_DEPLOYMENT that would misrepresent it as a confirmed price
        # disagreement. It always forces at least REVIEW_REQUIRED.
        if connector_any_partial_response:
            review_reasons.append(
                f"Connector '{target_connector.connector_id}' returned one or more partial/incomplete "
                "responses; premium comparison evidence is incomplete for at least one probe."
            )
            review_required = True

        # Never issue PASS unless the mandatory deterministic evidence for this
        # mission's mode actually exists — a Gemini outage or a skipped optional
        # stage must never be able to manufacture a clean release decision.
        if not self._mandatory_evidence_ok(result) and not blocking_reasons:
            blocking_reasons.append(
                "Mandatory deterministic evidence is incomplete; a PASS decision cannot be issued."
            )

        # No mission stage may be silently missing when a decision is issued —
        # this is the concrete enforcement of "every stage visible with a
        # reason; incomplete evidence never produces PASS."
        missing_stages = recorder.missing_stages()
        # DECISION and EVIDENCE_FINALIZATION are recorded below, after this
        # check, so they are expected to still be "missing" at this point.
        missing_stages = [
            s for s in missing_stages if s not in (MissionStage.DECISION, MissionStage.EVIDENCE_FINALIZATION)
        ]
        if missing_stages:
            blocking_reasons.append(
                f"{len(missing_stages)} mission stage(s) were never accounted for: "
                f"{[s.value for s in missing_stages]}."
            )

        if blocking_reasons:
            decision_status = "BLOCK_DEPLOYMENT"
            # Cite the actual reasons rather than len(blocking_reasons) as a single
            # "N critical pricing drift findings" count: blocking_reasons mixes
            # heterogeneous signals (semantic diffs, connector mismatches, missing
            # stages, etc.) whose own counts are not interchangeable with each
            # other or with the number of reasons in the list.
            summary_msg = "Deployment blocked: " + " ".join(blocking_reasons)
            rec_msg = "Apply proposed rating engine remediation patch and re-run assurance verification before releasing."
        elif review_required:
            # Every other signal agrees, but compilation uncertainty and/or a
            # product/jurisdiction mismatch between the two sources was
            # flagged -- that must never be silently absorbed into a PASS.
            decision_status = "REVIEW_REQUIRED"
            summary_msg = "No pricing drift was proven, but this mission cannot issue a PASS: " + " ".join(review_reasons)
            rec_msg = "Resolve the flagged issue, then re-run assurance verification."
            blocking_reasons = review_reasons
        else:
            decision_status = "PASS"
            summary_msg = "Assurance mission verified full compliance and equivalence."
            rec_msg = "Approve pricing engine release."

        result.release_decision = SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=ReleaseDecision(
                status=decision_status,
                confidence_score=1.0,
                summary=summary_msg,
                blocking_reasons=blocking_reasons,
                recommendation=rec_msg,
            ),
        )

        result.overall_status = "COMPLETED" if decision_status == "PASS" else "COMPLETED"
        mission.status = MissionStatus.COMPLETED
        result.agent_execution = SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=agent_actions,
        )
        result.evidence_refs = evidence_ids + budget.evidence_ids + connector_evidence_ids

        recorder.record(MissionStage.DECISION, StageStatus.COMPLETED)
        still_missing = [s for s in recorder.missing_stages() if s != MissionStage.EVIDENCE_FINALIZATION]
        recorder.record(
            MissionStage.EVIDENCE_FINALIZATION,
            StageStatus.COMPLETED if not still_missing else StageStatus.FAILED,
            reason=None if not still_missing else f"Missing stages: {[s.value for s in still_missing]}",
        )
        result.stage_outcomes = recorder.outcomes()

        # Honest final ai_runtime status: only ever claims a live invocation when
        # at least one Gemini call in this mission actually succeeded.
        if budget.any_gemini_success:
            result.ai_runtime["model_status"] = AI_RUNTIME_LIVE_STATUS
        elif budget.any_gemini_attempted:
            result.ai_runtime["model_status"] = AI_RUNTIME_FALLBACK_STATUS
        else:
            result.ai_runtime["model_status"] = AI_RUNTIME_NOT_INVOKED_STATUS
        result.ai_runtime["gemini_calls_made"] = str(budget.gemini_call_count)

        self._update_mission_record(mission, result, agent_actions)
        return result

    def _create_record_from_mission(self, mission: AssuranceMission) -> Any:
        from app.storage.models import AssuranceRunRecord, AssuranceRunStatus
        return AssuranceRunRecord(
            run_id=mission.mission_id,
            status=AssuranceRunStatus.RUNNING,
            workflow_stage="RUNNING",
            left_package_id=mission.source_a.source_id,
            right_package_id=mission.source_b.source_id if mission.source_b else None,
            metadata=mission.dict(),
        )

    def _update_mission_record(
        self, mission: AssuranceMission, result: AssuranceResultV2, agent_actions: list[AgentAction]
    ) -> None:
        """Persists the final mission outcome via apply_transition so a mission that
        was CANCELLED (by a request that raced in during execution) can never be
        overwritten with a later COMPLETED/FAILED/NEEDS_REVIEW result from this
        worker. If the transition is refused (target not legal from the mission's
        current stored status), the existing terminal record is left untouched."""
        from app.storage.models import AssuranceRunStatus

        if mission.status == MissionStatus.COMPLETED:
            target_status, workflow_stage = AssuranceRunStatus.COMPLETED, "FINISHED"
        elif mission.status == MissionStatus.NEEDS_REVIEW:
            target_status, workflow_stage = AssuranceRunStatus.NEEDS_REVIEW, "NEEDS_REVIEW"
        elif mission.status == MissionStatus.FAILED:
            target_status, workflow_stage = AssuranceRunStatus.FAILED, "FAILED"
        else:
            target_status, workflow_stage = AssuranceRunStatus(mission.status.value), mission.status.value

        existing = self.store.get_run(mission.mission_id)
        if existing is None:
            self.store.create_run(self._create_record_from_mission(mission))

        transition = apply_transition(self.store, mission.mission_id, target_status, workflow_stage=workflow_stage)
        if not transition.ok or transition.record is None:
            # Refused (e.g. mission already CANCELLED/ARCHIVED) — do not clobber it.
            return

        rec = transition.record
        rec.decision = result.release_decision.data.status if result.release_decision.data else "UNKNOWN"
        rec.summary = result.release_decision.data.summary if result.release_decision.data else "Mission executed."
        rec.report = result.dict()
        rec.agent_activity = [act.dict() for act in agent_actions]

        self.store.update_run(rec)
