"""Opt-in `google_id_token` connector auth (private Cloud Run target).

Token minting is patched (no network/ADC). These tests exercise the
transport layer (`_do_request`), so the HTTPS / DNS / SSRF checks in
`send_quote_to_entry` are neither bypassed nor modified here; they keep
their own coverage in test_security.py.

The ID-token audience is an explicit setting (the stable, untagged service URL),
never derived from the request endpoint: a candidate deployment's endpoint is a
traffic-tagged URL, which Cloud Run rejects as an audience (HTTP 401).
"""

import httpx
import pytest
from pydantic import ValidationError

from app.connectors import client as client_module
from app.connectors.client import ConnectorClient
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.connectors.registry import ConnectorRegistryEntry
from tests.connectors.conftest import make_registry_entry

STABLE = "https://rateguard-rating-engine-abc123-uc.a.run.app"
TAGGED = "https://candidate---rateguard-rating-engine-abc123-uc.a.run.app"

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
        self.host: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.auth = request.headers.get("authorization")
        self.host = request.url.host
        return httpx.Response(200, json={})


def _entry(base_url: str = STABLE, audience: str | None = STABLE, **kw):
    return make_registry_entry(
        base_url=base_url,
        is_local_dev=False,
        auth_mode="google_id_token",
        audience=audience,
        **kw,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    client_module._id_token_cache.clear()


def _capture_audience(monkeypatch) -> dict:
    seen: dict = {}

    def fake_fetch(audience: str) -> str:
        seen["aud"] = audience
        return "tok123"

    monkeypatch.setattr(client_module, "_fetch_google_id_token", fake_fetch)
    return seen


@pytest.mark.asyncio
async def test_id_token_generated_for_the_configured_audience(monkeypatch):
    seen = _capture_audience(monkeypatch)
    transport = _RecordingTransport()
    status, _ = await ConnectorClient(transport=transport)._do_request(_entry(), PAYLOAD, "c1")
    assert status == 200
    assert transport.auth == "Bearer tok123"
    assert seen["aud"] == STABLE


@pytest.mark.asyncio
async def test_candidate_endpoint_and_stable_audience_can_differ(monkeypatch):
    seen = _capture_audience(monkeypatch)
    entry = _entry(base_url=TAGGED, audience=STABLE)
    assert entry.base_url == TAGGED and entry.audience == STABLE and entry.base_url != entry.audience
    transport = _RecordingTransport()
    await ConnectorClient(transport=transport)._do_request(entry, PAYLOAD, "c1")
    # The request goes to the tagged endpoint ...
    assert transport.host == "candidate---rateguard-rating-engine-abc123-uc.a.run.app"
    # ... but the token is minted for the stable service URL, not the tagged one.
    assert seen["aud"] == STABLE


@pytest.mark.asyncio
async def test_audience_is_normalised_without_trailing_slash(monkeypatch):
    seen = _capture_audience(monkeypatch)
    await ConnectorClient(transport=_RecordingTransport())._do_request(
        _entry(audience=STABLE + "/"), PAYLOAD, "c1"
    )
    assert seen["aud"] == STABLE


@pytest.mark.asyncio
async def test_missing_audience_fails_closed_and_never_derives_one_from_the_endpoint(monkeypatch):
    seen = _capture_audience(monkeypatch)
    # Registry construction refuses this configuration ...
    with pytest.raises(ValidationError):
        _entry(base_url=TAGGED, audience=None)
    # ... and the client independently refuses an entry that somehow lacks one.
    bare = ConnectorRegistryEntry.model_construct(**{**_entry().model_dump(), "audience": None, "base_url": TAGGED})
    transport = _RecordingTransport()
    with pytest.raises(ConnectorException) as exc:
        await ConnectorClient(transport=transport)._do_request(bare, PAYLOAD, "c1")
    assert exc.value.error.code == "CONNECTOR_AUTH_UNAVAILABLE"
    assert "aud" not in seen and transport.auth is None  # no token minted, no request sent


@pytest.mark.parametrize(
    "audience",
    [
        "http://rateguard-rating-engine-abc123-uc.a.run.app",  # not https
        TAGGED,  # tagged URL is never a valid audience
        STABLE + "/quote",  # path
        STABLE + "?x=1",  # query
        "https://user:pw@rateguard-rating-engine-abc123-uc.a.run.app",  # credentials
        "https://other-service-abc123-uc.a.run.app",  # different service than the endpoint
        "not-a-url",
        "",
    ],
)
def test_invalid_audience_is_rejected_at_registration(audience):
    with pytest.raises(ValidationError):
        _entry(base_url=TAGGED, audience=audience)


def test_audience_without_id_token_auth_is_inconsistent():
    with pytest.raises(ValidationError):
        make_registry_entry(audience=STABLE)


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
    # Registration refuses it ...
    with pytest.raises(ValidationError):
        make_registry_entry(auth_mode="google_id_token", audience=STABLE)  # is_local_dev=True by default
    # ... and so does the client, as defence in depth.
    entry = ConnectorRegistryEntry.model_construct(**{**_entry().model_dump(), "is_local_dev": True})
    with pytest.raises(ConnectorException) as exc:
        await ConnectorClient(transport=_RecordingTransport())._do_request(entry, PAYLOAD, "c1")
    assert exc.value.error.code == "CONNECTOR_AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_default_mode_sends_no_authorization_header():
    transport = _RecordingTransport()
    entry = make_registry_entry()
    assert entry.auth_mode == "none" and entry.audience is None
    await ConnectorClient(transport=transport)._do_request(entry, PAYLOAD, "c1")
    assert transport.auth is None
