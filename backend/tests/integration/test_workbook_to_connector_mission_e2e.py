"""REAL-PATH end-to-end proof through the API surface: upload + compile a
Controlled Workbook v1, create a connector-backed Release Conformance mission,
dispatch it through the real Pub/Sub push endpoint, and assert on the final
`GET /missions/{id}` result.

Unlike the earlier version of this file, **nothing here replaces the test
planner**: the probes that run are the ones the real generator derives from the
compiled workbook (its verified control cases, plus boundary probes mined from
the compiled tables). The only stand-in is the network transport to the demo
rating engine (`httpx.ASGITransport` wrapping the real `rating_engine` app, so
real HTTP/Pydantic semantics with no socket).

The seeded-plan variants that isolate supervisor decision logic live in
`tests/agents/test_supervisor_connector_path.py` and are explicitly *not*
real-path evidence.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import openpyxl
from fastapi.testclient import TestClient

from app.connectors.client import ConnectorClient
from app.main import app
from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome
from rating_engine.main import app as real_rating_engine_app

client = TestClient(app)

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "workbook_v1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CANONICAL = SAMPLES_DIR / "canonical" / "AZ_HO3_GOLDEN_workbook.xlsx"


def _upload_and_compile(content: bytes, name: str = "workbook.xlsx") -> tuple[str, str, dict]:
    upload = client.post("/api/v1/sources", files={"file": (name, io.BytesIO(content), XLSX)})
    assert upload.status_code == 200, upload.text
    source_id = upload.json()["source_id"]
    compiled = client.post(f"/api/v1/sources/{source_id}/compile")
    assert compiled.status_code == 200, compiled.text
    return source_id, compiled.json()["ipir_package_id"], compiled.json()


def _canonical_bytes() -> bytes:
    return CANONICAL.read_bytes()


def _workbook_with_control_case_date(iso_date: str) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(_canonical_bytes()))
    ws = wb["RG_CONTROL_CASES"]
    for row in ws.iter_rows(min_row=2):
        payload = json.loads(row[1].value)
        payload["effective_date"] = iso_date
        row[1].value = json.dumps(payload)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _run_mission(source_id: str, package_id: str, engine_version: str, *, client_factory=None) -> dict:
    payload = {
        "name": "Workbook vs Connector Real-Path Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "az_ho3",
        "jurisdiction": "Arizona",
        "effective_period_start": "2026-10-01",
        "source_a": {
            "source_id": source_id, "source_type": "FILE", "name": "Controlled Workbook",
            "compiled_package_id": package_id,
        },
        "source_b": {
            "source_id": "rating-engine-demo", "source_type": "API_CONNECTOR", "name": "Connector",
            "connector_id": "rating-engine-demo", "engine_version": engine_version,
        },
        "disposable_sample_run": True,
    }
    post = client.post("/api/v1/missions", json=payload)
    assert post.status_code == 202, post.text
    mission_id = post.json()["mission_id"]

    job = AssuranceJob(job_id=f"JOB-{mission_id}", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")
    envelope = {
        "message": {
            "data": base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8"),
            "message_id": f"MSG-{mission_id}",
        }
    }
    transport = httpx.ASGITransport(app=real_rating_engine_app)
    factory = client_factory or (lambda: ConnectorClient(transport=transport))
    # NOTE: only the connector's network transport is substituted. The test
    # planner is the real, unpatched `RiskDirectedTestGenerator`.
    with patch("app.agents.supervisor.ConnectorClient", side_effect=factory):
        pushed = client.post("/internal/pubsub/assurance", json=envelope)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["status"] == ProcessingOutcome.SUCCEEDED.value

    got = client.get(f"/api/v1/missions/{mission_id}")
    assert got.status_code == 200
    body = got.json()
    body["_connector_evidence"] = client.get(f"/api/v1/missions/{mission_id}/connector-evidence").json()
    return body


def _experiments(mission: dict) -> list[dict]:
    return mission["result"]["experiments"]["data"]["experiments"]


# ----------------------------------------------------------------- PASS / BLOCK


def test_clean_canonical_workbook_vs_clean_connector_passes_on_the_real_path():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(source_id, package_id, "canonical-v1")

    result = mission["result"]
    assert result["release_decision"]["data"]["status"] == "PASS"
    experiments = _experiments(mission)
    assert len(experiments) > 1, "the real planner must produce more than a single probe"
    assert all(e["outcome"] == "MATCH" and e["matches"] for e in experiments)
    assert result["experiments"]["data"]["mismatch_count"] == 0
    assert result["experiments"]["data"]["inconclusive_count"] == 0

    control = [e for e in experiments if e["probe_origin"] == "CONTROL_CASE"]
    assert len(control) == 1
    assert control[0]["expected_premium"] == "700.00" and control[0]["actual_premium"] == "700.00"
    assert control[0]["risk_inputs"]["roof_age"] == 25


def test_defective_connector_blocks_and_a_roof_age_21_plus_probe_was_generated_and_executed():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(source_id, package_id, "defective-v1")

    result = mission["result"]
    assert result["release_decision"]["data"]["status"] == "BLOCK_DEPLOYMENT"

    experiments = _experiments(mission)
    executed_21_plus = [e for e in experiments if int(e["risk_inputs"]["roof_age"]) >= 21]
    assert executed_21_plus, "a probe reaching roof_age >= 21 must have been generated AND executed"
    # The locked drift: 700.00 (approved) vs 655.00 (defective) at roof_age >= 21.
    assert any(
        e["expected_premium"] == "700.00" and e["actual_premium"] == "655.00" and e["outcome"] == "MISMATCH"
        for e in executed_21_plus
    )
    # Boundary neighbours of the roof_age >= 21 tier were exercised too.
    assert {20, 21, 22} <= {int(e["risk_inputs"]["roof_age"]) for e in experiments}
    # Below the changed tier the defective engine agrees, proving the probe set separates the two.
    assert any(int(e["risk_inputs"]["roof_age"]) <= 20 and e["outcome"] == "MATCH" for e in experiments)

    recon = result["reconciliation"]["data"]
    assert recon["root_cause"]["expected_value"] == "700.00" and recon["root_cause"]["actual_value"] == "655.00"
    assert "probe origin" in recon["root_cause"]["explanation"]
    assert "calculation date 2026-10-01" in recon["root_cause"]["explanation"]

    from app.models.stages import MISSION_STAGE_ORDER

    assert {o["stage"] for o in result["stage_outcomes"]} == {s.value for s in MISSION_STAGE_ORDER}


# ------------------------------------------------------------- dates + evidence


def test_package_effective_date_is_carried_to_oracle_connector_and_evidence():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(source_id, package_id, "canonical-v1")

    for e in _experiments(mission):
        assert e["calculation_date"] == "2026-10-01"
        assert e["calculation_date_source"] in {"PACKAGE_EFFECTIVE_START", "CONTROL_CASE"}
    invocations = mission["_connector_evidence"]["connector_invocations"]
    assert invocations and all(i["calculation_date"] == "2026-10-01" for i in invocations)


def test_evidence_identifies_probe_origin_and_calculation_date_for_every_probe():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(source_id, package_id, "defective-v1")

    experiments = _experiments(mission)
    assert {e["probe_origin"] for e in experiments} >= {"CONTROL_CASE", "BOUNDARY"}
    assert all(e["probe_provenance"] for e in experiments)
    invocations = mission["_connector_evidence"]["connector_invocations"]
    assert len(invocations) == len(experiments)
    assert {i["probe_origin"] for i in invocations} >= {"CONTROL_CASE", "BOUNDARY"}
    assert all(i["scenario_id"] and i["calculation_date_source"] for i in invocations)


def test_control_case_date_takes_precedence_over_package_start_end_to_end():
    source_id, package_id, _ = _upload_and_compile(_workbook_with_control_case_date("2026-11-01"))
    mission = _run_mission(source_id, package_id, "canonical-v1")

    assert mission["result"]["release_decision"]["data"]["status"] == "PASS"
    experiments = _experiments(mission)
    assert all(e["calculation_date"] == "2026-11-01" for e in experiments)
    control = next(e for e in experiments if e["probe_origin"] == "CONTROL_CASE")
    assert control["calculation_date_source"] == "CONTROL_CASE"
    assert all(i["calculation_date"] == "2026-11-01" for i in mission["_connector_evidence"]["connector_invocations"])


def test_out_of_period_control_case_date_fails_safely_never_passes():
    """A control case dated before the package starts cannot verify, is not used
    as a probe, and (with no declared defaults to fall back on) leaves no
    executable probe -- so the mission requires review instead of passing."""
    source_id, package_id, compiled = _upload_and_compile(_workbook_with_control_case_date("2026-09-15"))
    assert compiled["workbook_compilation_receipt"]["status"] == "REVIEW_REQUIRED"
    mission = _run_mission(source_id, package_id, "canonical-v1")

    decision = mission["result"]["release_decision"]["data"]
    assert decision["status"] == "REVIEW_REQUIRED"
    assert decision["status"] != "PASS"
    assert mission["_connector_evidence"]["connector_invocation_count"] == 0


# ----------------------------------------------------- connector failure semantics


def _failing_factory(handler):
    def factory():
        return ConnectorClient(transport=httpx.MockTransport(handler), sleep_fn=_no_sleep)

    return factory


async def _no_sleep(_seconds: float) -> None:
    return None


def test_unauthenticated_connector_yields_review_required_not_block_deployment():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(
        source_id, package_id, "canonical-v1",
        client_factory=_failing_factory(lambda request: httpx.Response(403, json={"detail": "Forbidden"})),
    )
    decision = mission["result"]["release_decision"]["data"]
    assert decision["status"] == "REVIEW_REQUIRED"
    assert mission["result"]["experiments"]["data"]["mismatch_count"] == 0
    assert mission["result"]["experiments"]["data"]["inconclusive_count"] >= 1
    assert all(e["outcome"] == "INCONCLUSIVE" for e in _experiments(mission))


def test_malformed_connector_response_yields_review_required_not_block_deployment():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    mission = _run_mission(
        source_id, package_id, "canonical-v1",
        client_factory=_failing_factory(lambda request: httpx.Response(200, text="this is not json")),
    )
    assert mission["result"]["release_decision"]["data"]["status"] == "REVIEW_REQUIRED"


def test_internal_unhandled_failure_is_failed_not_a_release_decision():
    """An unexpected internal error is a technical FAILED state with no release
    decision -- distinct from proven mismatch (BLOCK_DEPLOYMENT), proven
    equivalence (PASS) and inconclusive evidence (REVIEW_REQUIRED)."""
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    payload = {
        "name": "Internal failure", "mode": "RELEASE_CONFORMANCE", "product": "az_ho3",
        "jurisdiction": "Arizona", "effective_period_start": "2026-10-01",
        "source_a": {"source_id": source_id, "source_type": "FILE", "name": "Controlled Workbook",
                     "compiled_package_id": package_id},
        "source_b": {"source_id": "rating-engine-demo", "source_type": "API_CONNECTOR", "name": "Connector",
                     "connector_id": "rating-engine-demo", "engine_version": "canonical-v1"},
        "disposable_sample_run": True,
    }
    mission_id = client.post("/api/v1/missions", json=payload).json()["mission_id"]
    job = AssuranceJob(job_id=f"JOB-{mission_id}", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")
    envelope = {"message": {
        "data": base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8"),
        "message_id": f"MSG-{mission_id}",
    }}
    with patch("app.agents.supervisor.AssuranceSupervisor.run_mission", side_effect=RuntimeError("simulated internal bug")):
        pushed = client.post("/internal/pubsub/assurance", json=envelope)
    assert pushed.json()["status"] == ProcessingOutcome.RETRYABLE_FAILURE.value

    mission = client.get(f"/api/v1/missions/{mission_id}").json()
    assert mission["status"] == "FAILED"
    assert mission["decision"] is None


def test_unsupported_workbook_rejected_before_mission_can_be_created():
    negative_path = SAMPLES_DIR / "negative" / "unsupported_function.xlsx"
    with negative_path.open("rb") as f:
        upload_res = client.post("/api/v1/sources", files={"file": (negative_path.name, f, XLSX)})
    assert upload_res.status_code == 200
    source_id = upload_res.json()["source_id"]

    compile_res = client.post(f"/api/v1/sources/{source_id}/compile")
    assert compile_res.status_code == 400
    assert "Traceback" not in compile_res.text
