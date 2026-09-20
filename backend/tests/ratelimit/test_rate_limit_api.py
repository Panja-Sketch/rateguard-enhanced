"""Rate limiting through the real HTTP dependency chain (real auth, real policies)."""

import pytest
from fastapi.routing import APIRoute

from app.core.config import Settings, get_settings
from app.core.runtime_config import RuntimeConfigError
from app.core.startup_checks import validate_startup_configuration
from app.ratelimit import (
    DEFAULT_POLICIES,
    InMemoryRateLimiter,
    RateLimitUnavailable,
    get_rate_limiter,
)
from app.ratelimit.dependency import get_policies
from tests.auth.conftest import bearer
from tests.auth.test_route_access_matrix import _flatten

pytestmark = pytest.mark.real_auth

GOOD_ENV = {"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite", "VERTEX_AI_LOCATION": "us"}


def _connector_test(api, token="admin-token"):
    return api.post("/api/v1/connectors/no-such-connector/test", headers=bearer(token))


def test_connector_test_is_limited_to_five_per_hour_with_a_safe_retry_after(api):
    codes = [_connector_test(api).status_code for _ in range(5)]
    assert 429 not in codes
    r = _connector_test(api)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 3600
    assert r.json() == {"detail": {"code": "RATE_LIMITED", "message": "Too many requests. Please retry later."}}
    assert "admin-token" not in r.text and "uid-admin" not in r.text


def test_mission_creation_is_limited_to_ten_per_hour(api):
    def create():
        return api.post("/api/v1/missions", json={}, headers=bearer("owner-token")).status_code

    assert 429 not in [create() for _ in range(10)]
    assert create() == 429


def test_retry_shares_the_mission_creation_budget(api):
    for _ in range(10):
        api.post("/api/v1/missions", json={}, headers=bearer("owner-token"))
    assert api.post("/api/v1/missions/MIS-NONE/retry", headers=bearer("owner-token")).status_code == 429


def test_limits_are_per_user_and_per_tenant(api):
    for _ in range(6):
        _connector_test(api, "admin-token")
    assert _connector_test(api, "admin-token").status_code == 429
    assert _connector_test(api, "b-admin-token").status_code != 429  # other tenant, other uid


def test_second_user_in_the_same_tenant_is_not_affected(api):
    for _ in range(10):
        api.post("/api/v1/missions", json={}, headers=bearer("owner-token"))
    assert api.post("/api/v1/missions", json={}, headers=bearer("admin-token")).status_code != 429


def test_denied_and_unauthenticated_requests_never_consume_quota(api, auth_env):
    limiter: InMemoryRateLimiter = auth_env["limiter"]
    for _ in range(30):
        api.post("/api/v1/missions", json={})  # anonymous -> 401
        api.post("/api/v1/missions", json={}, headers=bearer("viewer-token"))  # wrong role -> 403
        api.post("/api/v1/connectors/x/test", headers=bearer("owner-token"))  # wrong role -> 403
        api.post("/api/v1/missions", json={}, headers=bearer("stranger-token"))  # no account -> 403
    assert limiter._counters == {}
    assert api.post("/api/v1/missions", json={}, headers=bearer("owner-token")).status_code != 429


def test_every_expensive_action_is_actually_wired_to_its_limit():
    from app.main import app

    wired: set[str] = set()
    for r in _flatten(app.routes):
        if isinstance(r, APIRoute):
            stack = list(r.dependant.dependencies)
            while stack:
                d = stack.pop()
                op = getattr(d.call, "rate_limit_operation", None)
                if op:
                    wired.add(op)
                stack.extend(d.dependencies)
    assert wired == set(DEFAULT_POLICIES)


