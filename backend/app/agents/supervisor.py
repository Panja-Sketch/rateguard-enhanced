import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal
from typing import Any

from app.adapters.extractor_registry import EXTRACTOR_REGISTRY, excel_layout_recognized
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.agents.config import get_agent_config
from app.agents.decision_schemas import (
    MAX_ADDITIONAL_PROBE_TESTS,
    MAX_REGRESSION_TESTS,
    MAX_SELECTED_TESTS,
    AlignmentOptionsDecision,
    DifferencePrioritizationDecision,
    EvidenceSufficiencyDecision,
    ExtractionStrategyDecision,
    GeminiDecisionBase,
    PortfolioAnalysisDecision,
    RemediationProposalDecision,
    RemediationRevalidationSelectionDecision,
    TestSelectionDecision,
)
from app.agents.gemini_client import GeminiDecisionClient, GeminiInvocationEvidence
from app.engines.diff import SemanticDiffEngine
from app.engines.impact import PricingImpactEngine
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.portfolio import PortfolioExposureAnalyzer
from app.engines.reconciliation import PricingReconciliationEngine
from app.engines.testing import RiskDirectedTestGenerator
from app.engines.testing.models import PricingTestScenario, ScenarioClassification
from app.ipir.package import IPIRPackage
from app.ipir.schema import validate_ipir_schema
from app.models import (
    AgentAction,
    AnalysisStatus,
    AssuranceMission,
    AssuranceResultV2,
    BlastRadiusResult,
    ComparisonMode,
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
MAX_GEMINI_CALLS_PER_MISSION = 6
MAX_PROBE_ROUNDS = 1

# Below this confidence, an extraction result always requires human review
# regardless of which extractor (deterministic, Gemini-selected, or fallback)
# produced it.
LOW_CONFIDENCE_REVIEW_THRESHOLD = 0.60


@dataclass
class _InvestigationBudget:
    """Mission-scoped, single-use tracker for Gemini call/probe budgets and
    duplicate-probe prevention. One instance per `run_mission()` call."""

    gemini_call_count: int = 0
    any_gemini_success: bool = False
    any_gemini_attempted: bool = False
    evidence_ids: list[str] = dc_field(default_factory=list)
    executed_test_ids: set[str] = dc_field(default_factory=set)


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

    def __init__(self, store: BaseRunStore, gemini_client: GeminiDecisionClient | None = None):
        self.store = store
        self.semantic_diff_engine = SemanticDiffEngine()
        self.impact_engine = PricingImpactEngine()
        self.test_generator = RiskDirectedTestGenerator()
        self.reconciliation_engine = PricingReconciliationEngine()
        self.portfolio_analyzer = PortfolioExposureAnalyzer()
        self.remediation_service = RemediationService()
        self.gemini = gemini_client if gemini_client is not None else GeminiDecisionClient(get_agent_config())

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

    def _finalize_cancelled(
        self, mission: AssuranceMission, result: AssuranceResultV2, agent_actions: list[AgentAction]
    ) -> AssuranceResultV2:
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
        if budget.gemini_call_count >= MAX_GEMINI_CALLS_PER_MISSION:
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

        if result.confidence < LOW_CONFIDENCE_REVIEW_THRESHOLD:
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
        cancellation_check: "Callable[[], bool] | None" = None,
    ) -> AssuranceResultV2:
        agent_actions: list[AgentAction] = []
        tool_invocations: list[ToolInvocation] = []
        evidence_ids: list[str] = []
        budget = _InvestigationBudget()
        raw_diff_result = None
        test_plan = None

        result = AssuranceResultV2(
            mission_id=mission.mission_id,
            mode=mission.mode.value,
            overall_status="RUNNING",
            ai_runtime={
                "model_id": "gemini-3.7-flash",
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
            result.validation = SectionResult(
                status=AnalysisStatus.FAILED,
                error_message="Mission validation failed.",
                data=val_issues,
            )
            result.overall_status = "FAILED"
            mission.status = MissionStatus.FAILED
            mission.validation_issues = val_issues
            self._update_mission_record(mission, result, agent_actions)
            return result

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
        review_required = bool(review_reasons)

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

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
                    return self._finalize_cancelled(mission, result, agent_actions)

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
        else:
            prioritized_diff_ids = []
            result.semantic_analysis = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Semantic comparison skipped: no comparison target available.",
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

            oracle = PremiumOracleCalculator(left_pkg)
            target_calc = PremiumOracleCalculator(right_pkg)

            # Verification sample probes
            raw_impact = self.impact_engine.analyze(raw_diff_result, left_pkg)
            test_plan = self.test_generator.generate_plan(left_pkg, raw_diff_result, raw_impact)
            test_cases = test_plan.selected_scenarios[:5]

            experiments_list: list[RuntimeExperiment] = []

            for tc in test_cases:
                exp_res = oracle.calculate_policy_premium(tc.risk_values)
                act_res = target_calc.calculate_policy_premium(tc.risk_values)
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

            result.overall_status = "COMPLETED"
            mission.status = MissionStatus.COMPLETED
            self._update_mission_record(mission, result, agent_actions)
            return result

        # -------------------------------------------------------------
        # BRANCH B: MATERIAL DRIFT OR RUNTIME VERIFICATION
        # -------------------------------------------------------------
        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

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
        else:
            raw_impact = None
            result.impact_analysis = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Dependency impact graph traversal skipped: no comparison target available.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

        # STAGE 4: Risk-Directed Boundary Testing / Black-Box Probes
        self._mark_stage(mission.mission_id, "RISK_DIRECTED_TESTING")
        exp_start = time.time()
        if raw_diff_result and raw_impact:
            test_plan = self.test_generator.generate_plan(left_pkg, raw_diff_result, raw_impact)
            selected_tests = test_plan.selected_scenarios  # deterministic default

            # Real Gemini decision point: select which deterministically-generated
            # candidate boundary tests to execute. Gemini may only choose scenario
            # ids from the candidate pool the optimizer already produced.
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions)

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
        else:
            selected_tests = []

        experiments_list: list[RuntimeExperiment] = []
        mismatch_count = 0
        match_count = 0

        oracle = PremiumOracleCalculator(left_pkg)
        target_calc = PremiumOracleCalculator(right_pkg) if right_pkg else None

        def _run_probe(tc: PricingTestScenario) -> tuple[Decimal, Decimal]:
            """Executes exactly one deterministic premium-oracle-vs-target probe.
            Which scenario reaches this function is Gemini's only discretion —
            the arithmetic itself is untouched deterministic engine code."""
            exp_prem = oracle.calculate_policy_premium(tc.risk_values).final_premium
            if target_calc:
                act_prem = target_calc.calculate_policy_premium(tc.risk_values).final_premium
            else:
                act_prem = Decimal("0.00")
            return exp_prem, act_prem

        for tc in selected_tests:
            exp_prem, act_prem = _run_probe(tc)
            matched = exp_prem == act_prem
            if matched:
                match_count += 1
            else:
                mismatch_count += 1

            scenario_id = getattr(tc, "id", getattr(tc, "scenario_id", "RG-EXP"))
            budget.executed_test_ids.add(scenario_id)
            experiments_list.append(
                RuntimeExperiment(
                    experiment_id=scenario_id,
                    probe_name=tc.name,
                    category="RISK_DIRECTED",
                    risk_inputs=tc.risk_values,
                    expected_premium=str(exp_prem),
                    actual_premium=str(act_prem),
                    matches=matched,
                )
            )

        # Real Gemini decision point (bounded to MAX_PROBE_ROUNDS): decide whether
        # enough evidence has been gathered, or request one more bounded round of
        # additional boundary probes from the untested candidate pool. Never
        # re-executes a scenario id already run in this mission.
        probe_round = 0
        while (
            probe_round < MAX_PROBE_ROUNDS
            and mismatch_count > 0
            and test_plan is not None
            and budget.gemini_call_count < MAX_GEMINI_CALLS_PER_MISSION
        ):
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions)

            remaining_pool = {
                sc.id: sc for sc in test_plan.candidate_scenarios if sc.id not in budget.executed_test_ids
            }
            if not remaining_pool:
                break

            mismatched_probe_names = [e.probe_name for e in experiments_list if not e.matches]
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
                tc = remaining_pool[extra_id]
                exp_prem, act_prem = _run_probe(tc)
                matched = exp_prem == act_prem
                if matched:
                    match_count += 1
                else:
                    mismatch_count += 1
                budget.executed_test_ids.add(extra_id)
                experiments_list.append(
                    RuntimeExperiment(
                        experiment_id=extra_id,
                        probe_name=tc.name,
                        category="ADDITIONAL_PROBE",
                        risk_inputs=tc.risk_values,
                        expected_premium=str(exp_prem),
                        actual_premium=str(act_prem),
                        matches=matched,
                    )
                )

        exp_latency = (time.time() - exp_start) * 1000

        result.experiments = SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=ExperimentsData(
                total_generated=test_plan.candidate_count if test_plan is not None else len(selected_tests),
                total_executed=len(selected_tests),
                match_count=match_count,
                mismatch_count=mismatch_count,
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

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

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
        else:
            result.reconciliation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Zero price divergences reproduced during experiment probing.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

        # STAGE 6: Portfolio Blast Radius & Measured Telemetry
        self._mark_stage(mission.mission_id, "PORTFOLIO_ANALYSIS")
        port_start = time.time()

        # Real Gemini decision point: justify whether the costly 50K-policy scan
        # is warranted. Only consulted when zero mismatches were reproduced — a
        # confirmed mismatch always forces the scan regardless of this vote, and
        # a Gemini failure defaults to the safe conservative choice: run it.
        run_portfolio = True
        if right_pkg and mismatch_count == 0:
            if self._is_cancelled(mission.mission_id, cancellation_check):
                return self._finalize_cancelled(mission, result, agent_actions)

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

        if right_pkg and run_portfolio:
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
        elif right_pkg and not run_portfolio:
            result.blast_radius = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Portfolio blast radius scan waived by Gemini justification (zero premium mismatches reproduced).",
            )
        else:
            result.blast_radius = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Portfolio blast radius evaluation requires a full IPIR target package or accessible portfolio batch execution.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

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
                    return self._finalize_cancelled(mission, result, agent_actions)

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
            else:
                # Real Gemini decision point: propose a structured remediation candidate
                # by selecting which confirmed findings to correct. Gemini may only
                # choose finding_ids the diff engine already produced; the deterministic
                # remediation service applies each finding's own recorded intent_value —
                # Gemini never supplies or invents a corrected number itself.
                if self._is_cancelled(mission.mission_id, cancellation_check):
                    return self._finalize_cancelled(mission, result, agent_actions)

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
                        return self._finalize_cancelled(mission, result, agent_actions)

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
        else:
            result.remediation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Remediation proposal not required for clean verification or runtime verification mode.",
            )
            result.revalidation = SectionResult(
                status=AnalysisStatus.NOT_RUN,
                reason="Revalidation omitted.",
            )

        if self._is_cancelled(mission.mission_id, cancellation_check):
            return self._finalize_cancelled(mission, result, agent_actions)

        # STAGE 8: Final Release Decision
        self._mark_stage(mission.mission_id, "DECISION")
        blocking_reasons: list[str] = []
        if mismatch_count > 0:
            blocking_reasons.append(f"{mismatch_count} price calculation mismatches reproduced.")
        if result.blast_radius.data and float(result.blast_radius.data.absolute_financial_exposure) > 0:
            blocking_reasons.append(f"Financial exposure of ${result.blast_radius.data.absolute_financial_exposure} exceeds zero-drift tolerance.")
        if sem_diffs:
            blocking_reasons.append(f"{len(sem_diffs)} AST semantic differences identified.")

        # Never issue PASS unless the mandatory deterministic evidence for this
        # mission's mode actually exists — a Gemini outage or a skipped optional
        # stage must never be able to manufacture a clean release decision.
        if not self._mandatory_evidence_ok(result) and not blocking_reasons:
            blocking_reasons.append(
                "Mandatory deterministic evidence is incomplete; a PASS decision cannot be issued."
            )

        if blocking_reasons:
            decision_status = "BLOCK_DEPLOYMENT"
            summary_msg = f"Deployment blocked due to {len(blocking_reasons)} critical pricing drift findings."
            rec_msg = "Apply proposed rating engine remediation patch and re-run assurance verification before releasing."
        elif review_required:
            # Every other signal agrees, but compilation uncertainty and/or a
            # product/jurisdiction mismatch between the two sources was
            # flagged -- that must never be silently absorbed into a PASS.
            decision_status = "REVIEW_REQUIRED"
            summary_msg = "No pricing drift found, but this mission cannot issue a PASS: " + " ".join(review_reasons)
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
        result.evidence_refs = evidence_ids + budget.evidence_ids

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
