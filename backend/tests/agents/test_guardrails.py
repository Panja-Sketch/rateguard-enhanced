"""Operational guardrails are read from the canonical RATEGUARD_* variables at
runtime and actually bound behaviour: Gemini call cap, probe-round cap, and the
low-confidence review threshold (which can never yield PASS)."""

import pytest

from app.adapters.models import SourceDescriptor, SourceFormat
from app.agents.config import AgentConfig
from app.agents.decision_schemas import DifferencePrioritizationDecision
from app.agents.supervisor import AssuranceSupervisor, _InvestigationBudget
from app.api.assurance import resolve_demo_package
from app.core.runtime_config import RuntimeConfigError, resolve_guardrails
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.storage.memory_store import InMemoryRunStore
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus
from tests.agents.test_extraction_orchestration import _DATA_DIR
from tests.agents.test_gemini_supervisor import FakeGeminiClient, _defective_mission

CALLS = "RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION"
ROUNDS = "RATEGUARD_MAX_PROBE_ROUNDS"
THRESHOLD = "RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD"


def _run_defective(fake, mission_id):
    supervisor = AssuranceSupervisor(InMemoryRunStore(), gemini_client=fake)
    return supervisor.run_mission(
        _defective_mission(mission_id),
        resolve_demo_package("AZ_HO3_2026_09"),
        resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE"),
    )


# ---- the variables are actually read by runtime code ----------------------


def test_defaults_preserve_previous_behaviour(monkeypatch):
    for name in (CALLS, ROUNDS, THRESHOLD):
        monkeypatch.delenv(name, raising=False)
    cfg = AgentConfig()
    assert (cfg.max_gemini_calls_per_mission, cfg.max_probe_rounds, cfg.low_confidence_review_threshold) == (6, 1, 0.60)


def test_supervisor_reads_the_environment(monkeypatch):
    monkeypatch.setenv(CALLS, "3")
    monkeypatch.setenv(ROUNDS, "2")
    monkeypatch.setenv(THRESHOLD, "0.85")
    sup = AssuranceSupervisor(InMemoryRunStore(), gemini_client=FakeGeminiClient())
    assert sup.agent_config.max_gemini_calls_per_mission == 3
    assert sup.agent_config.max_probe_rounds == 2
    assert sup.agent_config.low_confidence_review_threshold == 0.85


@pytest.mark.parametrize(
    ("name", "value"),
    [(CALLS, "0"), (CALLS, "51"), (CALLS, "abc"), (ROUNDS, "-1"), (ROUNDS, "11"), (THRESHOLD, "0.4"), (THRESHOLD, "1.1"), (THRESHOLD, "x")],
)
def test_out_of_range_values_are_rejected_at_startup_and_by_config(name, value):
    with pytest.raises(RuntimeConfigError, match=name):
        resolve_guardrails({name: value})
    with pytest.raises(ValueError):
        # AgentConfig pulls from the process environment; patch it directly.
        import os

        os.environ[name] = value
        try:
            AgentConfig()
        finally:
            del os.environ[name]


def test_valid_guardrails_resolve_and_defaults_apply():
    assert resolve_guardrails({}) == {CALLS: 6, ROUNDS: 1, THRESHOLD: 0.60}
    assert resolve_guardrails({CALLS: "10", ROUNDS: "3", THRESHOLD: "0.8"}) == {CALLS: 10, ROUNDS: 3, THRESHOLD: 0.8}


@pytest.mark.parametrize("legacy", ["MAX_GEMINI_CALLS_PER_MISSION", "MAX_PROBE_ROUNDS", "LOW_CONFIDENCE_REVIEW_THRESHOLD"])
def test_conflicting_legacy_names_are_rejected(legacy):
    from app.core.runtime_config import resolve_ai_runtime_config

    good = {"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite", "VERTEX_AI_LOCATION": "us"}
    with pytest.raises(RuntimeConfigError, match=legacy):
        resolve_ai_runtime_config({**good, legacy: "5"})


def test_startup_summary_reports_effective_guardrails():
    from app.core.config import Settings
    from app.core.startup_checks import validate_startup_configuration

    env = {"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite", "VERTEX_AI_LOCATION": "us", CALLS: "10", ROUNDS: "3", THRESHOLD: "0.8"}
    summary = validate_startup_configuration(Settings(firebase_project_id="p"), env)
    assert summary["guardrails"] == {CALLS: 10, ROUNDS: 3, THRESHOLD: 0.8}
    with pytest.raises(RuntimeConfigError):
        validate_startup_configuration(Settings(firebase_project_id="p"), {**env, CALLS: "999"})


# ---- Gemini calls cannot exceed the mission limit --------------------------


def test_ask_gemini_stops_at_the_configured_limit(monkeypatch):
    monkeypatch.setenv(CALLS, "2")
    store = InMemoryRunStore()
    store.create_run(AssuranceRunRecord(run_id="MIS-CAP", status=AssuranceRunStatus.RUNNING))
    fake = FakeGeminiClient()
    sup = AssuranceSupervisor(store, gemini_client=fake)
    budget = _InvestigationBudget()
    outcomes = [
        sup._ask_gemini("MIS-CAP", budget, "PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "s", "p")
        for _ in range(5)
    ]
    assert len(fake.calls) == 2
    assert budget.gemini_call_count == 2
    assert [d is not None for d, _ in outcomes] == [True, True, False, False, False]
    assert all(ev is None for _, ev in outcomes[2:])


def test_full_mission_never_exceeds_a_tight_call_limit_and_never_probes_when_rounds_are_zero(monkeypatch):
    monkeypatch.setenv(CALLS, "2")
    monkeypatch.setenv(ROUNDS, "0")
    fake = FakeGeminiClient(modes={"EVIDENCE_SUFFICIENCY": "needs_more"})
    res = _run_defective(fake, "MIS-CAP-FULL")
    assert len(fake.calls) <= 2
    assert int(res.ai_runtime["gemini_calls_made"]) <= 2
    assert "EVIDENCE_SUFFICIENCY" not in fake.calls  # zero probe rounds configured
    # The deterministic pipeline is unaffected by the cap: still blocks the defect.
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"


# ---- probe rounds cannot exceed the configured limit -----------------------


def test_probe_rounds_are_bounded_by_the_configured_limit(monkeypatch):
    monkeypatch.setenv(CALLS, "20")
    monkeypatch.setenv(ROUNDS, "2")
    fake = FakeGeminiClient(modes={"EVIDENCE_SUFFICIENCY": "needs_more"})
    res = _run_defective(fake, "MIS-ROUNDS")
    assert fake.calls.count("EVIDENCE_SUFFICIENCY") <= 2
    experiment_ids = [e.experiment_id for e in res.experiments.data.experiments]
    assert len(experiment_ids) == len(set(experiment_ids))
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"


# ---- low-confidence extraction cannot produce PASS -------------------------


def _confident_pdf_extraction():
    """Legacy PDF path (test-only reachability) that yields confidence 0.95."""
    from tests.agents.test_extraction_orchestration import FixedExtractorGeminiClient

    desc = SourceDescriptor(source_id="SRC-PDF-GUARD", name="spec.pdf", source_type=SourceFormat.PDF, format="pdf", storage_uri="x")
    content = (_DATA_DIR / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf").read_bytes()
    sup = AssuranceSupervisor(InMemoryRunStore(), gemini_client=FixedExtractorGeminiClient("pdf_structured_section_extractor"))
    return sup.extract_and_compile_source(desc, content)


def test_threshold_from_environment_forces_review_of_an_otherwise_accepted_extraction(monkeypatch):
    monkeypatch.delenv(THRESHOLD, raising=False)
    baseline = _confident_pdf_extraction()
    assert baseline.confidence == 0.95 and baseline.requires_human_review is False

    monkeypatch.setenv(THRESHOLD, "0.96")
    assert _confident_pdf_extraction().requires_human_review is True
    monkeypatch.setenv(THRESHOLD, "0.95")  # boundary: not strictly below the threshold
    assert _confident_pdf_extraction().requires_human_review is False


def test_low_confidence_extraction_can_never_produce_pass(monkeypatch):
    """Extraction below the threshold is flagged; a flagged source downgrades an
    otherwise perfectly matching mission to REVIEW_REQUIRED, never PASS."""
    monkeypatch.setenv(THRESHOLD, "0.96")
    extracted = _confident_pdf_extraction()
    assert extracted.requires_human_review is True

    mission = AssuranceMission(
        mission_id="MIS-LOWCONF-GUARD",
        name="Low confidence guard",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(
            source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="A",
            requires_human_review=extracted.requires_human_review,
        ),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="B"),
    )
    res = AssuranceSupervisor(InMemoryRunStore()).run_mission(
        mission, resolve_demo_package("AZ_HO3_2026_09"), resolve_demo_package("AZ_HO3_2026_09_CLEAN")
    )
    assert res.semantic_analysis.data.difference_count == 0
    assert res.release_decision.data.status == "REVIEW_REQUIRED"
    assert res.release_decision.data.status != "PASS"
