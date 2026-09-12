from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cors_origin_headers() -> None:
    res = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_system_info() -> None:
    res = client.get("/api/v1/system/info")
    assert res.status_code == 200
    data = res.json()
    assert data["gemini_model"] == "gemini-3.7-flash"
    assert data["agent_framework"] == "Google GenAI SDK"
    assert data["agent_provider"] == "Google Vertex AI"
    assert data["agent_supervisor"] == "Google GenAI SDK Structured-Decision Supervisor"
    assert data["ipir_version"] == "0.1"
    assert "ADK" not in data["agent_framework"]


def test_assurance_run_events_and_evidence_are_generic_over_the_run_store() -> None:
    """GET /assurance/runs/{id}/events and .../evidence are the two endpoints
    from the pre-Mission-V2 API surface that survive -- they are generic
    reads over the run store, and the live Mission detail page's Agent
    Action Timeline and Evidence Lineage tabs call them for real 'MIS-*'
    missions. Everything else in the old /api/v1/assurance/* and
    /api/v1/demo/* surface (creating/listing legacy 'RUN-*' runs, the
    retired scenario lab) has been removed -- it was unreachable from the
    deployed product."""
    from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store

    store = get_run_store()
    store.create_run(AssuranceRunRecord(
        run_id="MIS-EVENTS-EVIDENCE-TEST", status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED",
    ))

    events_res = client.get("/api/v1/assurance/runs/MIS-EVENTS-EVIDENCE-TEST/events")
    assert events_res.status_code == 200
    assert "events" in events_res.json()

    evidence_res = client.get("/api/v1/assurance/runs/MIS-EVENTS-EVIDENCE-TEST/evidence")
    assert evidence_res.status_code == 200
    assert "evidence" in evidence_res.json()

    missing_res = client.get("/api/v1/assurance/runs/MIS-DOES-NOT-EXIST/events")
    assert missing_res.status_code == 404


def test_removed_legacy_endpoints_are_gone() -> None:
    """The retired legacy surface must not silently resurrect as a 404 that
    looks like a typo -- it should be genuinely absent (405/404 from
    routing, not a handled response)."""
    assert client.post("/api/v1/assurance/runs", json={}).status_code == 404
    assert client.get("/api/v1/assurance/runs").status_code == 404
    assert client.get("/api/v1/demo/packages").status_code == 404
    assert client.get("/api/v1/demo/scenarios").status_code == 404
    assert client.post("/api/v1/assurance/scenario-lab", json={}).status_code == 404
