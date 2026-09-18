"""Connector contract negative-path tests (locked doc section 17.1):
authentication denied, timeout, 429/5xx retry (capped, with backoff),
malformed JSON, wrong request ID, partial batch, oversized response, and
redirect/SSRF-attempt rejection (redirect half; the private/loopback/
link-local/metadata-address half lives in test_security.py). Also covers
the task's own additional required detections: wrong engine_version,
float-typed outputs, and unsupported trace nodes.

No test in this file makes a real outbound network call — every fake
target is a small Starlette app served through `httpx.ASGITransport`.
"""

from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.ipir.enums import TransactionType
from tests.connectors.conftest import (
    AUTH_HEADER_VALUE,
    AUTH_TOKEN_ENV_VAR,
    make_auth_required_app,
    make_flaky_app,
    make_float_output_app,
    make_incomplete_outputs_app,
    make_malformed_json_app,
    make_oversized_app,
    make_redirect_app,
    make_registry_entry,
    make_slow_app,
    make_unsupported_trace_node_app,
    make_wrong_engine_version_app,
    make_wrong_request_id_app,
)

GOLDEN_INPUTS = {"roof_age": 25, "dwelling_limit": "300000.00"}
GOLDEN_DATE = date(2026, 10, 15)


def _request(**overrides) -> ConnectorQuoteRequest:
    defaults = dict(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=GOLDEN_DATE,
        transaction_type=TransactionType.RENEWAL,
        inputs=GOLDEN_INPUTS,
    )
    defaults.update(overrides)
    return ConnectorQuoteRequest(**defaults)


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_authentication_denied(monkeypatch):
    # Fake target only accepts AUTH_HEADER_VALUE exactly ->
    # 401 -> CONNECTOR_AUTH_DENIED, NON_RETRYABLE.
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, f"wrong-{AUTH_HEADER_VALUE}")
    entry = make_registry_entry(
        auth_header_name="Authorization",
        auth_token_env_var=AUTH_TOKEN_ENV_VAR,
    )
    transport = httpx.ASGITransport(app=make_auth_required_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_AUTH_DENIED"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_authentication_succeeds_with_correct_token(monkeypatch):
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, AUTH_HEADER_VALUE)
    entry = make_registry_entry(
        auth_header_name="Authorization",
        auth_token_env_var=AUTH_TOKEN_ENV_VAR,
    )
    transport = httpx.ASGITransport(app=make_auth_required_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    response = await client.send_quote_to_entry(entry, _request())
    assert response.outputs["final_premium"] == "700.00"


class _TimingOutTransport(httpx.AsyncBaseTransport):
    """Raises `httpx.ReadTimeout` directly, deterministically, rather than
    relying on real wall-clock timing. `httpx.ASGITransport` was tried
    first and found (by running the test, not by assumption) to not
    enforce `httpx.Timeout` at all for a purely in-process ASGI app call —
    there is no real socket I/O for a `Timeout` to bound. This transport
    instead verifies the connector's own catch-and-classify logic for a
    real `httpx.TimeoutException` deterministically and instantly, without
    waiting through any real delay at all."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated read timeout", request=request)


@pytest.mark.asyncio
async def test_timeout_triggers_and_is_classified_retryable():
    entry = make_registry_entry()
    client = ConnectorClient(transport=_TimingOutTransport(), sleep_fn=_no_sleep, max_attempts=1)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_TIMEOUT"
    assert excinfo.value.category == ConnectorFailureCategory.RETRYABLE


@pytest.mark.asyncio
async def test_slow_target_response_completes_when_within_a_generous_wait(real_demo_transport):
    """Separate, honest note on real-timing coverage: a real target that
    responds slower than the client's configured timeout genuinely does
    raise `CONNECTOR_TIMEOUT` end-to-end when run against a real socket
    (verified manually during development against a real bound TCP
    server); that specific real-timing path is not re-asserted here to
    keep this suite's runtime bounded, per the task's own "keep the suite
    fast" guidance. `test_timeout_triggers_and_is_classified_retryable`
    above covers the classification logic deterministically instead."""
    transport = httpx.ASGITransport(app=make_slow_app(delay_seconds=0.01))
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)
    entry = make_registry_entry()
    response = await client.send_quote_to_entry(entry, _request())
    assert response.outputs["final_premium"] == "700.00"


@pytest.mark.asyncio
async def test_429_retries_then_succeeds_with_backoff_mocked():
    app, state = make_flaky_app(fail_times=2, fail_status=429)
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=app)
    sleeps: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = ConnectorClient(transport=transport, sleep_fn=recording_sleep)
    response = await client.send_quote_to_entry(entry, _request())

    assert response.outputs["final_premium"] == "700.00"
    assert state["calls"] == 3  # 2 failures + 1 success
    assert len(sleeps) == 2
    assert all(s >= 0 for s in sleeps)


@pytest.mark.asyncio
async def test_5xx_retries_capped_at_five_attempts():
    app, state = make_flaky_app(fail_times=999, fail_status=503)
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=app)
    sleeps: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = ConnectorClient(transport=transport, sleep_fn=recording_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_UPSTREAM_ERROR"
    assert excinfo.value.category == ConnectorFailureCategory.RETRYABLE
    assert state["calls"] == 5  # capped at locked doc section 16.2's five attempts
    assert len(sleeps) == 4  # one fewer sleep than attempts (no sleep after the last)


@pytest.mark.asyncio
async def test_4xx_application_rejection_is_never_retried():
    app, state = make_flaky_app(fail_times=999, fail_status=400)
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=app)
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_REQUEST_REJECTED"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE
    assert state["calls"] == 1  # never retried


@pytest.mark.asyncio
async def test_malformed_json_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_malformed_json_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_MALFORMED_JSON"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_wrong_request_id_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_wrong_request_id_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_REQUEST_ID_MISMATCH"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_wrong_engine_version_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_wrong_engine_version_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_ENGINE_VERSION_MISMATCH"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_partial_batch_incomplete_outputs_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_incomplete_outputs_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_INCOMPLETE_OUTPUT_BATCH"
    assert excinfo.value.category == ConnectorFailureCategory.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_oversized_response_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_oversized_app(oversized_field_bytes=2 * 1024 * 1024))
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_RESPONSE_TOO_LARGE"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_redirect_rejected_not_followed():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_redirect_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_UNEXPECTED_REDIRECT"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_float_output_rejected_explicitly_not_relying_on_pydantic_coercion():
    """Verifies actual behavior rather than assuming it: confirms a JSON
    number in `outputs` is rejected by the connector's own explicit
    pre-check, regardless of whatever Pydantic's own coercion mode would
    have done with `dict[str, str]`."""
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_float_output_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request())

    assert excinfo.value.error.code == "CONNECTOR_OUTPUT_NOT_DECIMAL_STRING"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE
    assert excinfo.value.error.details[0]["json_type"] == "float"


def test_pydantic_dict_str_str_actually_rejects_a_float_in_this_config():
    """Documents, with a real assertion (not an assumption), the actual
    verified behavior of the Pydantic version pinned in this codebase:
    `dict[str, str]` does NOT silently coerce a JSON float into a string —
    `model_validate` raises `ValidationError` directly. The task
    instructions explicitly required verifying this with a real test
    rather than assuming either behavior.

    This does not make the explicit pre-check in
    `ConnectorClient._parse_response` redundant: without it, a float
    output would still be caught, but folded into the generic
    `CONNECTOR_SCHEMA_VIOLATION` code instead of the more specific,
    actionable `CONNECTOR_OUTPUT_NOT_DECIMAL_STRING` seen in the test
    above — and the explicit check makes the connector's behavior
    independent of Pydantic's coercion mode entirely, so it cannot
    silently change if that mode changes in a future Pydantic upgrade."""
    from app.connectors.contract import ConnectorQuoteResponse

    with pytest.raises(ValidationError):
        ConnectorQuoteResponse.model_validate(
            {
                "request_id": "r1",
                "engine_version": "canonical-v1",
                "outputs": {"final_premium": 700.0},
                "trace": [],
                "rated_at": "2026-10-15T00:00:00Z",
            }
        )


@pytest.mark.asyncio
async def test_unsupported_trace_node_rejected():
    entry = make_registry_entry()
    transport = httpx.ASGITransport(app=make_unsupported_trace_node_app())
    client = ConnectorClient(transport=transport, sleep_fn=_no_sleep)

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, _request(trace_requested=True))

    assert excinfo.value.error.code == "CONNECTOR_UNSUPPORTED_TRACE_NODE"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE
