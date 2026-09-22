"""Proves `app.connectors.client` genuinely adapts to a second, differently-
shaped wire contract (`vendor_gateway_v1`) -- not just RateGuard's own
contract under a new connector id. Run through the real
`backend/rating_engine` ASGI app's `/vendor/rate-quote` route (real Pydantic
validation on the target side, no socket), exactly like the
`rateguard_native_v1` golden tests in test_client_golden.py."""

from datetime import date

import pytest

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.ipir.enums import TransactionType

GOLDEN_INPUTS = {"roof_age": 25, "dwelling_limit": "300000.00"}
GOLDEN_DATE = date(2026, 10, 15)


@pytest.mark.asyncio
async def test_vendor_gateway_canonical_result_is_700(real_demo_transport, real_vendor_gateway_entry):
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    response = await client.send_quote_to_entry(real_vendor_gateway_entry, request)
    assert response.outputs["final_premium"] == "700.00"
    assert response.request_id == request.request_id
    assert response.engine_version == "canonical-v1"


@pytest.mark.asyncio
async def test_vendor_gateway_defective_result_is_655(real_demo_transport, real_vendor_gateway_entry):
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="defective-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    response = await client.send_quote_to_entry(real_vendor_gateway_entry, request)
    assert response.outputs["final_premium"] == "655.00"


@pytest.mark.asyncio
async def test_vendor_gateway_and_native_agree_on_the_same_inputs(
    real_demo_transport, real_demo_entry, real_vendor_gateway_entry
):
    """Same underlying computation, two different wire shapes -- both must
    reach the same answer, proving the adapter is a faithful translation."""
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    native = await client.send_quote_to_entry(real_demo_entry, request)
    vendor = await client.send_quote_to_entry(real_vendor_gateway_entry, request)
    assert native.outputs == vendor.outputs


@pytest.mark.asyncio
async def test_vendor_gateway_does_not_advertise_batch_capability(real_demo_transport, real_vendor_gateway_entry):
    """The batch contract is `rateguard_native_v1`'s own; a connector
    registered under a different wire_format is always driven with the
    bounded-concurrent single-quote path, even if its base_url happens to be
    a service that also serves /quote/batch under a different identity."""
    client = ConnectorClient(transport=real_demo_transport)
    max_items = await client.discover_batch_capability(real_vendor_gateway_entry)
    assert max_items == 0
