"""The optional scoped, read-only demo API key path (`X-RateGuard-Api-Key`,
README "External API Access") -- unit-level tests against
`_demo_api_key_user` directly, since it depends on `Settings` rather than the
injectable `TokenVerifier`/`UserDirectory` the other auth tests exercise."""

from unittest.mock import Mock

import pytest

from app.auth import Role
from app.auth.dependencies import _demo_api_key_user
from app.core.config import Settings


def _request(headers: dict[str, str]) -> Mock:
    req = Mock()
    req.headers = {k.lower(): v for k, v in headers.items()}
    req.url.path = "/api/v1/missions"
    return req


def test_returns_none_when_feature_unconfigured(monkeypatch):
    monkeypatch.setattr(
        "app.auth.dependencies.get_settings",
        lambda: Settings(demo_api_key=None),
    )
    assert _demo_api_key_user(_request({"X-RateGuard-Api-Key": "anything"})) is None


def test_returns_none_when_header_absent_even_if_configured(monkeypatch):
    monkeypatch.setattr(
        "app.auth.dependencies.get_settings",
        lambda: Settings(demo_api_key="correct-key"),  # pragma: allowlist secret
    )
    assert _demo_api_key_user(_request({})) is None


def test_valid_key_resolves_to_readonly_viewer(monkeypatch):
    monkeypatch.setattr(
        "app.auth.dependencies.get_settings",
        lambda: Settings(demo_api_key="correct-key", demo_api_key_tenant_id="demo-tenant"),  # pragma: allowlist secret
    )
    user = _demo_api_key_user(_request({"X-RateGuard-Api-Key": "correct-key"}))
    assert user is not None
    assert user.role == Role.VIEWER
    assert user.tenant_id == "demo-tenant"


def test_wrong_key_raises_401(monkeypatch):
    monkeypatch.setattr(
        "app.auth.dependencies.get_settings",
        lambda: Settings(demo_api_key="correct-key"),  # pragma: allowlist secret
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _demo_api_key_user(_request({"X-RateGuard-Api-Key": "wrong-key"}))
    assert excinfo.value.status_code == 401