@pytest.mark.parametrize(
    ("method", "path", "op"),
    [
        ("POST", "/api/v1/sources", "source_upload"),
        ("POST", "/api/v1/sources/SRC-X/compile", "source_compile"),
        ("POST", "/api/v1/missions", "mission_create"),
        ("POST", "/api/v1/connectors/x/test", "connector_test"),
        ("POST", "/api/v1/missions/MIS-X/explanations", "explanation_create"),
        ("GET", "/api/v1/missions/MIS-X/evidence/download", "evidence_download"),
        ("GET", "/api/v1/sources/SRC-X/artifacts/SRC-X", "source_download"),
    ],
)
def test_each_operation_returns_429_after_its_limit(api, monkeypatch, method, path, op):
    monkeypatch.setattr(get_settings(), "rate_limits", {op: "2/3600"})
    get_policies.cache_clear()
    try:
        kw = {"headers": bearer("admin-token")}
        if method == "POST":
            kw["json"] = {} if "sources" not in path or path.endswith("compile") else None
        codes = [api.request(method, path, **{k: v for k, v in kw.items() if v is not None}).status_code for _ in range(3)]
        assert codes[0] != 429 and codes[1] != 429 and codes[2] == 429
    finally:
        get_policies.cache_clear()


def test_limiter_outage_fails_closed_with_503(api, auth_env, monkeypatch):
    def boom(*a, **k):
        raise RateLimitUnavailable("down")

    monkeypatch.setattr(auth_env["limiter"], "hit", boom)
    r = api.post("/api/v1/missions", json={}, headers=bearer("owner-token"))
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert r.json()["detail"]["code"] == "RATE_LIMIT_UNAVAILABLE"


def test_corrupt_counter_state_returns_429_not_a_free_pass(api, auth_env, monkeypatch):
    from app.ratelimit import RateDecision

    monkeypatch.setattr(auth_env["limiter"], "hit", lambda *a, **k: RateDecision(False, 0, 42, corrupt_state=True))
    r = api.post("/api/v1/missions", json={}, headers=bearer("owner-token"))
    assert r.status_code == 429 and r.headers["retry-after"] == "42"


def test_contention_fallback_is_a_429_with_retry_after_never_a_5xx(api, auth_env, monkeypatch):
    from app.ratelimit import RateDecision

    monkeypatch.setattr(auth_env["limiter"], "hit", lambda *a, **k: RateDecision(False, 0, 1, reason="contention"))
    r = api.post("/api/v1/missions", json={}, headers=bearer("owner-token"))
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    assert r.json()["detail"]["code"] == "RATE_LIMITED"


def test_disabling_is_possible_locally_but_startup_forbids_it_when_deployed():
    validate_startup_configuration(Settings(firebase_project_id="p", rate_limit_enabled=False), GOOD_ENV)
    with pytest.raises(RuntimeConfigError, match="RATE_LIMIT_ENABLED"):
        validate_startup_configuration(
            Settings(firebase_project_id="p", rate_limit_enabled=False, environment="candidate", cors_origins=["https://w.example.test"]),
            GOOD_ENV,
        )


def test_startup_validates_and_reports_rate_limit_policies():
    ok = validate_startup_configuration(Settings(firebase_project_id="p", rate_limits={"connector_test": "3/600"}), GOOD_ENV)
    assert ok["rate_limits"]["connector_test"] == "3/600" and ok["rate_limits"]["mission_create"] == "10/3600"
    for bad in ({"connector_test": "0/600"}, {"nope": "1/1"}, {"mission_create": "abc"}):
        with pytest.raises(RuntimeConfigError):
            validate_startup_configuration(Settings(firebase_project_id="p", rate_limits=bad), GOOD_ENV)


def test_real_limiter_selected_by_factory_is_a_shared_store_only_on_firestore(monkeypatch):
    monkeypatch.setenv("RATEGUARD_RUN_STORE", "memory")
    get_rate_limiter.cache_clear()
    try:
        assert isinstance(get_rate_limiter(), InMemoryRateLimiter)
    finally:
        get_rate_limiter.cache_clear()
