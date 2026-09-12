"""Tests for the real, structured Gemini decision points wired into
AssuranceSupervisor. Every test here injects a FakeGeminiClient (or a real
GeminiDecisionClient explicitly configured as disabled) — no test may reach a
real model or live GCP endpoint.

Full-mission runs are deliberately consolidated: each `_run_defective()` call
triggers three 50K-policy portfolio scans (stage 6, plus before/after inside
remediation revalidation), so tests that don't need mutual isolation share a
single run rather than paying that cost repeatedly.
"""

import re
from datetime import UTC, datetime

from app.agents.config import AgentConfig
from app.agents.decision_schemas import (
    DifferencePrioritizationDecision,
    EvidenceSufficiencyDecision,
    PortfolioAnalysisDecision,
    RemediationProposalDecision,
    RemediationRevalidationSelectionDecision,
    TestSelectionDecision,
)
from app.agents.gemini_client import GeminiDecisionClient, GeminiInvocationEvidence
from app.agents.supervisor import (
    MAX_GEMINI_CALLS_PER_MISSION,
    MAX_PROBE_ROUNDS,
    AssuranceSupervisor,
    _InvestigationBudget,
)
from app.agents.tool_registry import is_known_tool
from app.api.assurance import resolve_demo_package
from app.engines.diff import SemanticDiffEngine
from app.engines.impact import PricingImpactEngine
from app.engines.testing import RiskDirectedTestGenerator
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.models.result_v2 import AnalysisStatus, AssuranceResultV2, SectionResult
from app.services.remediation_service import RemediationService
from app.storage.memory_store import InMemoryRunStore
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus

_ID_PATTERN = re.compile(r"(FND-[0-9A-F]{6}|RG_CAND_\d+)")


def _payload_for(schema: type, found_ids: list[str], mode: str) -> dict:
    base = {"rationale": "Fake Gemini rationale for test.", "confidence": 0.9, "needs_human_review": False}
    bogus = ["BOGUS-ID-1", "BOGUS-ID-2"]

    if schema is DifferencePrioritizationDecision:
        return {**base, "selected_difference_ids": (bogus if mode == "invalid_ids" else found_ids[:3])}
    if schema is TestSelectionDecision:
        ids = bogus if mode == "invalid_ids" else (found_ids[:3] or bogus)
        return {**base, "selected_test_ids": ids}
    if schema is EvidenceSufficiencyDecision:
        if mode == "needs_more":
            return {**base, "stop_condition": "NEEDS_MORE_EVIDENCE", "additional_test_ids": found_ids[:2]}
        if mode == "invalid_ids":
            return {**base, "stop_condition": "NEEDS_MORE_EVIDENCE", "additional_test_ids": bogus}
        return {**base, "stop_condition": "SUFFICIENT", "additional_test_ids": []}
    if schema is PortfolioAnalysisDecision:
        return {**base, "should_run_portfolio": mode != "waive"}
    if schema is RemediationProposalDecision:
        ids = bogus if mode == "invalid_ids" else (found_ids[:5] or bogus)
        return {**base, "selected_finding_ids": ids}
    if schema is RemediationRevalidationSelectionDecision:
        if mode == "invalid_ids":
            return {**base, "targeted_test_ids": bogus, "regression_test_ids": bogus}
        return {**base, "targeted_test_ids": found_ids[:3], "regression_test_ids": found_ids[:1]}
    raise ValueError(f"no fake payload builder registered for schema {schema}")


class FakeGeminiClient:
    """Duck-typed stand-in for GeminiDecisionClient. `modes` maps a decision_type
    to either 'valid' (default), 'invalid_ids', 'waive', 'needs_more', or
    'fail:<FAILURE_CATEGORY>' to simulate a call failure."""

    def __init__(self, modes: dict[str, str] | None = None):
        self.modes = modes or {}
        self.calls: list[str] = []

    def decide(self, decision_type, schema, system_instruction, prompt):
        self.calls.append(decision_type)
        mode = self.modes.get(decision_type, "valid")
        now = datetime.now(UTC).isoformat()

        if mode.startswith("fail:"):
            category = mode.split(":", 1)[1]
            evidence = GeminiInvocationEvidence(
                invocation_id=f"GEM-FAKE-{len(self.calls)}",
                model_id="gemini-3.7-flash",
                decision_type=decision_type,
                started_at=now,
                ended_at=now,
                latency_ms=1.0,
                success=False,
                failure_category=category,
            )
            return None, evidence

        found_ids = _ID_PATTERN.findall(prompt)
        payload = _payload_for(schema, found_ids, mode)
        decision = schema.model_validate(payload)
        evidence = GeminiInvocationEvidence(
            invocation_id=f"GEM-FAKE-{len(self.calls)}",
            model_id="gemini-3.7-flash",
            decision_type=decision_type,
            started_at=now,
            ended_at=now,
            latency_ms=2.5,
            success=True,
            schema_valid=True,
            rationale=decision.rationale,
            confidence=decision.confidence,
            needs_human_review=decision.needs_human_review,
        )
        return decision, evidence


