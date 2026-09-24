"""Startup must fail closed on a missing or inconsistent connector auth
configuration in production/staging/candidate (the audience is explicit, never
derived from a traffic-tagged endpoint)."""

import pytest

from app.connectors import registry as registry_module
from app.core.config import Settings
from app.core.runtime_config import RuntimeConfigError
from app.core.startup_checks import validate_startup_configuration

GOOD_ENV = {"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite", "VERTEX_AI_LOCATION": "us"}
STABLE = "https://rateguard-rating-engine-abc123-uc.a.run.app"
TAGGED = "https://candidate---rateguard-rating-engine-abc123-uc.a.run.app"
WEB = "https://candidate---rateguard-web-abc123-uc.a.run.app"


def _settings(environment: str = "candidate", **over) -> Settings:
    values = dict(
        firebase_project_id="p",
        environment=environment,
        cors_origins=[WEB],
        rating_engine_connector_base_url=TAGGED,
        rating_engine_connector_is_local_dev=False,
        rating_engine_connector_auth_mode="google_id_token",
        rating_engine_connector_audience=STABLE,
    )
    values.update(over)
    return Settings(**values)


@pytest.mark.parametrize("environment", ["candidate", "staging", "production"])
def test_tagged_endpoint_with_stable_audience_starts(environment):
    base = STABLE if environment == "production" else TAGGED
    validate_startup_configuration(_settings(environment, rating_engine_connector_base_url=base), GOOD_ENV)


@pytest.mark.parametrize("environment", ["candidate", "staging", "production"])
@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"rating_engine_connector_audience": None}, "audience"),
        ({"rating_engine_connector_audience": TAGGED}, "traffic-tagged"),
        ({"rating_engine_connector_audience": "http://rateguard-rating-engine-abc123-uc.a.run.app"}, "https"),
        ({"rating_engine_connector_audience": "https://unrelated-abc123-uc.a.run.app"}, "same service"),
        ({"rating_engine_connector_auth_mode": "none", "rating_engine_connector_audience": None}, "google_id_token"),
        ({"rating_engine_connector_is_local_dev": True}, "local-dev"),
        ({"rating_engine_connector_base_url": "http://rateguard-rating-engine-abc123-uc.a.run.app"}, "https"),
        ({"vendor_gateway_connector_audience": "https://unrelated-abc123-uc.a.run.app"}, "vendor-gateway-demo"),
    ],
)
def test_deployed_environments_fail_startup_on_missing_or_inconsistent_connector_auth(environment, over, fragment):
    settings = _settings(environment, **over)
    with pytest.raises(RuntimeConfigError, match=fragment):
        validate_startup_configuration(settings, GOOD_ENV)


def test_local_development_may_stay_unauthenticated_only_when_explicit():
    dev = Settings(firebase_project_id="p")  # defaults: local-dev connector, auth none
    assert dev.rating_engine_connector_is_local_dev and dev.rating_engine_connector_auth_mode == "none"
    validate_startup_configuration(dev, GOOD_ENV)
    # Not local-dev and not authenticated is inconsistent even outside a deployed env.
    with pytest.raises(RuntimeConfigError, match="local-dev"):
        validate_startup_configuration(
            Settings(firebase_project_id="p", rating_engine_connector_auth_mode="google_id_token"), GOOD_ENV
        )


def test_application_refuses_to_start_with_a_missing_audience():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(_settings(rating_engine_connector_audience=None))
    with pytest.raises(RuntimeConfigError, match="audience"), TestClient(app):
        pass


def test_registry_carries_the_configured_audience_for_both_registered_connectors(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(registry_module, "get_settings", lambda: settings)
    registry_module.reset_registry_cache()
    try:
        reg = registry_module.get_registry()
        for entry in reg.values():
            assert entry.base_url == TAGGED and entry.audience == STABLE
            assert entry.auth_mode == "google_id_token" and entry.is_local_dev is False
    finally:
        registry_module.reset_registry_cache()


def test_registry_metadata_never_exposes_endpoint_or_audience():
    for meta in registry_module.list_connectors_metadata():
        dumped = meta.model_dump_json()
        assert "run.app" not in dumped and "audience" not in dumped
