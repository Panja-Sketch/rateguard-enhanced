"""The route-access matrix, asserted three ways:

1. every non-public route in the running app is in MATRIX (nothing is added
   without an explicit access decision);
2. each route's declared dependency requires exactly the MATRIX roles;
3. behaviorally, each of the four roles gets 403 exactly where the matrix says
   and never 401/403 where it is allowed.

docs/security/AUTHORIZATION_MATRIX.md mirrors MATRIX; keep them in sync."""

import pytest
from fastapi.routing import APIRoute

from app.auth import Role
from app.main import app
from tests.auth.conftest import ROLE_USERS, bearer

pytestmark = pytest.mark.real_auth

ALL = {Role.ADMIN, Role.RELEASE_OWNER, Role.CONSUMER_REVIEWER, Role.VIEWER}
WRITE = {Role.ADMIN, Role.RELEASE_OWNER}
DOWNLOAD = {Role.ADMIN, Role.RELEASE_OWNER, Role.CONSUMER_REVIEWER}
REVIEW = {Role.ADMIN, Role.CONSUMER_REVIEWER}
ADMIN = {Role.ADMIN}

MATRIX: dict[tuple[str, str], set[Role]] = {
    ("GET", "/api/v1/me"): ALL,
    ("GET", "/api/v1/system/info"): ALL,
    ("GET", "/api/v1/system/status"): ADMIN,
    ("GET", "/api/v1/assurance/runs/{run_id}/events"): ALL,
    ("GET", "/api/v1/assurance/runs/{run_id}/evidence"): ALL,
    ("GET", "/api/v1/connectors"): ALL,
    ("POST", "/api/v1/connectors/{connector_id}/test"): ADMIN,
    ("POST", "/api/v1/sources"): WRITE,
    ("POST", "/api/v1/sources/{source_id}/compile"): WRITE,
    ("GET", "/api/v1/sources/{source_id}"): ALL,
    ("GET", "/api/v1/sources/{source_id}/artifacts/{artifact_id}"): WRITE,
    ("POST", "/api/v1/missions"): WRITE,
    ("GET", "/api/v1/missions"): ALL,
    ("GET", "/api/v1/missions/{mission_id}"): ALL,
    ("GET", "/api/v1/missions/{mission_id}/evidence"): ALL,
    ("GET", "/api/v1/missions/{mission_id}/evidence/download"): DOWNLOAD,
    ("GET", "/api/v1/missions/{mission_id}/evidence/bundle"): DOWNLOAD,
    ("GET", "/api/v1/missions/{mission_id}/impact"): ALL,
    ("GET", "/api/v1/missions/{mission_id}/connector-evidence"): ALL,
    ("POST", "/api/v1/missions/{mission_id}/alignment-options"): WRITE,
    ("POST", "/api/v1/missions/{mission_id}/cancel"): WRITE,
    ("POST", "/api/v1/missions/{mission_id}/retry"): WRITE,
    ("POST", "/api/v1/missions/{mission_id}/archive"): WRITE,
    ("DELETE", "/api/v1/missions/{mission_id}"): ADMIN,
    ("POST", "/api/v1/missions/{mission_id}/explanations"): DOWNLOAD,
    ("GET", "/api/v1/missions/{mission_id}/explanations"): ALL,
    ("POST", "/api/v1/explanations/{explanation_id}/approve"): REVIEW,
    ("POST", "/api/v1/explanations/{explanation_id}/reject"): REVIEW,
}

PUBLIC = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
}
WORKER = {("POST", "/internal/pubsub/assurance"), ("POST", "/internal/pubsub/impact-batch")}


def _declared_roles(route: APIRoute) -> set[Role] | None:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        roles = getattr(dep.call, "allowed_roles", None)
        if roles is not None:
            return set(roles)
        stack.extend(dep.dependencies)
    return None


def _flatten(routes):
    """FastAPI includes routers lazily (`_IncludedRouter`); walk into them. Every
    router in this app carries its full prefix on the router itself."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _flatten(inner.routes)
        else:
            yield r


def _routes() -> dict[tuple[str, str], APIRoute]:
    found = {}
    for r in _flatten(app.routes):
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                found[(m, r.path)] = r
    return found


def test_every_route_has_an_explicit_access_decision():
    keys = set(_routes())
    unaccounted = keys - set(MATRIX) - PUBLIC - WORKER
    assert not unaccounted, f"routes with no access-matrix entry: {sorted(unaccounted)}"
    stale = set(MATRIX) - keys
    assert not stale, f"matrix entries with no route: {sorted(stale)}"


def test_declared_dependency_roles_match_the_matrix():
    routes = _routes()
    for key, expected in MATRIX.items():
        assert _declared_roles(routes[key]) == expected, key


def test_public_and_worker_routes_have_no_firebase_role_dependency():
    routes = _routes()
    for key in PUBLIC | WORKER:
        assert _declared_roles(routes[key]) is None, key


def _concrete(path: str) -> str:
    return (
        path.replace("{run_id}", "RUN-NONE")
        .replace("{mission_id}", "MIS-NONE")
        .replace("{source_id}", "SRC-NONE")
        .replace("{artifact_id}", "SRC-NONE")
        .replace("{connector_id}", "no-such-connector")
        .replace("{explanation_id}", "EXP-AAAAAAAA-MIS-NONE")
    )


CASES = [(key, role) for key in MATRIX for role in Role]


@pytest.mark.parametrize(("key", "role"), CASES, ids=[f"{m} {p} as {r.value}" for (m, p), r in CASES])
def test_role_access_matrix(api, key, role):
    method, path = key
    _, token = ROLE_USERS[role]
    kwargs = {"headers": bearer(token)}
    if method == "POST" and path.endswith("/reject"):
        kwargs["json"] = {"reason": "not accurate"}
    elif method == "POST":
        kwargs["json"] = {}
    resp = api.request(method, _concrete(path), **kwargs)
    if role in MATRIX[key]:
        assert resp.status_code not in (401, 403), (resp.status_code, resp.text)
    else:
        assert resp.status_code == 403, (resp.status_code, resp.text)
        assert resp.json()["detail"]["code"] == "INSUFFICIENT_ROLE"


@pytest.mark.parametrize("key", list(MATRIX), ids=[f"{m} {p}" for m, p in MATRIX])
def test_anonymous_requests_are_rejected_on_every_business_route(api, key):
    method, path = key
    kwargs = {"json": {}} if method == "POST" else {}
    resp = api.request(method, _concrete(path), **kwargs)
    assert resp.status_code == 401
