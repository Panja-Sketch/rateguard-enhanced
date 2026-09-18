"""Fake ASGI target apps and shared fixtures for `tests/connectors/`.

No test in this package makes a real outbound network call. The golden/
canonical/defective tests exercise the *real* `backend/rating_engine`
service (`rating_engine.main.app`) through `httpx.ASGITransport` — real
ASGI request/response handling, real Pydantic validation on the target
side, no socket. The negative/security tests that the real demo target
cannot easily be made to exercise (auth failure, malformed JSON, slow
response, redirect, oversized response, retryable 429/5xx, wrong
request/engine id, incomplete outputs, float outputs, unsupported trace
nodes) use small purpose-built Starlette apps, also wired through
`httpx.ASGITransport` — this still exercises real HTTP semantics (real
status codes, real streaming, real asyncio-timeout behavior) without
touching the network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from app.connectors.registry import ConnectorRegistryEntry
from rating_engine.main import app as real_rating_engine_app

AUTH_TOKEN_ENV_VAR = "RATEGUARD_TEST_CONNECTOR_TOKEN"
# The env var holds the *complete* header value the client will send
# verbatim (see app.connectors.client._do_request) - not a bare token the
# client would prepend a scheme to.
AUTH_TOKEN_VALUE = "test-secret-token-abc123"  # pragma: allowlist secret - test fixture only
AUTH_HEADER_VALUE = f"Bearer {AUTH_TOKEN_VALUE}"


def make_registry_entry(**overrides: Any) -> ConnectorRegistryEntry:
    defaults: dict[str, Any] = dict(
        connector_id="test-connector",
        display_name="Test Connector",
        base_url="http://127.0.0.1:9999",
        allowed_engine_versions=("canonical-v1", "defective-v1"),
        is_local_dev=True,
    )
    defaults.update(overrides)
    return ConnectorRegistryEntry(**defaults)


def _ok_body(payload: dict, **overrides: Any) -> dict:
    body = {
        "request_id": payload["request_id"],
        "engine_version": payload["engine_version"],
        "outputs": {"final_premium": "700.00"},
        "trace": [],
        "rated_at": datetime.now(UTC).isoformat(),
    }
    body.update(overrides)
    return body


def make_auth_required_app() -> Starlette:
    async def quote(request: Request) -> Response:
        auth = request.headers.get("authorization")
        if auth != AUTH_HEADER_VALUE:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        payload = await request.json()
        return JSONResponse(_ok_body(payload))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_slow_app(delay_seconds: float) -> Starlette:
    async def quote(request: Request) -> Response:
        await asyncio.sleep(delay_seconds)
        payload = await request.json()
        return JSONResponse(_ok_body(payload))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_malformed_json_app() -> Starlette:
    async def quote(request: Request) -> Response:
        return Response(content=b"{not valid json at all!!", media_type="application/json", status_code=200)

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_redirect_app() -> Starlette:
    async def quote(request: Request) -> Response:
        return RedirectResponse(url="/quote-elsewhere", status_code=302)

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_oversized_app(oversized_field_bytes: int = 2 * 1024 * 1024) -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        huge_value = "9" * oversized_field_bytes
        return JSONResponse(_ok_body(payload, outputs={"final_premium": huge_value}))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_flaky_app(fail_times: int, fail_status: int) -> tuple[Starlette, dict]:
    state = {"calls": 0}

    async def quote(request: Request) -> Response:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            return JSONResponse({"error": "upstream busy"}, status_code=fail_status)
        payload = await request.json()
        return JSONResponse(_ok_body(payload))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])]), state


def make_wrong_request_id_app() -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        return JSONResponse(_ok_body(payload, request_id="not-the-id-you-sent"))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_wrong_engine_version_app() -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        return JSONResponse(_ok_body(payload, engine_version="some-other-version"))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_incomplete_outputs_app() -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        return JSONResponse(_ok_body(payload, outputs={"final_premium": "   "}))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_float_output_app() -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        return JSONResponse(_ok_body(payload, outputs={"final_premium": 700.0}))

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


def make_unsupported_trace_node_app() -> Starlette:
    async def quote(request: Request) -> Response:
        payload = await request.json()
        return JSONResponse(
            _ok_body(
                payload,
                trace=[{"node_id": "x", "node_type": "HACKED_NODE", "operation": "EVIL_OP", "result": "1"}],
            )
        )

    return Starlette(routes=[Route("/quote", quote, methods=["POST"])])


@pytest.fixture
def real_demo_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=real_rating_engine_app)


@pytest.fixture
def real_demo_entry() -> ConnectorRegistryEntry:
    return make_registry_entry(
        connector_id="rating-engine-demo-test",
        display_name="Test Demo Rating Engine",
        allowed_engine_versions=("canonical-v1", "defective-v1"),
    )
