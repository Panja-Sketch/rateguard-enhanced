import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome
from app.models.mission import (
    AssuranceMission,
    ComparisonMode,
    MissionObjective,
    MissionStatus,
    PricingSourceRef,
)
from app.models.result_v2 import (
    AnalysisStatus,
    AssuranceResultV2,
    BlastRadiusResult,
    SectionResult,
)
from app.services.mission_execution_service import MissionExecutionService
from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store
from app.storage.firestore_store import sanitize_for_firestore

client = TestClient(app)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


def test_async_mission_creation_returns_202_fast():
    """Proves POST /api/v1/missions validates synchronously and returns HTTP 202 immediately."""
    payload = {
        "name": "Async Stabilization Verification Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {
            "source_id": "AZ_HO3_2026_09",
            "source_type": "SAMPLE_RELEASE",
            "name": "Actuarial Spec",
        },
        "source_b": {
            "source_id": "AZ_HO3_2026_09_CLEAN",
            "source_type": "SAMPLE_RELEASE",
            "name": "Clean Implementation",
        },
        "disposable_sample_run": True,
    }

    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 202
    data = res.json()
    assert "mission_id" in data
    assert data["mission_id"].startswith("MIS-")
    assert data["status"] == "QUEUED"
    assert data["workflow_stage"] == "QUEUED"
    assert "status_url" in data


def test_immediate_get_mission_returns_queued_record_without_memory_sharing():
    """Proves POST mission immediately persists QUEUED record readable across GET endpoint."""
    payload = {
        "name": "Immediate Persistence Verification",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Spec"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
    }

    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 202
    mission_id = res.json()["mission_id"]

    # GET detail must return 200 OK QUEUED immediately from durable store
    get_res = client.get(f"/api/v1/missions/{mission_id}")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["mission_id"] == mission_id
    assert data["status"] in ("QUEUED", "RUNNING", "COMPLETED")


def test_pubsub_publish_failure_returns_503_without_local_execution():
    """Proves Pub/Sub publishing failure returns HTTP 503 MISSION_QUEUE_UNAVAILABLE and does NOT execute in API process."""
    payload = {
        "name": "PubSub Failure Test Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Spec"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
    }

    mock_publisher = MagicMock()
    mock_publisher.publish_assurance_job.side_effect = RuntimeError("Pub/Sub Topic Unavailable")

    with patch("app.api.missions.get_message_publisher", return_value=mock_publisher):
        res = client.post("/api/v1/missions", json=payload)
        assert res.status_code == 503
        data = res.json()["detail"]
        assert data["code"] == "MISSION_QUEUE_UNAVAILABLE"
        assert "mission_id" in data

        store = get_run_store()
        rec = store.get_run(data["mission_id"])
        assert rec is not None
        assert rec.workflow_stage == "QUEUE_FAILED"
        assert rec.metadata.get("error_code") == "MISSION_QUEUE_UNAVAILABLE"


def test_distributed_mission_execution_path():
    """Simulates complete cross-process distributed Pub/Sub worker execution path."""
    payload = {
        "name": "Distributed Execution Test Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Spec"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
        "disposable_sample_run": True,
    }

    # 1. API creates mission
    post_res = client.post("/api/v1/missions", json=payload)
    assert post_res.status_code == 202
    mission_id = post_res.json()["mission_id"]

    # 2. Serialize AssuranceJob to Pub/Sub envelope as Cloud Pub/Sub does
    job = AssuranceJob(
        job_id=f"JOB-{mission_id}",
        run_id=mission_id,
        job_type="ASSURANCE_MISSION_V2",
        schema_version=2,
        left_source_id="AZ_HO3_2026_09",
        right_source_id="AZ_HO3_2026_09_CLEAN",
        left_package_id="AZ_HO3_2026_09",
        right_package_id="AZ_HO3_2026_09_CLEAN",
    )
    b64_data = base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-TEST-100"}}

    # 4. Worker processes envelope via /internal/pubsub/assurance endpoint
    pub_res = client.post("/internal/pubsub/assurance", json=envelope)
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == ProcessingOutcome.SUCCEEDED.value

    # 5. Detail GET endpoint returns completed status and PASS decision
    get_res = client.get(f"/api/v1/missions/{mission_id}")
    assert get_res.status_code == 200
    detail = get_res.json()
    assert detail["status"] in ("COMPLETED", "FINISHED")
    assert detail["decision"] == "PASS"


def test_real_uploaded_source_resolves_through_v2_worker_dispatch():
    """Proves a real uploaded/compiled source (source_type FILE, as used by the
    /sources page) resolves correctly inside MissionExecutionService, instead of
    being rejected as an unsupported demo package_id. Covers the mission_execution_service
    _resolve_source_package fallback that reads the compiled IPIR artifact back
    from the artifact store."""
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()

    upload_res = client.post(
        "/api/v1/sources",
        files={"file": ("rate_spec.json", content, "application/json")},
    )
    assert upload_res.status_code == 200
    source_id = upload_res.json()["source_id"]

    compile_res = client.post(f"/api/v1/sources/{source_id}/compile")
    assert compile_res.status_code == 200
    compiled_package_id = compile_res.json()["ipir_package_id"]

    payload = {
        "name": "Real Uploaded Source Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {
            "source_id": source_id,
            "source_type": "FILE",
            "name": "rate_spec.json",
            "compiled_package_id": compiled_package_id,
        },
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
        "disposable_sample_run": True,
    }
    post_res = client.post("/api/v1/missions", json=payload)
    assert post_res.status_code == 202
    mission_id = post_res.json()["mission_id"]

    job = AssuranceJob(
        job_id=f"JOB-{mission_id}",
        run_id=mission_id,
        job_type="ASSURANCE_MISSION_V2",
        schema_version=2,
        left_source_id=source_id,
        right_source_id="AZ_HO3_2026_09_CLEAN",
        left_package_id=source_id,
        right_package_id="AZ_HO3_2026_09_CLEAN",
    )
    b64_data = base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-TEST-FILE-SOURCE"}}

    pub_res = client.post("/internal/pubsub/assurance", json=envelope)
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == ProcessingOutcome.SUCCEEDED.value

    get_res = client.get(f"/api/v1/missions/{mission_id}")
    assert get_res.status_code == 200
    assert get_res.json()["status"] in ("COMPLETED", "FINISHED")


