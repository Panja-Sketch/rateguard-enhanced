"""End-to-end Release Conformance proof through the REAL API surface: upload
+ compile a Controlled Workbook v1 file, create a mission with a connector-
backed Source B, dispatch it through the real Pub/Sub push endpoint (the
same synchronous-worker path every other async-stabilization test in this
suite uses), and assert on the final `GET /missions/{id}` result.

Covers the Session 4 CP9 acceptance requirements:
- clean workbook vs canonical-v1 connector -> PASS
- defective workbook vs defective-v1 connector -> BLOCK_DEPLOYMENT,
  $700.00 vs $655.00, exact first-divergent-node
- unsupported workbook -> compile rejected (400), mission never created

Test-generation seeding note (see docs/implementation/DECISIONS.md D8): the
generic, fully-automatic deterministic boundary-scenario generator derives
its baseline probe from each input's own declared range (the present bound,
or a midpoint) — for this workbook's `roof_age` input (`minimum=0`, no
declared `maximum`), that baseline is `roof_age=0`, not the workbook's own
embedded golden control case (`roof_age=25`). This is a real, documented
scoping limitation of the automatic path for inputs with an open-ended
range, not a defect in the mismatch/decision logic itself (which is
identical regardless of which risk value is probed). To assert the LOCKED
golden dollar figures specifically (as opposed to "the mismatch/PASS logic
is correct for whatever value is probed", which the automatic path already
proves), this test pins the probed scenario to the workbook's own declared
golden control case via `RiskDirectedTestGenerator.generate_plan` — the
same seeding approach used by `tests/agents/test_supervisor_connector_path.py`
for the same documented reason.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.connectors.client import ConnectorClient
from app.engines.testing.models import PricingTestPlan, PricingTestScenario, ScenarioClassification
from app.main import app
from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome
from rating_engine.main import app as real_rating_engine_app

client = TestClient(app)

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "workbook_v1"


def _golden_scenario() -> PricingTestScenario:
    return PricingTestScenario(
        id="RG_GOLDEN_E2E",
        name="Locked Golden Case (roof_age=25)",
        risk_values={"roof_age": 25, "dwelling_limit": "300000.00", "effective_date": "2026-10-01"},
        effective_date="2026-10-01",
        classification=ScenarioClassification.CONTROL,
        purpose="Reproduce the locked golden case end-to-end via the real API.",
    )


def _seeded_plan(pkg_id: str) -> PricingTestPlan:
    scenario = _golden_scenario()
    return PricingTestPlan(
        package_id=pkg_id, candidate_count=1, selected_count=1,
        selected_scenarios=[scenario], candidate_scenarios=[scenario],
        coverage_metrics={"candidate_reduction_pct": 0.0},
    )


def _upload_and_compile_workbook(path: Path) -> tuple[str, str]:
    with path.open("rb") as f:
        upload_res = client.post(
            "/api/v1/sources",
            files={"file": (path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert upload_res.status_code == 200, upload_res.text
    source_id = upload_res.json()["source_id"]

    compile_res = client.post(f"/api/v1/sources/{source_id}/compile")
    assert compile_res.status_code == 200, compile_res.text
    body = compile_res.json()
    assert body["workbook_compilation_receipt"]["status"] == "VERIFIED"
    return source_id, body["ipir_package_id"]


def _create_and_run_connector_mission(source_id: str, compiled_package_id: str, engine_version: str) -> dict:
    payload = {
        "name": "Workbook vs Connector E2E Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "az_ho3",
        "jurisdiction": "Arizona",
        "effective_period_start": "2026-10-01",
        "source_a": {
            "source_id": source_id, "source_type": "FILE", "name": "Controlled Workbook",
            "compiled_package_id": compiled_package_id,
        },
        "source_b": {
            "source_id": "rating-engine-demo", "source_type": "API_CONNECTOR", "name": "Connector",
            "connector_id": "rating-engine-demo", "engine_version": engine_version,
        },
        "disposable_sample_run": True,
    }
    post_res = client.post("/api/v1/missions", json=payload)
    assert post_res.status_code == 202, post_res.text
    mission_id = post_res.json()["mission_id"]

    job = AssuranceJob(job_id=f"JOB-{mission_id}", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")
    envelope = {
        "message": {
            "data": base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8"),
            "message_id": f"MSG-{mission_id}",
        }
    }

    transport = httpx.ASGITransport(app=real_rating_engine_app)
    with (
        patch("app.agents.supervisor.ConnectorClient", side_effect=lambda: ConnectorClient(transport=transport)),
        patch("app.engines.testing.RiskDirectedTestGenerator.generate_plan", side_effect=lambda pkg, diff, impact: _seeded_plan(pkg.id)),
    ):
        pub_res = client.post("/internal/pubsub/assurance", json=envelope)
    assert pub_res.status_code == 200, pub_res.text
    assert pub_res.json()["status"] == ProcessingOutcome.SUCCEEDED.value

    get_res = client.get(f"/api/v1/missions/{mission_id}")
    assert get_res.status_code == 200
    return get_res.json()


def test_clean_workbook_vs_canonical_connector_passes_end_to_end():
    source_id, package_id = _upload_and_compile_workbook(SAMPLES_DIR / "canonical" / "AZ_HO3_GOLDEN_workbook.xlsx")
    mission = _create_and_run_connector_mission(source_id, package_id, "canonical-v1")

    result = mission["result"]
    assert result["release_decision"]["data"]["status"] == "PASS"
    exp = result["experiments"]["data"]["experiments"][0]
    assert exp["expected_premium"] == "700.00"
    assert exp["actual_premium"] == "700.00"


def test_defective_connector_blocks_deployment_with_golden_figures_end_to_end():
    source_id, package_id = _upload_and_compile_workbook(SAMPLES_DIR / "canonical" / "AZ_HO3_GOLDEN_workbook.xlsx")
    mission = _create_and_run_connector_mission(source_id, package_id, "defective-v1")

    result = mission["result"]
    exp = result["experiments"]["data"]["experiments"][0]
    assert exp["expected_premium"] == "700.00"
    assert exp["actual_premium"] == "655.00"
    assert result["release_decision"]["data"]["status"] == "BLOCK_DEPLOYMENT"
    assert result["reconciliation"]["data"]["first_divergent_node"]
    assert result["reconciliation"]["data"]["root_cause"]["expected_value"] == "700.00"
    assert result["reconciliation"]["data"]["root_cause"]["actual_value"] == "655.00"

    stage_outcomes = result.get("stage_outcomes") or []
    assert stage_outcomes, "stage_outcomes must be present for a completed connector mission"
    from app.models.stages import MISSION_STAGE_ORDER

    recorded = {o["stage"] for o in stage_outcomes}
    assert recorded == {s.value for s in MISSION_STAGE_ORDER}


def test_unsupported_workbook_rejected_before_mission_can_be_created():
    negative_path = SAMPLES_DIR / "negative" / "unsupported_function.xlsx"
    with negative_path.open("rb") as f:
        upload_res = client.post(
            "/api/v1/sources",
            files={"file": (negative_path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert upload_res.status_code == 200
    source_id = upload_res.json()["source_id"]

    compile_res = client.post(f"/api/v1/sources/{source_id}/compile")
    assert compile_res.status_code == 400
    assert "Traceback" not in compile_res.text
