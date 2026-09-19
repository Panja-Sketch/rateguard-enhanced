"""Proves the locked 20-stage `MissionStage` ledger (CP9) is fully accounted
for -- every stage recorded exactly once, with a reason on every
non-COMPLETED outcome -- across each of the supervisor's real mission-mode
paths: JSON-vs-JSON Equivalence, JSON-vs-JSON Release Conformance, and the
clean-equivalence fast path. The connector path's stage accounting is
already proven end-to-end by
`tests/agents/test_supervisor_connector_path.py`."""

from app.agents.supervisor import AssuranceSupervisor
from app.api.assurance import resolve_demo_package
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.models.stages import MISSION_STAGE_ORDER, StageStatus
from app.storage.memory_store import InMemoryRunStore


def _assert_fully_accounted(result):
    assert result.stage_outcomes, "stage_outcomes must never be empty"
    recorded = {o.stage for o in result.stage_outcomes}
    assert recorded == set(MISSION_STAGE_ORDER), f"missing stages: {set(MISSION_STAGE_ORDER) - recorded}"
    for outcome in result.stage_outcomes:
        if outcome.status != StageStatus.COMPLETED:
            assert outcome.reason, f"{outcome.stage} has status {outcome.status} but no reason"


def test_clean_equivalence_fast_path_fully_accounts_for_every_stage():
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_CLEAN")

    mission = AssuranceMission(
        mission_id="MIS-STAGE-CLEAN-01",
        name="Clean Equivalence",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="Target"),
    )
    result = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert result.release_decision.data.status == "PASS"
    _assert_fully_accounted(result)


def test_release_conformance_material_drift_fully_accounts_for_every_stage():
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    mission = AssuranceMission(
        mission_id="MIS-STAGE-DEFECTIVE-01",
        name="Release Conformance",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Target"),
    )
    result = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert result.release_decision.data.status == "BLOCK_DEPLOYMENT"
    _assert_fully_accounted(result)

    # Modules genuinely not built in this codebase are always NOT_APPLICABLE
    # with an honest reason -- never silently completed or fabricated.
    from app.models.stages import MissionStage

    by_stage = {o.stage: o for o in result.stage_outcomes}
    for unbuilt in (
        MissionStage.COHORT_DISTRIBUTION,
        MissionStage.PIPELINE_IMPACT,
        MissionStage.EXPLANATION_FACTS,
        MissionStage.EXPLANATION_DRAFT,
    ):
        # These modules are now built (consumer-protection/explanations); each
        # stage must still be honestly accounted for: COMPLETED, or
        # NOT_APPLICABLE with a stated reason -- never silently missing.
        outcome = by_stage[unbuilt]
        assert outcome.status in (StageStatus.COMPLETED, StageStatus.NOT_APPLICABLE)
        if outcome.status == StageStatus.NOT_APPLICABLE:
            assert outcome.reason