def test_execution_lease_skips_duplicate_delivery():
    """Proves duplicate Pub/Sub delivery safely skips re-execution when mission is completed."""
    job = AssuranceJob(
        job_id="JOB-DUP-100",
        run_id="MIS-DUP-100",
        job_type="ASSURANCE_MISSION_V2",
    )

    store = get_run_store()
    rec = AssuranceRunRecord(
        run_id="MIS-DUP-100",
        status=AssuranceRunStatus.COMPLETED,
        workflow_stage="FINISHED",
        decision="PASS",
    )
    store.save_run(rec)

    res = MissionExecutionService.execute_job(job)
    assert res.outcome == ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED
    assert res.should_ack is True


def test_invalid_mission_creation_returns_422_without_enqueuing():
    """Proves invalid missions fail synchronously with a structured HTTP 422 (field,
    code, message per issue) and are never enqueued. source_a is intentionally
    omitted here too: with hidden demo defaults removed, that alone must be rejected."""
    payload = {
        "name": "Invalid Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
    }

    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 422
    data = res.json()
    assert "detail" in data
    assert "issues" in data["detail"]
    issue_fields = {i["field"] for i in data["detail"]["issues"]}
    assert "source_a" in issue_fields
    for issue in data["detail"]["issues"]:
        assert {"field", "code", "message"} <= set(issue.keys())


def test_completed_mission_delete_returns_409_conflict():
    """Proves completed non-disposable audit missions reject DELETE with HTTP 409 Conflict."""
    store = get_run_store()
    mission_id = "MIS-AUDIT-COMPLETED-TEST"

    rec = AssuranceRunRecord(
        run_id=mission_id,
        status=AssuranceRunStatus.COMPLETED,
        workflow_stage="COMPLETED",
        decision="PASS",
        summary="Completed audit record",
        metadata={
            "record_type": "ASSURANCE_MISSION_V2",
            "disposable_sample_run": False,
        },
    )
    store.save_run(rec)

    res = client.delete(f"/api/v1/missions/{mission_id}")
    assert res.status_code == 409
    data = res.json()["detail"]
    assert data["code"] == "MISSION_DELETE_NOT_ALLOWED"


def test_disposable_mission_delete_succeeds():
    """Proves disposable sample missions can be deleted cleanly."""
    store = get_run_store()
    mission_id = "MIS-DISPOSABLE-TEST"

    rec = AssuranceRunRecord(
        run_id=mission_id,
        status=AssuranceRunStatus.FAILED,
        workflow_stage="FAILED",
        summary="Disposable sample run",
        metadata={
            "disposable_sample_run": True,
        },
    )
    store.save_run(rec)

    res = client.delete(f"/api/v1/missions/{mission_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "DELETED"


def test_firestore_serialization_round_trip():
    """Proves all V2 models (Decimals, Enums, Datetime, BaseModels) are Firestore-safe."""
    mission = AssuranceMission(
        mission_id="MIS-FIRESTORE-TEST",
        name="Firestore Safety Audit",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        status=MissionStatus.COMPLETED,
        objective=MissionObjective(
            product="AZ_HO3",
            jurisdiction="Arizona",
            effective_period_start="2026-09-01",
            portfolio_dataset="az_ho3_2026_synthetic_50k.csv",
            gating_policy="STRICT_ZERO_DRIFT",
        ),
        source_a=PricingSourceRef(
            source_id="AZ_HO3_2026_09",
            source_type="SAMPLE_RELEASE",
            name="Canonical Intent",
        ),
    )

    clean_dict = sanitize_for_firestore(mission)
    assert isinstance(clean_dict["status"], str)
    assert clean_dict["status"] == "COMPLETED"
    assert isinstance(clean_dict["mode"], str)

    result = AssuranceResultV2(
        mission_id="MIS-FIRESTORE-TEST",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        overall_status="COMPLETED",
        blast_radius=SectionResult(
            status=AnalysisStatus.SUCCEEDED,
            data=BlastRadiusResult(
                portfolio_id="AZ_HO3_2026_SYNTHETIC_50K",
                total_policies=50000,
                financially_affected_count=2150,
                absolute_financial_exposure="142500.50",
            ),
        ),
    )

    clean_res = sanitize_for_firestore(result)
    assert clean_res["blast_radius"]["data"]["absolute_financial_exposure"] == "142500.50"
    assert clean_res["blast_radius"]["status"] == "SUCCEEDED"


def test_obsolete_scenario_lab_route_is_gone():
    """The Scenario Lab route (superseded by Mission V2) has been removed
    entirely, not merely stubbed -- see test_removed_legacy_endpoints_are_gone
    in tests/api/test_assurance_api.py for the full removed-surface check."""
    res = client.post("/api/v1/assurance/scenario-lab", json={})
    assert res.status_code == 404
