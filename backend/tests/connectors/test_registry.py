"""Connector registry tests: fail-closed selection, safe metadata exposure
(locked doc section 13.2), and the real two-entry demo registry (proving the
registry generalizes beyond a single hardcoded connector -- see
app/connectors/registry.py's `_build_registry` docstring)."""

import pytest

from app.connectors.errors import ConnectorFailureCategory
from app.connectors.registry import (
    UnknownConnectorError,
    UnknownEngineVersionError,
    get_registry,
    list_connectors_metadata,
    reset_registry_cache,
    select_connector,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry_cache()
    yield
    reset_registry_cache()


def test_real_registry_has_two_demo_connectors_with_different_wire_formats():
    registry = get_registry()
    assert set(registry) == {"rating-engine-demo", "vendor-gateway-demo"}
    for connector_id in ("rating-engine-demo", "vendor-gateway-demo"):
        entry = registry[connector_id]
        assert set(entry.allowed_engine_versions) == {"canonical-v1", "defective-v1"}
    assert registry["rating-engine-demo"].wire_format == "rateguard_native_v1"
    assert registry["vendor-gateway-demo"].wire_format == "vendor_gateway_v1"


def test_select_connector_succeeds_for_registered_id_and_version():
    entry = select_connector("rating-engine-demo", "canonical-v1")
    assert entry.connector_id == "rating-engine-demo"


def test_select_connector_fails_closed_for_unregistered_connector():
    with pytest.raises(UnknownConnectorError) as excinfo:
        select_connector("not-a-real-connector", "canonical-v1")
    assert excinfo.value.error.code == "CONNECTOR_NOT_REGISTERED"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


def test_select_connector_fails_closed_for_undeclared_engine_version():
    with pytest.raises(UnknownEngineVersionError) as excinfo:
        select_connector("rating-engine-demo", "made-up-v99")
    assert excinfo.value.error.code == "CONNECTOR_ENGINE_VERSION_NOT_ALLOWED"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


def test_select_connector_never_falls_back_to_a_default():
    """An unregistered connector_id must never silently resolve to the
    one real demo connector, even though it is the only entry."""
    with pytest.raises(UnknownConnectorError):
        select_connector("", "canonical-v1")


def test_metadata_never_exposes_base_url_or_credentials():
    metadata = list_connectors_metadata()
    assert len(metadata) == 2
    by_id = {m.connector_id: m.model_dump() for m in metadata}
    assert set(by_id) == {"rating-engine-demo", "vendor-gateway-demo"}
    for dumped in by_id.values():
        assert "base_url" not in dumped
        assert "auth_header_name" not in dumped
        assert "auth_token_env_var" not in dumped
        assert set(dumped["allowed_engine_versions"]) == {"canonical-v1", "defective-v1"}
    assert by_id["rating-engine-demo"]["wire_format"] == "rateguard_native_v1"
    assert by_id["vendor-gateway-demo"]["wire_format"] == "vendor_gateway_v1"
