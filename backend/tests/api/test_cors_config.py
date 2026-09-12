"""Focused CORS tests.

Regression coverage for the candidate-deployment CORS gap: the candidate
rateguard-web origin (a Cloud Run traffic-tag URL, e.g.
"https://candidate---rateguard-web-<hash>.a.run.app") was never added to
RATEGUARD_CORS_ORIGINS for the candidate API, so every browser request from
the candidate frontend was silently blocked by CORSMiddleware while a plain
CLI/urllib client (which never sends an Origin header) saw no failure at all.
See infrastructure/deploy_candidate.sh's get_candidate_cors_origins() /
infrastructure/lib/CandidateDeployLib.ps1's Get-CandidateCorsOrigins for the
fix that derives the candidate origin from the production origin.

These tests exercise the real app.main CORSMiddleware contract (same
allow_origins/allow_credentials/allow_methods/allow_headers configuration),
via a standalone app built the same way app/main.py builds it, so they don't
depend on -- or mutate -- the module-level `app` singleton's cached Settings.
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import Settings

PRODUCTION_ORIGIN = "https://rateguard-web-iqofutwtva-uc.a.run.app"
CANDIDATE_ORIGIN = "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"
UNKNOWN_ORIGIN = "https://evil.example.com"


def _build_app(cors_origins: list[str]) -> FastAPI:
    """Mirrors app.main's exact CORSMiddleware setup (see app/main.py)."""
    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "healthy"}

    @test_app.post("/api/v1/missions")
    async def create_mission() -> dict[str, str]:
        return {"mission_id": "test"}

    return test_app


CANDIDATE_DEPLOYMENT_ORIGINS = [PRODUCTION_ORIGIN, CANDIDATE_ORIGIN]
client = TestClient(_build_app(CANDIDATE_DEPLOYMENT_ORIGINS))


def test_cors_origins_env_var_is_a_json_array_parsed_by_settings(monkeypatch) -> None:
    """The exact RATEGUARD_CORS_ORIGINS environment-variable contract:
    pydantic-settings parses it as a JSON array string into
    Settings.cors_origins (see infrastructure/runtime-env.yaml)."""
    monkeypatch.setenv(
        "RATEGUARD_CORS_ORIGINS",
        json.dumps(["http://localhost:3000", PRODUCTION_ORIGIN, CANDIDATE_ORIGIN]),
    )
    settings = Settings()
    assert settings.cors_origins == ["http://localhost:3000", PRODUCTION_ORIGIN, CANDIDATE_ORIGIN]


def test_production_origin_allowed() -> None:
    res = client.get("/health/live", headers={"Origin": PRODUCTION_ORIGIN})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == PRODUCTION_ORIGIN


def test_candidate_origin_allowed() -> None:
    res = client.get("/health/live", headers={"Origin": CANDIDATE_ORIGIN})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == CANDIDATE_ORIGIN


def test_unknown_origin_is_not_granted_cors_access() -> None:
    """The request still gets a 200 body (the server doesn't reject it), but
    without an Access-Control-Allow-Origin header a real browser blocks the
    response from ever reaching frontend JS -- this is what "rejected" means
    for a simple (non-preflighted) CORS request."""
    res = client.get("/health/live", headers={"Origin": UNKNOWN_ORIGIN})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_preflight_allows_production_origin() -> None:
    res = client.options(
        "/api/v1/missions",
        headers={
            "Origin": PRODUCTION_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == PRODUCTION_ORIGIN
    assert "POST" in res.headers.get("access-control-allow-methods", "")


def test_preflight_allows_candidate_origin() -> None:
    res = client.options(
        "/api/v1/missions",
        headers={
            "Origin": CANDIDATE_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == CANDIDATE_ORIGIN
    assert "POST" in res.headers.get("access-control-allow-methods", "")


def test_preflight_rejects_unknown_origin() -> None:
    """Matches the exact candidate-origin-bug symptom: an unlisted origin's
    preflight gets HTTP 400 with no Access-Control-Allow-Origin header."""
    res = client.options(
        "/api/v1/missions",
        headers={
            "Origin": UNKNOWN_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 400
    assert "access-control-allow-origin" not in res.headers


def test_no_wildcard_origin_configured() -> None:
    """allow_credentials=True makes a wildcard origin both invalid per the
    Fetch/CORS spec and a real credential-leak risk -- the allow-list must
    always be an explicit set of origins."""
    assert "*" not in CANDIDATE_DEPLOYMENT_ORIGINS
