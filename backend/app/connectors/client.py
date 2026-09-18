"""The real HTTP client for the REST rating-engine connector (locked doc
section 8, CP8). Calls a target's `/quote` endpoint over real HTTP
semantics (`httpx.AsyncClient`), translating between this connector's own
richer contract (`app.connectors.contract`) and the specific wire shape a
target expects (today, only `backend/rating_engine`'s
`rating_engine.models.QuoteRequest`/`QuoteResponse`).

Implements, in order, every control from locked doc section 8.2:

- destination allowlisting (only a registered `connector_id`, never a URL)
- HTTPS enforcement outside local development
- DNS resolution + private/loopback/link-local/metadata address rejection
- redirect denial (any 3xx is a hard failure, never followed)
- connect timeout 3s / request timeout 10s / mission-level budget 60s
- response-size cap (streamed, counted, aborted before unbounded buffering)
- strict response-schema validation (`extra="forbid"`)
- request-id/engine-version echo verification
- capped exponential backoff with jitter, retrying only safe/idempotent
  failures (timeouts, 429, 5xx) — never a 4xx application-level rejection
- secret retrieval via `Settings`/env var only, log redaction throughout
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.connectors.budget import TargetBudget
from app.connectors.contract import ConnectorQuoteRequest, ConnectorQuoteResponse
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.connectors.redact import scrub_secrets
from app.connectors.registry import ConnectorRegistryEntry, select_connector
from app.connectors.retry import MAX_ATTEMPTS, compute_backoff_delay
from app.connectors.security import enforce_https_or_local_dev, resolve_and_validate_host

logger = logging.getLogger(__name__)

# Locked doc section 8.2 exact numbers.
CONNECT_TIMEOUT_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 10.0

# A quote response is small JSON; 1 MiB is a conservative, generous cap
# that a legitimate response will never approach while still bounding an
# oversized/adversarial response before it is fully buffered.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024


class ConnectorClient:
    """Stateless-per-call HTTP client. `transport` and `sleep_fn` are
    injectable for tests (an `httpx.ASGITransport` wrapping a real FastAPI
    app for real-HTTP-semantics tests; a no-op sleep to keep retry tests
    fast)."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._transport = transport
        self._sleep_fn = sleep_fn
        self._max_attempts = max_attempts

    async def send_quote(
        self,
        connector_id: str,
        engine_version: str,
        request: ConnectorQuoteRequest,
        *,
        budget: TargetBudget | None = None,
        correlation_id: str | None = None,
    ) -> ConnectorQuoteResponse:
        """Mission-facing entrypoint: selects the connector by id/engine
        version (never a URL) from the fixed registry, then sends."""
        entry = select_connector(connector_id, engine_version)
        return await self.send_quote_to_entry(
            entry, request, budget=budget, correlation_id=correlation_id
        )

    async def send_quote_to_entry(
        self,
        entry: ConnectorRegistryEntry,
        request: ConnectorQuoteRequest,
        *,
        budget: TargetBudget | None = None,
        correlation_id: str | None = None,
    ) -> ConnectorQuoteResponse:
        """Lower-level entrypoint taking an already-resolved
        `ConnectorRegistryEntry` directly. Used internally by `send_quote`
        and by tests that need to exercise the client against a
        purpose-built fake target (e.g. one requiring auth) without
        mutating the process-wide registry singleton. Never used to accept
        a mission/user-supplied URL — `entry` always originates from the
        fixed registry or a test fixture, never request input."""
        correlation_id = correlation_id or str(uuid.uuid4())
        budget = budget or TargetBudget()

        scheme = entry.scheme()
        host, port = entry.host_and_port()
        enforce_https_or_local_dev(scheme, host, is_local_dev=entry.is_local_dev)
        resolve_and_validate_host(host, port, allow_local_dev_loopback=entry.is_local_dev)

        target_payload = self._to_target_payload(request, correlation_id)

        last_error: ConnectorException | None = None
        for attempt in range(1, self._max_attempts + 1):
            budget.check()
            try:
                status_code, body = await self._do_request(entry, target_payload, correlation_id)
                self._raise_for_status(status_code, correlation_id)
                return self._parse_response(body, request, correlation_id)
            except ConnectorException as exc:
                last_error = exc
                is_last_attempt = attempt == self._max_attempts
                if exc.category != ConnectorFailureCategory.RETRYABLE or is_last_attempt:
                    logger.warning(
                        "connector_request_failed correlation_id=%s connector_id=%s "
                        "attempt=%d code=%s message=%s",
                        correlation_id,
                        entry.connector_id,
                        attempt,
                        exc.error.code,
                        scrub_secrets(exc.error.message),
                    )
                    raise
                delay = compute_backoff_delay(attempt)
                logger.info(
                    "connector_request_retry correlation_id=%s connector_id=%s "
                    "attempt=%d delay_seconds=%.3f code=%s",
                    correlation_id,
                    entry.connector_id,
                    attempt,
                    delay,
                    exc.error.code,
                )
                await self._sleep_fn(delay)

        assert last_error is not None  # pragma: no cover - loop always sets/raises
        raise last_error

    # -- payload translation -------------------------------------------------

    def _to_target_payload(self, request: ConnectorQuoteRequest, correlation_id: str) -> dict[str, Any]:
        if request.jurisdiction is not None:
            # Jurisdiction is never silently dropped: it is recorded here in
            # the connector's own log line (for evidence/traceability) even
            # though the current demo target's wire contract has no
            # jurisdiction field and would reject an unknown one
            # (`extra="forbid"` on `rating_engine.models.QuoteRequest`).
            logger.info(
                "connector_jurisdiction_recorded correlation_id=%s jurisdiction=%s "
                "(carried in the connector's own contract for evidence; not "
                "forwarded to this target's wire payload)",
                correlation_id,
                request.jurisdiction,
            )
        return {
            "request_id": request.request_id,
            "engine_version": request.engine_version,
            "product_id": request.product,
            "effective_date": request.effective_date.isoformat(),
            "transaction_type": request.transaction_type.value,
            "inputs": request.inputs,
            "trace_requested": request.trace_requested,
        }

    # -- HTTP transport --------------------------------------------------------

    async def _do_request(
        self,
        entry: ConnectorRegistryEntry,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> tuple[int, bytes]:
        headers = {"X-Correlation-Id": correlation_id}
        if entry.auth_header_name and entry.auth_token_env_var:
            # The env var holds the *complete* header value (e.g.
            # "Bearer <token>" for an Authorization header) — the client
            # never assembles or guesses a scheme prefix, so config fully
            # controls the wire format for a future authenticated target.
            token = os.getenv(entry.auth_token_env_var)
            if token:
                headers[entry.auth_header_name] = token

        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=REQUEST_TIMEOUT_SECONDS,
            write=REQUEST_TIMEOUT_SECONDS,
            pool=REQUEST_TIMEOUT_SECONDS,
        )
        client_kwargs: dict[str, Any] = {
            "base_url": entry.base_url,
            "follow_redirects": False,
            "timeout": timeout,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream("POST", "/quote", json=payload, headers=headers) as response:
                    if 300 <= response.status_code < 400:
                        raise ConnectorException(
                            code="CONNECTOR_UNEXPECTED_REDIRECT",
                            message=(
                                f"Connector target returned redirect status "
                                f"{response.status_code}; redirects are denied."
                            ),
                            category=ConnectorFailureCategory.NON_RETRYABLE,
                            correlation_id=correlation_id,
                            details=[{"status_code": response.status_code}],
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ConnectorException(
                                code="CONNECTOR_RESPONSE_TOO_LARGE",
                                message=(
                                    f"Connector response exceeded the "
                                    f"{MAX_RESPONSE_BYTES}-byte limit."
                                ),
                                category=ConnectorFailureCategory.NON_RETRYABLE,
                                correlation_id=correlation_id,
                            )
                    return response.status_code, bytes(body)
        except httpx.TimeoutException as exc:
            raise ConnectorException(
                code="CONNECTOR_TIMEOUT",
                message=scrub_secrets(f"Connector request timed out: {exc}"),
                category=ConnectorFailureCategory.RETRYABLE,
                correlation_id=correlation_id,
            ) from exc
        except httpx.TransportError as exc:
            raise ConnectorException(
                code="CONNECTOR_TRANSPORT_ERROR",
                message=scrub_secrets(f"Connector transport error: {exc}"),
                category=ConnectorFailureCategory.RETRYABLE,
                correlation_id=correlation_id,
            ) from exc

    def _raise_for_status(self, status_code: int, correlation_id: str) -> None:
        if status_code == 200:
            return
        if status_code in (401, 403):
            raise ConnectorException(
                code="CONNECTOR_AUTH_DENIED",
                message=f"Connector target rejected authentication (status {status_code}).",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
                details=[{"status_code": status_code}],
            )
        if status_code == 429 or 500 <= status_code < 600:
            raise ConnectorException(
                code="CONNECTOR_UPSTREAM_ERROR",
                message=f"Connector target returned retryable status {status_code}.",
                category=ConnectorFailureCategory.RETRYABLE,
                correlation_id=correlation_id,
                details=[{"status_code": status_code}],
            )
        if 400 <= status_code < 500:
            raise ConnectorException(
                code="CONNECTOR_REQUEST_REJECTED",
                message=f"Connector target rejected the request (status {status_code}).",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
                details=[{"status_code": status_code}],
            )
        raise ConnectorException(
            code="CONNECTOR_UNEXPECTED_STATUS",
            message=f"Connector target returned unexpected status {status_code}.",
            category=ConnectorFailureCategory.NON_RETRYABLE,
            correlation_id=correlation_id,
            details=[{"status_code": status_code}],
        )

    # -- response parsing --------------------------------------------------------

    def _parse_response(
        self,
        body: bytes,
        request: ConnectorQuoteRequest,
        correlation_id: str,
    ) -> ConnectorQuoteResponse:
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorException(
                code="CONNECTOR_MALFORMED_JSON",
                message=f"Connector response was not valid JSON: {exc}",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
            ) from exc

        if not isinstance(raw, dict):
            raise ConnectorException(
                code="CONNECTOR_MALFORMED_JSON",
                message="Connector response JSON was not an object.",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
            )

        # Explicit pre-check: a response `outputs` value that is a JSON
        # number (not a JSON string) is rejected here, before Pydantic ever
        # sees it — so behavior never silently depends on whether Pydantic's
        # coercion mode happens to accept or reject a float-to-str
        # coercion. See tests/connectors/test_client_negative.py for a
        # verification of Pydantic's actual (non-)coercion behavior too.
        raw_outputs = raw.get("outputs")
        if isinstance(raw_outputs, dict):
            for key, value in raw_outputs.items():
                if not isinstance(value, str):
                    raise ConnectorException(
                        code="CONNECTOR_OUTPUT_NOT_DECIMAL_STRING",
                        message=(
                            f"Output '{key}' was a JSON {type(value).__name__}, "
                            "not a decimal string."
                        ),
                        category=ConnectorFailureCategory.NON_RETRYABLE,
                        correlation_id=correlation_id,
                        details=[{"output_key": key, "json_type": type(value).__name__}],
                    )

        try:
            response = ConnectorQuoteResponse.model_validate(raw)
        except ValidationError as exc:
            message = str(exc)
            code = (
                "CONNECTOR_UNSUPPORTED_TRACE_NODE"
                if "unsupported trace" in message
                else "CONNECTOR_SCHEMA_VIOLATION"
            )
            raise ConnectorException(
                code=code,
                message=scrub_secrets(message),
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
            ) from exc

        if response.request_id != request.request_id:
            raise ConnectorException(
                code="CONNECTOR_REQUEST_ID_MISMATCH",
                message="Connector response request_id did not match the request sent.",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
                details=[{"sent": request.request_id, "received": response.request_id}],
            )

        if response.engine_version != request.engine_version:
            raise ConnectorException(
                code="CONNECTOR_ENGINE_VERSION_MISMATCH",
                message="Connector response engine_version did not match the request sent.",
                category=ConnectorFailureCategory.NON_RETRYABLE,
                correlation_id=correlation_id,
                details=[{"sent": request.engine_version, "received": response.engine_version}],
            )

        # Minimal, documented incomplete-output-batch check (task scope: "a
        # documented, minimal check is fine ... don't over-build this"):
        # at least one non-empty output must be present. Per-product
        # expected-output-key validation is left as a future connector
        # config field (e.g. a `required_output_keys` mapping), not built
        # here.
        if not any(value.strip() for value in response.outputs.values()):
            raise ConnectorException(
                code="CONNECTOR_INCOMPLETE_OUTPUT_BATCH",
                message="Connector response contained no non-empty outputs.",
                category=ConnectorFailureCategory.REVIEW_REQUIRED,
                correlation_id=correlation_id,
            )

        # Jurisdiction is echoed back into the connector's own response
        # contract for evidence/traceability even though the current demo
        # target never returns it (see `_to_target_payload` above).
        if request.jurisdiction is not None:
            response = response.model_copy(update={"jurisdiction": request.jurisdiction})

        return response
