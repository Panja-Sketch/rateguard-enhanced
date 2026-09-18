"""Golden/canonical/defective result tests (locked doc section 17.1
"canonical and defective results"; section 8.3), run through the real
registry-selection + client path + the real `backend/rating_engine`
service via `httpx.ASGITransport` (no network)."""

from datetime import date

import pytest

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.ipir.enums import TransactionType

GOLDEN_INPUTS = {"roof_age": 25, "dwelling_limit": "300000.00"}
GOLDEN_DATE = date(2026, 10, 15)


@pytest.mark.asyncio
async def test_canonical_result_is_700(real_demo_transport, real_demo_entry):
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    response = await client.send_quote_to_entry(real_demo_entry, request)
    assert response.outputs["final_premium"] == "700.00"
    assert response.request_id == request.request_id
    assert response.engine_version == "canonical-v1"


@pytest.mark.asyncio
async def test_defective_result_is_655(real_demo_transport, real_demo_entry):
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="defective-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    response = await client.send_quote_to_entry(real_demo_entry, request)
    assert response.outputs["final_premium"] == "655.00"


@pytest.mark.asyncio
async def test_trace_requested_returns_validated_nonempty_trace(real_demo_transport, real_demo_entry):
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
        trace_requested=True,
    )
    response = await client.send_quote_to_entry(real_demo_entry, request)
    assert len(response.trace) > 0
    # Every trace step round-tripped through the connector's own strict
    # `ConnectorTraceStep` model, which allowlist-validates node_type/operation.
    for step in response.trace:
        assert step.node_type
        assert step.operation


@pytest.mark.asyncio
async def test_jurisdiction_carried_in_connector_contract_but_not_forwarded_to_target(
    real_demo_transport, real_demo_entry
):
    """The demo target has no jurisdiction field; the connector must not
    silently drop caller-supplied jurisdiction. It is echoed back on the
    connector's own response contract even though the target never saw it."""
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        jurisdiction="US-AZ",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    response = await client.send_quote_to_entry(real_demo_entry, request)
    assert response.outputs["final_premium"] == "700.00"
    assert response.jurisdiction == "US-AZ"


@pytest.mark.asyncio
async def test_duplicate_request_id_returns_stable_response(real_demo_transport, real_demo_entry):
    """Idempotency scoping judgment call (documented in DECISIONS.md D7):
    the real demo target is stateless/deterministic per input, so "stable"
    here means "same inputs + same request_id -> same computed outputs",
    not a request-id-keyed response cache/store (which the demo target has
    no need for and this connector does not fabricate)."""
    client = ConnectorClient(transport=real_demo_transport)
    request = ConnectorQuoteRequest(
        request_id="fixed-request-id-for-idempotency-test",
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    first = await client.send_quote_to_entry(real_demo_entry, request)
    second = await client.send_quote_to_entry(real_demo_entry, request)
    assert first.request_id == second.request_id
    assert first.engine_version == second.engine_version
    assert first.outputs == second.outputs
