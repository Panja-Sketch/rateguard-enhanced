"""The Pub/Sub worker boundary is a *separate* control from user authentication.

`/internal/pubsub/assurance` is not a Firebase-user endpoint: in production it
is reachable only through private Cloud Run IAM (`--no-allow-unauthenticated`)
plus the Pub/Sub push subscription's OIDC service identity. Application code
therefore must NOT require (or accept as a substitute) a Firebase bearer token,
and the public API service must not expose the route at all."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.messaging import AssuranceJob
from tests.auth.conftest import bearer

WORKER_PATH = "/internal/pubsub/assurance"


def _envelope(job_id: str = "JOB-BOUNDARY") -> dict:
    job = AssuranceJob(job_id=job_id, run_id="MIS-BOUNDARY-NOT-THERE", job_type="ASSURANCE_MISSION_V2")
    data = base64.b64encode(job.model_dump_json().encode()).decode()
    return {"message": {"data": data, "message_id": "1"}, "subscription": "projects/p/subscriptions/s"}


@pytest.mark.real_auth
def test_worker_route_does_not_use_firebase_user_authentication(api):
    """No Authorization header at all: the request reaches the worker handler
    (it answers about the job) rather than being rejected as an unauthenticated user."""
    r = api.post(WORKER_PATH, json=_envelope())
    assert r.status_code not in (401, 403)
    assert r.json()["job_id"] == "JOB-BOUNDARY"


@pytest.mark.real_auth
def test_a_firebase_user_token_is_irrelevant_to_the_worker_route(api):
    with_token = api.post(WORKER_PATH, json=_envelope("JOB-A"), headers=bearer("admin-token"))
    without_token = api.post(WORKER_PATH, json=_envelope("JOB-A"))
    assert with_token.status_code == without_token.status_code
    assert with_token.json()["status"] == without_token.json()["status"]


@pytest.mark.real_auth
def test_business_api_rejects_what_the_worker_route_accepts(api):
    assert api.get("/api/v1/missions").status_code == 401
    assert api.post(WORKER_PATH, json=_envelope()).status_code != 401


def _client(role: str) -> TestClient:
    return TestClient(create_app(Settings(service_role=role, firebase_project_id="rateguard-test")))


def test_public_api_service_does_not_expose_the_internal_worker_route():
    api_only = _client("api")
    assert api_only.post(WORKER_PATH, json=_envelope()).status_code == 404
    assert api_only.get("/health/live").status_code == 200


def test_worker_service_exposes_no_business_api():
    worker_only = _client("worker")
    assert worker_only.get("/api/v1/missions").status_code == 404
    assert worker_only.post("/api/v1/sources").status_code == 404
    assert worker_only.post(WORKER_PATH, json=_envelope()).status_code not in (404, 401, 403)
    assert worker_only.get("/health/live").status_code == 200


def test_invalid_service_role_is_rejected():
    with pytest.raises(ValueError):
        create_app(Settings(service_role="everything"))
