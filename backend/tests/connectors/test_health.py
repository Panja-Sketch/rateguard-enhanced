"""Connector health testing (locked doc section 13.2 `POST
/connectors/{connector_id}/test`). Runs the golden case through the full
client path against the real `backend/rating_engine` service, and confirms
the health-check function never accepts an arbitrary connector target."""

import pytest

from app.connectors.client import ConnectorClient
from app.connectors.health import test_connector_health as run_connector_health_check


@pytest.mark.asyncio
async def test_health_check_passes_for_real_canonical_engine(real_demo_transport):
    # select_connector requires the connector_id to be in the real global
    # registry ("rating-engine-demo") for this end-to-end health-check
    # path, so route that connector's HTTP calls through the in-process
    # demo transport rather than a real socket.
    result = await run_connector_health_check(
        "rating-engine-demo", client=ConnectorClient(transport=real_demo_transport)
    )

    assert result["connector_id"] == "rating-engine-demo"
    assert result["healthy"] is True
    assert result["actual_premium"] == "700.00"
    assert "base_url" not in result
    assert "credential" not in str(result).lower()


@pytest.mark.asyncio
async def test_health_check_fails_closed_for_unregistered_connector():
    result = await run_connector_health_check("not-a-real-connector-id")
    assert result["healthy"] is False
    assert result["code"] == "CONNECTOR_NOT_REGISTERED"