def _defective_mission(mission_id: str = "MIS-GEM-01") -> AssuranceMission:
    return AssuranceMission(
        mission_id=mission_id,
        name="Gemini Supervisor Test Mission",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Target"),
    )


def _run_defective(fake_client, mission_id: str = "MIS-GEM-01") -> AssuranceResultV2:
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store, gemini_client=fake_client)
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")
    return supervisor.run_mission(_defective_mission(mission_id), left_pkg, right_pkg)


def test_valid_decision_applied_no_false_model_id_and_budget_respected():
    """Covers: valid structured decision applied, model_id never stamped on a
    deterministic action, and the per-mission Gemini call budget is respected."""
    fake = FakeGeminiClient()
    res = _run_defective(fake)

    gemini_actions = [a for a in res.agent_execution.data if a.is_gemini_decision]
    assert gemini_actions, "expected at least one real Gemini decision to be applied"
    for a in gemini_actions:
        assert a.model_id == "gemini-3.7-flash"
        assert a.invocation_id is not None
        assert a.is_fallback is False

    for a in res.agent_execution.data:
        if not a.is_gemini_decision:
            assert a.model_id is None, f"deterministic action {a.action_id} must not carry a model_id"

    assert len(fake.calls) <= MAX_GEMINI_CALLS_PER_MISSION
    assert int(res.ai_runtime["gemini_calls_made"]) <= MAX_GEMINI_CALLS_PER_MISSION
    assert res.ai_runtime["model_status"] == "GEMINI_LIVE_DECISIONS_APPLIED"
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"


def test_unknown_tool_is_rejected_by_registry():
    assert is_known_tool("compare_ipir")
    assert is_known_tool("query_portfolio")
    assert not is_known_tool("delete_production_database")
    assert not is_known_tool("")


def test_fallback_paths_consolidated():
    """Covers: out-of-vocabulary ID rejection, schema-invalid fallback, and
    timeout fallback — all in one mission run, each on a different decision
    point, so the deterministic pipeline still completes correctly around them."""
    fake = FakeGeminiClient(modes={
        "PRIORITIZE_DIFFERENCES": "invalid_ids",
        "PROPOSE_REMEDIATION": "fail:SCHEMA_INVALID",
        "SELECT_REVALIDATION_TESTS": "fail:TIMEOUT",
    })
    res = _run_defective(fake)
    actions_by_type = {a.decision_type: a for a in res.agent_execution.data if a.decision_type}

    assert actions_by_type["PRIORITIZE_DIFFERENCES"].is_fallback
    assert actions_by_type["PRIORITIZE_DIFFERENCES"].fallback_reason == "NO_VALID_IDS_IN_RESPONSE"
    assert actions_by_type["PRIORITIZE_DIFFERENCES"].model_id is None

    assert actions_by_type["PROPOSE_REMEDIATION"].fallback_reason == "SCHEMA_INVALID"
    assert actions_by_type["SELECT_REVALIDATION_TESTS"].fallback_reason == "TIMEOUT"

    # Deterministic pipeline must still complete correctly despite every
    # rejected/failed Gemini response above.
    assert res.experiments.data.total_executed > 0
    assert res.remediation.data is not None
    assert res.revalidation.data is not None
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"


def test_quota_exceeded_fallback_classified_directly():
    """Exercises the QUOTA_EXCEEDED fallback path directly against `_ask_gemini`
    (no full mission run needed — this is pure call/evidence bookkeeping)."""
    store = InMemoryRunStore()
    mission = _defective_mission("MIS-GEM-QUOTA")
    store.create_run(AssuranceRunRecord(run_id=mission.mission_id, status=AssuranceRunStatus.RUNNING))

    fake = FakeGeminiClient(modes={"PROPOSE_REMEDIATION": "fail:QUOTA_EXCEEDED"})
    supervisor = AssuranceSupervisor(store, gemini_client=fake)
    budget = _InvestigationBudget()

    decision, evidence = supervisor._ask_gemini(
        mission.mission_id, budget, "PROPOSE_REMEDIATION", RemediationProposalDecision, "sys", "prompt"
    )
    assert decision is None
    assert evidence.success is False
    assert evidence.failure_category == "QUOTA_EXCEEDED"
    assert budget.any_gemini_attempted is True
    assert budget.any_gemini_success is False


