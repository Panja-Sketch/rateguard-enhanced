"""Tests for the on-demand Equivalence-mode alignment-options endpoint and the
supervisor-side PROPOSE_ALIGNMENT_OPTIONS decision it depends on.

Equivalence mode assumes neither Source A nor Source B is authoritative, so
the mission pipeline itself never computes a directional patch -- see
AssuranceSupervisor.run_mission's STAGE 7 branch. A directional patch is only
ever produced here, on demand, after a human explicitly picks a reference."""

import pytest
from fastapi.testclient import TestClient

from app.agents.supervisor import AssuranceSupervisor
from app.api.assurance import resolve_demo_package
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store, reset_run_store

app = __import__("app.main", fromlist=["app"]).app
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_run_store()
    yield
    reset_run_store()


def _seed_completed_equivalence_mission(mission_id: str) -> AssuranceMission:
    """Seeds a COMPLETED Equivalence mission record directly in the store,
    bypassing the async worker (this endpoint's behavior is independent of
    how the mission reached COMPLETED)."""
    mission = AssuranceMission(
        mission_id=mission_id,
        name="Equivalence Alignment Options Test Mission",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Source B"),
    )
    store = get_run_store()
    store.create_run(AssuranceRunRecord(
        run_id=mission_id,
        status=AssuranceRunStatus.COMPLETED,
        metadata={"mission_object": mission.model_dump(mode="json")},
    ))
    return mission


def test_supervisor_equivalence_mode_defers_remediation_and_logs_neutral_decision():
    supervisor = AssuranceSupervisor(get_run_store())
    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    mission = AssuranceMission(
        mission_id="MIS-EQUIV-DEFER-01",
        name="Equivalence Defers Remediation",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Source B"),
    )

    res = supervisor.run_mission(mission, left_pkg, right_pkg)

    assert res.semantic_analysis.data.difference_count > 0
    assert res.remediation.status == "NOT_RUN"
    assert res.revalidation.status == "NOT_RUN"

    decision_types = [a.decision_type for a in (res.agent_execution.data or [])]
    assert "PROPOSE_ALIGNMENT_OPTIONS" in decision_types
    assert "PROPOSE_REMEDIATION" not in decision_types


def test_alignment_options_endpoint_computes_directional_patch_after_reference_choice():
    mission_id = "MIS-ALIGN-API-01"
    _seed_completed_equivalence_mission(mission_id)

    resp_a = client.post(f"/api/v1/missions/{mission_id}/alignment-options", json={"reference": "A"})
    assert resp_a.status_code == 200, resp_a.text
    body_a = resp_a.json()
    assert body_a["reference"] == "A"
    assert body_a["difference_count"] > 0
    assert body_a["remediation"]["derived_package_id"]
    assert float(body_a["revalidation"]["before_absolute_exposure"]) > 0

    resp_b = client.post(f"/api/v1/missions/{mission_id}/alignment-options", json={"reference": "B"})
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()
    assert body_b["reference"] == "B"
    assert body_b["difference_count"] > 0

    # Genuinely direction-dependent, not a cached/hardcoded stub: the two
    # reference directions produce distinct derived package ids.
    assert body_a["remediation"]["derived_package_id"] != body_b["remediation"]["derived_package_id"]


def test_alignment_options_endpoint_rejects_release_conformance_mode():
    mission_id = "MIS-ALIGN-API-RC"
    mission = AssuranceMission(
        mission_id=mission_id,
        name="Release Conformance (should reject alignment-options)",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Target"),
    )
    store = get_run_store()
    store.create_run(AssuranceRunRecord(
        run_id=mission_id,
        status=AssuranceRunStatus.COMPLETED,
        metadata={"mission_object": mission.model_dump(mode="json")},
    ))

    resp = client.post(f"/api/v1/missions/{mission_id}/alignment-options", json={"reference": "A"})
    assert resp.status_code == 400


def test_alignment_options_endpoint_rejects_unfinished_mission():
    mission_id = "MIS-ALIGN-API-RUNNING"
    mission = AssuranceMission(
        mission_id=mission_id,
        name="Still Running",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Source B"),
    )
    store = get_run_store()
    store.create_run(AssuranceRunRecord(
        run_id=mission_id,
        status=AssuranceRunStatus.RUNNING,
        metadata={"mission_object": mission.model_dump(mode="json")},
    ))

    resp = client.post(f"/api/v1/missions/{mission_id}/alignment-options", json={"reference": "A"})
    assert resp.status_code == 409


def test_alignment_options_endpoint_404_on_unknown_mission():
    resp = client.post("/api/v1/missions/MIS-DOES-NOT-EXIST/alignment-options", json={"reference": "A"})
    assert resp.status_code == 404


def test_alignment_options_endpoint_422_on_invalid_reference():
    mission_id = "MIS-ALIGN-API-BADREF"
    _seed_completed_equivalence_mission(mission_id)
    resp = client.post(f"/api/v1/missions/{mission_id}/alignment-options", json={"reference": "C"})
    assert resp.status_code == 422
