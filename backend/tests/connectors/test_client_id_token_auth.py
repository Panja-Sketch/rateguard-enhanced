"""Opt-in `google_id_token` connector auth (private Cloud Run target).

Token minting is patched (no network/ADC). These tests exercise the
transport layer (`_do_request`), so the HTTPS / DNS / SSRF checks in
`send_quote_to_entry` are neither bypassed nor modified here; they keep
their own coverage in test_security.py.
"""

import httpx
import pytest

from app.connectors import client as client_module
from app.connectors.client import ConnectorClient
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from tests.connectors.conftest import make_registry_entry

PAYLOAD = {
    "request_id": "r1",
    "engine_version": "canonical-v1",
    "product_id": "az_ho3",
    "effective_date": "2026-10-15",
    "transaction_type": "RENEWAL",
    "inputs": {},
    "trace_requested": False,
}


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.auth: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.auth = request.headers.get("authorization")
        return httpx.Response(200, json={})


def _entry(**kw):
    return make_registry_entry(
        base_url="https://engine.example.run.app",
        is_local_dev=False,
        auth_mode="google_id_token",
        **kw,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    client_module._id_token_cache.clear()


@pytest.mark.asyncio
async def test_id_token_attached_with_base_url_audience(monkeypatch):
    seen = {}

    def fake_fetch(audience: str) -> str:
        seen["aud"] = audience
        return "tok123"

    monkeypatch.setattr(client_module, "_fetch_google_id_token", fake_fetch)
    transport = _RecordingTransport()
    status, _ = await ConnectorClient(transport=transport)._do_request(_entry(), PAYLOAD, "c1")
    assert status == 200
    assert transport.auth == "Bearer tok123"
    assert seen["aud"] == "https://engine.example.run.app"


@pytest.mark.asyncio
async def test_id_token_unavailable_fails_closed_without_leaking(monkeypatch):
    def boom(_aud: str) -> str:
        raise RuntimeError("internal-detail-should-not-leak")

    monkeypatch.setattr(client_module, "_fetch_google_id_token", boom)
    transport = _RecordingTransport()
    with pytest.raises(ConnectorException) as exc:
        await ConnectorClient(transport=transport)._do_request(_entry(), PAYLOAD, "c1")
    assert exc.value.error.code == "CONNECTOR_AUTH_UNAVAILABLE"
    assert exc.value.category == ConnectorFailureCategory.NON_RETRYABLE
    assert "internal-detail" not in exc.value.error.message
    assert transport.auth is None  # no request was sent


@pytest.mark.asyncio
async def test_id_token_mode_refused_for_local_dev(monkeypatch):
    monkeypatch.setattr(client_module, "_fetch_google_id_token", lambda a: "x")
    entry = make_registry_entry(auth_mode="google_id_token")  # is_local_dev=True by default
    with pytest.raises(ConnectorException) as exc:
        await ConnectorClient(transport=_RecordingTransport())._do_request(entry, PAYLOAD, "c1")
    assert exc.value.error.code == "CONNECTOR_AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_default_mode_sends_no_authorization_header():
    transport = _RecordingTransport()
    entry = make_registry_entry()
    assert entry.auth_mode == "none"
    await ConnectorClient(transport=transport)._do_request(entry, PAYLOAD, "c1")
    assert transport.auth is None
