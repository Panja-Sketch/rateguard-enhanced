"""CORS, request size limits, anonymous access and health-endpoint disclosure."""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ALLOWED = "https://web.example.test"
EVIL = "https://evil.example.test"


def _client(**kw) -> TestClient:
    settings = Settings(firebase_project_id="rateguard-test", cors_origins=[ALLOWED], **kw)
    return TestClient(create_app(settings))


def test_cors_allows_only_configured_origin_and_no_credentials():
    c = _client()
    ok = c.options(
        "/api/v1/missions",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == ALLOWED
    assert "access-control-allow-credentials" not in ok.headers

    bad = c.options(
        "/api/v1/missions",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers
    assert bad.status_code == 400


def test_cors_never_returns_wildcard():
    c = _client()
    r = c.get("/health/live", headers={"Origin": EVIL})
    assert r.headers.get("access-control-allow-origin") not in ("*", EVIL)


def test_cors_restricts_methods_and_headers():
    c = _client()
    r = c.options(
        "/api/v1/missions",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "PUT"},
    )
    assert r.status_code == 400
    r = c.options(
        "/api/v1/missions",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-role"},
    )
    assert r.status_code == 400


def test_oversized_declared_body_is_rejected_before_processing():
    c = _client(max_request_bytes=1000)
    r = c.post("/api/v1/missions", content=b"x" * 5000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


def test_oversized_streamed_body_is_rejected():
    c = _client(max_request_bytes=1000)

    def chunks():
        for _ in range(10):
            yield b"y" * 500

    r = c.post("/api/v1/missions", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_body_within_limit_reaches_authentication():
    c = _client(max_request_bytes=1000)
    r = c.post("/api/v1/missions", json={"name": "small"})
    assert r.status_code == 401  # got past the size limit, stopped by auth


@pytest.mark.real_auth
def test_health_endpoints_are_public_and_expose_no_configuration(api):
    for path in ("/health", "/health/live", "/health/ready", "/"):
        r = api.get(path)
        assert r.status_code in (200, 503), path
        text = json.dumps(r.json()).lower()
        for forbidden in (
            "rateguard-enhanced",
            "rateguard-ai",
            "iam.gserviceaccount",
            "topic",
            "subscription",
            "project",
            "secret",
            "traceback",
            "firebase",
            "gemini",
        ):
            assert forbidden not in text, (path, forbidden)


@pytest.mark.real_auth
def test_liveness_needs_no_authentication_and_no_dependencies(api):
    assert api.get("/health/live").json() == {"status": "healthy"}


@pytest.mark.real_auth
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/missions",
        "/api/v1/missions/MIS-X",
        "/api/v1/missions/MIS-X/evidence",
        "/api/v1/missions/MIS-X/explanations",
        "/api/v1/sources/SRC-X",
        "/api/v1/connectors",
        "/api/v1/system/info",
        "/api/v1/system/status",
        "/api/v1/assurance/runs/MIS-X/evidence",
    ],
)
def test_business_endpoints_are_not_anonymously_accessible(api, path):
    assert api.get(path).status_code == 401


@pytest.mark.real_auth
def test_connector_metadata_never_leaks_urls_or_secrets_to_any_role(api):
    for token in ("viewer-token", "owner-token", "reviewer-token", "admin-token"):
        text = api.get("/api/v1/connectors", headers={"Authorization": "Bearer " + token}).text.lower()
        for forbidden in ("http://", "https://", "127.0.0.1", "token_env", "auth_header", "base_url", "secret"):
            assert forbidden not in text


def test_retry_after_is_readable_by_the_allowed_web_origin_only():
    c = _client()
    r = c.get("/health/live", headers={"Origin": ALLOWED})
    assert r.headers["access-control-expose-headers"] == "Retry-After"
