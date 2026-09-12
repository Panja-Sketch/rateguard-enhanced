from unittest.mock import patch

from fastapi.testclient import TestClient


def test_root_endpoint(client: TestClient) -> None:
    """Verify that root GET / returns expected metadata and running status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "RateGuard AI"
    assert data["version"] == "0.1.0"
    assert data["status"] == "running"


def test_health_endpoint(client: TestClient) -> None:
    """Verify that legacy GET /health remains backward compatible for existing probes."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "healthy"}


def test_liveness_endpoint_does_not_call_external_dependencies(client: TestClient) -> None:
    """Proves /health/live never touches run store, message queue, Gemini, or BigQuery."""
    with (
        patch("app.storage.get_run_store") as mock_store,
        patch("app.messaging.get_message_publisher") as mock_publisher,
    ):
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}
        mock_store.assert_not_called()
        mock_publisher.assert_not_called()


def test_readiness_endpoint_reports_ok_checks(client: TestClient) -> None:
    """Proves /health/ready reports structured per-dependency checks."""
    response = client.get("/health/ready")
    assert response.status_code in (200, 503)
    data = response.json()
    assert data["status"] in ("ready", "degraded")
    assert "run_store" in data["checks"]
    assert "message_queue" in data["checks"]
    assert data["checks"]["run_store"]["status"] in ("ok", "degraded")


def test_readiness_endpoint_reports_degraded_on_run_store_failure(client: TestClient) -> None:
    """Proves a run store failure surfaces as a structured degraded reason, not a crash."""
    with patch("app.storage.get_run_store", side_effect=RuntimeError("boom")):
        response = client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["run_store"]["status"] == "degraded"
        assert "boom" in data["checks"]["run_store"]["detail"]
