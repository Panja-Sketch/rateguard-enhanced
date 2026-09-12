from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _clear_auth_env(monkeypatch):
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(var, raising=False)


def test_system_status_reports_diagnostics_without_secrets():
    """Proves /api/v1/system/status reports configuration diagnostics and never
    exposes secret values, credentials, or auth headers."""
    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    data = res.json()

    for key in ("run_store", "message_queue", "worker_heartbeat", "gemini", "bigquery", "artifact_storage"):
        assert key in data

    assert data["message_queue"]["worker_push_route"] == "/internal/pubsub/assurance"
    assert "endpoint_probe_invoked" in data["gemini"]
    assert data["gemini"]["endpoint_probe_invoked"] is False

    # Never expose secret-shaped values anywhere in the response.
    serialized = str(data).lower()
    for forbidden in ("secret", "authorization", "bearer ", "api_key", "private_key"):
        assert forbidden not in serialized


def test_system_status_does_not_invoke_gemini_generation():
    """Proves the diagnostics endpoint reports the configured model id without
    performing a billable generation call (no 'generate_content'/response fields)."""
    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    gemini = res.json()["gemini"]
    assert "configured_model_id" in gemini
    assert "response" not in gemini
    assert "generated_text" not in gemini


def test_system_status_gemini_reports_vertex_ai_configuration(monkeypatch):
    """Regression: the gemini sub-object must report the SAME auth mode and
    location GeminiDecisionClient's real calls would use -- previously it
    reported a hardcoded, unrelated 'location' field (a stale
    AgentConfig.location default of 'us-central1', the general Cloud
    Run/GCP deployment region) instead of the actual effective Gemini
    location (GOOGLE_CLOUD_LOCATION='global' in this deployment)."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    gemini = res.json()["gemini"]

    assert gemini["configured_model_id"] == "gemini-3.7-flash"
    assert gemini["provider"] == "Google Vertex AI"
    assert "Google GenAI SDK" in gemini["framework"]
    assert gemini["auth_mode"] == "VERTEX_AI"
    assert gemini["configured_location"] == "global"
    assert gemini["configured_location"] != "us-central1"
    assert gemini["endpoint_probe_invoked"] is False


def test_system_status_gemini_reports_api_key_configuration(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-value")

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    gemini = res.json()["gemini"]

    assert gemini["provider"] == "Google Gemini API"
    assert gemini["auth_mode"] == "API_KEY"
    assert gemini["configured_location"] is None
    assert "fake-key-value" not in str(res.json())


def test_system_status_gemini_reports_no_auth_configuration(monkeypatch):
    _clear_auth_env(monkeypatch)

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    gemini = res.json()["gemini"]

    assert gemini["auth_mode"] == "NONE"
    assert gemini["provider"] == "Not configured"
    assert gemini["configured_location"] is None


def test_system_status_never_constructs_a_gemini_client(monkeypatch):
    """The endpoint must remain read-only: no Gemini generation call, no
    credential validation call. Fails loudly if the endpoint ever
    constructs a real google.genai.Client."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    def _fail_if_constructed(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("/api/v1/system/status must never construct a google.genai.Client")

    monkeypatch.setattr("google.genai.Client", _fail_if_constructed)

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    assert res.json()["gemini"]["auth_mode"] == "VERTEX_AI"
