import pytest
from fastapi.testclient import TestClient

from rating_engine.main import app

GOLDEN_PAYLOAD = {
    "request_id": "req-1",
    "engine_version": "canonical-v1",
    "product_id": "az_ho3",
    "effective_date": "2026-10-15",
    "transaction_type": "RENEWAL",
    "inputs": {"roof_age": 25, "dwelling_limit": "300000.00"},
    "trace_requested": False,
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_health_ready_reports_selftest_verified(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["selftest_verified"] is True
    assert set(body["engine_versions"]) == {"canonical-v1", "defective-v1"}


def test_canonical_quote_returns_700(client):
    response = client.post("/quote", json=GOLDEN_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["outputs"]["final_premium"] == "700.00"
    assert body["request_id"] == "req-1"
    assert body["engine_version"] == "canonical-v1"
    assert body["trace"] == []


def test_defective_quote_returns_655(client):
    payload = {**GOLDEN_PAYLOAD, "engine_version": "defective-v1"}
    response = client.post("/quote", json=payload)
    assert response.status_code == 200
    assert response.json()["outputs"]["final_premium"] == "655.00"


def test_trace_requested_returns_nonempty_trace(client):
    payload = {**GOLDEN_PAYLOAD, "trace_requested": True}
    response = client.post("/quote", json=payload)
    assert response.status_code == 200
    trace = response.json()["trace"]
    assert len(trace) > 0
    assert any(step["node_id"] == "final_premium_output" for step in trace)


def test_unknown_engine_version_rejected(client):
    payload = {**GOLDEN_PAYLOAD, "engine_version": "made-up-v99"}
    response = client.post("/quote", json=payload)
    assert response.status_code == 400


def test_wrong_product_id_rejected(client):
    payload = {**GOLDEN_PAYLOAD, "product_id": "wrong_product"}
    response = client.post("/quote", json=payload)
    assert response.status_code == 422


def test_missing_required_input_rejected(client):
    payload = {**GOLDEN_PAYLOAD, "inputs": {"dwelling_limit": "300000.00"}}
    response = client.post("/quote", json=payload)
    assert response.status_code == 422


def test_unknown_request_field_rejected(client):
    payload = {**GOLDEN_PAYLOAD, "unexpected_field": "boom"}
    response = client.post("/quote", json=payload)
    assert response.status_code == 422
