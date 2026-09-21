"""Authentication behavior of the centralized dependency (real chain, fake verifier)."""

import pytest

from app.auth import Role
from tests.auth.conftest import bearer

pytestmark = pytest.mark.real_auth

PROTECTED = "/api/v1/missions"


def test_valid_token_authenticates(api):
    r = api.get(PROTECTED, headers=bearer("viewer-token"))
    assert r.status_code == 200


def test_missing_authorization_header_is_401(api):
    r = api.get(PROTECTED)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize(
    "header",
    ["Bearer", "Bearer ", "Basic abc", "bearer-token", "Token viewer-token", "Bearer a b", "Bearer viewer-token extra"],
)
def test_malformed_authorization_header_is_401(api, header):
    r = api.get(PROTECTED, headers={"Authorization": header})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] in ("MALFORMED_AUTHORIZATION", "AUTHENTICATION_REQUIRED")


def test_oversized_token_is_401(api):
    r = api.get(PROTECTED, headers=bearer("a" * 9000))
    assert r.status_code == 401


def test_expired_token_is_401(api):
    r = api.get(PROTECTED, headers=bearer("expired-token"))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_TOKEN"


def test_unverifiable_token_is_401(api):
    r = api.get(PROTECTED, headers=bearer("wrong-audience-token"))
    assert r.status_code == 401


def test_error_bodies_never_echo_the_token(api):
    secret = "super-secret-looking-token-value"  # pragma: allowlist secret
    responses = (
        api.get(PROTECTED, headers=bearer(secret)),
        api.get(PROTECTED, headers={"Authorization": "Basic " + secret}),
    )
    for r in responses:
        assert secret not in r.text
        assert "Traceback" not in r.text


def test_unknown_user_record_is_403(api):
    r = api.get(PROTECTED, headers=bearer("stranger-token"))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "ACCOUNT_NOT_PROVISIONED"


def test_disabled_user_is_403_even_with_admin_role(api):
    r = api.get(PROTECTED, headers=bearer("disabled-token"))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "ACCOUNT_DISABLED"


def test_verifier_outage_fails_closed_with_503(api, auth_env):
    auth_env["verifier"].unavailable = True
    r = api.get(PROTECTED, headers=bearer("viewer-token"))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "AUTH_SERVICE_UNAVAILABLE"


def test_directory_outage_fails_closed_without_leaking(api, auth_env, monkeypatch):
    def boom(uid):
        raise RuntimeError("firestore project rateguard-secret exploded")

    monkeypatch.setattr(auth_env["directory"], "get_user", boom)
    r = api.get(PROTECTED, headers=bearer("viewer-token"))
    assert r.status_code == 503
    assert "rateguard-secret" not in r.text


@pytest.mark.parametrize("token", ["viewer-token", "owner-token", "reviewer-token", "admin-token"])
def test_all_four_roles_can_authenticate_and_read(api, token):
    assert api.get(PROTECTED, headers=bearer(token)).status_code == 200


def test_malformed_user_document_grants_nothing():
    from app.auth.directory import parse_user_document

    assert parse_user_document("u", {"role": "SUPERUSER", "tenant_id": "t"}) is None
    assert parse_user_document("u", {"role": "ADMIN"}) is None
    assert parse_user_document("u", {"role": "ADMIN", "tenant_id": "  "}) is None
    assert parse_user_document("u", None) is None
    assert parse_user_document("u", {"role": "VIEWER", "tenant_id": "t"}).role == Role.VIEWER


# ---- the backend never accepts a client-supplied role, tenant or identity ----


def test_client_supplied_role_and_tenant_headers_are_ignored(api):
    r = api.get(
        "/api/v1/system/status",
        headers={**bearer("viewer-token"), "X-Role": "ADMIN", "X-Tenant-Id": "tenant-b", "X-User-Id": "uid-admin"},
    )
    assert r.status_code == 403  # still a VIEWER


def test_client_supplied_role_and_tenant_query_params_are_ignored(api):
    r = api.get("/api/v1/system/status?role=ADMIN&tenant_id=tenant-b", headers=bearer("viewer-token"))
    assert r.status_code == 403


def test_client_supplied_role_and_tenant_in_body_are_ignored_for_mission_creation(api, monkeypatch):
    """A RELEASE_OWNER in tenant A posting role/tenant/created_by fields gets a
    mission stamped with tenant A and their own uid, whatever the body says."""
    published = []

    class _Publisher:
        def publish_assurance_job(self, job):
            published.append(job)

    monkeypatch.setattr("app.api.missions.get_message_publisher", lambda: _Publisher())
    body = {
        "name": "m",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "A"},
        "source_b": {"source_id": "AZ_HO3_2026_09_DEFECTIVE", "source_type": "SAMPLE_RELEASE", "name": "B"},
        "tenant_id": "tenant-b",
        "role": "ADMIN",
        "created_by": "uid-b-admin",
    }
    r = api.post("/api/v1/missions", json=body, headers=bearer("owner-token"))
    assert r.status_code == 202, r.text
    from app.storage import get_run_store

    rec = get_run_store().get_run(r.json()["mission_id"])
    assert rec.tenant_id == "tenant-a"
    assert rec.created_by == "uid-owner"
    assert published and published[0].tenant_id == "tenant-a"
    # ... and tenant B cannot see it.
    assert api.get("/api/v1/missions/" + rec.run_id, headers=bearer("b-admin-token")).status_code == 404


def test_reject_explanation_body_cannot_smuggle_identity(api):
    r = api.post(
        "/api/v1/explanations/EXP-AAAAAAAA-MIS-X/reject",
        json={"reason": "because", "reviewed_by": "someone", "role": "ADMIN"},
        headers=bearer("reviewer-token"),
    )
    assert r.status_code == 422  # extra fields forbidden


def test_me_reports_server_side_identity_only(api):
    r = api.get("/api/v1/me", headers={**bearer("reviewer-token"), "X-Role": "ADMIN", "X-Tenant-Id": "tenant-b"})
    assert r.status_code == 200
    assert r.json() == {
        "uid": "uid-reviewer",
        "email": "uid-reviewer@example.test",
        "tenant_id": "tenant-a",
        "role": "CONSUMER_REVIEWER",
    }


def test_me_for_unprovisioned_user_is_403_not_a_role_guess(api):
    r = api.get("/api/v1/me", headers=bearer("stranger-token"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ACCOUNT_NOT_PROVISIONED"