def test_ask_gemini_makes_no_attempt_once_budget_is_exhausted():
    store = InMemoryRunStore()
    mission = _defective_mission("MIS-GEM-BUDGET")
    store.create_run(AssuranceRunRecord(run_id=mission.mission_id, status=AssuranceRunStatus.RUNNING))

    fake = FakeGeminiClient()
    supervisor = AssuranceSupervisor(store, gemini_client=fake)
    budget = _InvestigationBudget(gemini_call_count=MAX_GEMINI_CALLS_PER_MISSION)

    decision, evidence = supervisor._ask_gemini(
        mission.mission_id, budget, "PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt"
    )
    assert decision is None
    assert evidence is None
    assert fake.calls == []


def test_probe_round_bounded_and_no_duplicate_probes():
    fake = FakeGeminiClient(modes={"EVIDENCE_SUFFICIENCY": "needs_more"})
    res = _run_defective(fake, mission_id="MIS-GEM-PROBE")

    experiment_ids = [e.experiment_id for e in res.experiments.data.experiments]
    assert len(experiment_ids) == len(set(experiment_ids)), "no scenario id should ever be probed twice"

    sufficiency_calls = [c for c in fake.calls if c == "EVIDENCE_SUFFICIENCY"]
    assert len(sufficiency_calls) <= MAX_PROBE_ROUNDS


def test_cancellation_between_stages_halts_execution():
    fake = FakeGeminiClient()
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store, gemini_client=fake)
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    call_count = {"n": 0}

    def cancel_after_two() -> bool:
        call_count["n"] += 1
        return call_count["n"] > 2

    res = supervisor.run_mission(
        _defective_mission("MIS-GEM-CANCEL"), left_pkg, right_pkg, cancellation_check=cancel_after_two
    )
    assert res.overall_status == "CANCELLED"


def test_no_pass_without_mandatory_evidence():
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store, gemini_client=FakeGeminiClient())

    result = AssuranceResultV2(mission_id="X", mode="RELEASE_CONFORMANCE", overall_status="RUNNING")
    result.validation = SectionResult(status=AnalysisStatus.SUCCEEDED, data=[])
    result.experiments = SectionResult(status=AnalysisStatus.NOT_RUN)
    assert supervisor._mandatory_evidence_ok(result) is False

    result.experiments = SectionResult(status=AnalysisStatus.SUCCEEDED, data=None)
    assert supervisor._mandatory_evidence_ok(result) is True


def test_gemini_disabled_deterministic_pipeline_still_operates():
    disabled_client = GeminiDecisionClient(AgentConfig(agent_enabled=False))
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store, gemini_client=disabled_client)
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    res = supervisor.run_mission(_defective_mission("MIS-GEM-DISABLED"), left_pkg, right_pkg)

    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert res.ai_runtime["model_status"] == "DETERMINISTIC_FALLBACK_GEMINI_UNAVAILABLE"
    for a in res.agent_execution.data:
        assert a.is_gemini_decision is False
        assert a.model_id is None


def test_remediation_package_is_immutable_and_isolated():
    rem_service = RemediationService()
    intent = resolve_demo_package("AZ_HO3_2026_09")
    defective = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")
    original_intent_id = intent.id

    proposal = rem_service.generate_remediation_proposal(intent, defective, [])
    assert proposal.derived_package_id != intent.id
    assert proposal.derived_package_id != defective.id

    reval = rem_service.revalidate_remediation(intent, defective, proposal)
    assert intent.id == original_intent_id  # original source package untouched
    assert reval.new_release_decision == "PASS"


def test_remediation_revalidation_reruns_targeted_and_regression_scenarios():
    rem_service = RemediationService()
    intent = resolve_demo_package("AZ_HO3_2026_09")
    defective = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")
    proposal = rem_service.generate_remediation_proposal(intent, defective, [])

    diff_res = SemanticDiffEngine().compare_packages(intent, defective)
    impact = PricingImpactEngine().analyze(diff_res, intent)
    test_plan = RiskDirectedTestGenerator().generate_plan(intent, diff_res, impact)
    scenario = test_plan.candidate_scenarios[0]

    reval = rem_service.revalidate_remediation(
        intent, defective, proposal,
        scenario_pool=test_plan.candidate_scenarios,
        targeted_scenario_ids=[scenario.id],
        regression_scenario_ids=[scenario.id],
    )
    assert reval.targeted_tests_rerun == 1
    assert reval.targeted_tests_passed == 1
    assert reval.regression_tests_rerun == 1
    assert reval.regression_tests_passed == 1
