import base64

from fastapi.testclient import TestClient

from app.main import app
from app.messaging import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store

client = TestClient(app)


def test_internal_pubsub_worker_endpoint():
    """Tests POST /internal/pubsub/assurance decoding a real Pub/Sub push
    message end to end for a genuine Mission V2 'MIS-*' job."""
    mission = AssuranceMission(
        mission_id="MIS-PUBSUB-ENDPOINT-TEST",
        name="Pub/Sub Endpoint Test",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Target"),
    )
    store = get_run_store()
    store.create_run(AssuranceRunRecord(
        run_id="MIS-PUBSUB-ENDPOINT-TEST",
        status=AssuranceRunStatus.QUEUED,
        metadata={"mission_object": mission.model_dump(mode="json")},
    ))

    job = AssuranceJob(
        job_id="JOB-TEST-001",
        run_id="MIS-PUBSUB-ENDPOINT-TEST",
        job_type="ASSURANCE_MISSION_V2",
    )

    encoded_data = base64.b64encode(job.model_dump_json().encode("utf-8")).decode("utf-8")

    envelope = {
        "message": {
            "data": encoded_data,
            "message_id": "12345",
        },
        "subscription": "projects/rateguard-ai/subscriptions/assurance-worker",
    }

    res = client.post("/internal/pubsub/assurance", json=envelope)
    assert res.status_code == 200

    data = res.json()
    assert data["status"] in (
        ProcessingOutcome.SUCCEEDED.value,
        ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED.value,
    )
    assert data["job_id"] == "JOB-TEST-001"
    assert data["run_id"] == "MIS-PUBSUB-ENDPOINT-TEST"
