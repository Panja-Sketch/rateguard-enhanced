"""Connector health testing (locked doc section 13.2: `POST
/connectors/{connector_id}/test` — "admin only; safe golden-case health
test").

This module provides the function, not a live FastAPI route — the task
scope for CP8 is the connector module itself, not the whole `/api/v1`
surface (no route currently exists to expose it, and none is added here;
see docs/implementation/DECISIONS.md D7 for this judgment call). Wiring an
actual authenticated `/api/v1/connectors/{connector_id}/test` route is
future integration-session work, same as the rest of the mission pipeline
per the CP8 scoping note in `app/connectors/__init__.py`.

`test_connector_health` runs the exact golden case (locked doc section
8.3: `roof_age=25` against `canonical-v1`, expecting exactly `$700.00`)
through the *full* client path — registry selection, allowlist, HTTPS/SSRF
checks, timeout, retry, schema validation — and reports pass/fail. It
never exposes the destination's credentials in its result, and it never
accepts an arbitrary target from its caller: only a `connector_id` already
present in the fixed registry.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.connectors.errors import ConnectorException
from app.ipir.enums import TransactionType

GOLDEN_CASE_INPUTS: dict[str, Any] = {"roof_age": 25, "dwelling_limit": "300000.00"}
GOLDEN_EFFECTIVE_DATE = date(2026, 10, 1)
GOLDEN_ENGINE_VERSION = "canonical-v1"
GOLDEN_EXPECTED_PREMIUM = "700.00"


async def test_connector_health(connector_id: str, *, client: ConnectorClient | None = None) -> dict[str, Any]:
    """Returns a plain, JSON-safe dict — never a credential, never the
    destination base URL — suitable for direct use as the body of a future
    admin-only `POST /connectors/{connector_id}/test` response."""
    client = client or ConnectorClient()
    request = ConnectorQuoteRequest(
        engine_version=GOLDEN_ENGINE_VERSION,
        product="az_ho3",
        effective_date=GOLDEN_EFFECTIVE_DATE,
        transaction_type=TransactionType.NEW_BUSINESS,
        inputs=GOLDEN_CASE_INPUTS,
    )
    try:
        response = await client.send_quote(connector_id, GOLDEN_ENGINE_VERSION, request)
    except ConnectorException as exc:
        return {
            "connector_id": connector_id,
            "healthy": False,
            "code": exc.error.code,
            "message": exc.error.message,
            "category": exc.error.category.value,
        }

    actual = response.outputs.get("final_premium")
    return {
        "connector_id": connector_id,
        "healthy": actual == GOLDEN_EXPECTED_PREMIUM,
        "actual_premium": actual,
        "expected_premium": GOLDEN_EXPECTED_PREMIUM,
    }
