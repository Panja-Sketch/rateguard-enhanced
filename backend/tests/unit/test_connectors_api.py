"""`GET /api/v1/connectors` (locked doc section 13.2): safe, credential-free
metadata only. Must never expose a base URL, auth header name, or token
env-var name — those live only in the server-side registry."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_connectors_returns_safe_metadata_only():
    resp = client.get("/api/v1/connectors")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1

    ids = {c["connector_id"] for c in body}
    assert "rating-engine-demo" in ids

    demo = next(c for c in body if c["connector_id"] == "rating-engine-demo")
    assert set(demo["allowed_engine_versions"]) == {"canonical-v1", "defective-v1"}
    assert "display_name" in demo

    # No credential/infrastructure leakage, in any entry, ever.
    raw_text = resp.text.lower()
    assert "base_url" not in raw_text
    assert "auth_header" not in raw_text
    assert "auth_token" not in raw_text
    assert "http://" not in raw_text
    assert "https://" not in raw_text
