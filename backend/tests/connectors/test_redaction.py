"""Log-redaction tests (locked doc sections 8.2 "never mission JSON or
logs", 15.4 "secrets are redacted from logs"). Proves a secret-shaped
string never appears unredacted in a captured log line produced by the
connector client."""

import logging
from datetime import date

import httpx
import pytest

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.connectors.errors import ConnectorException
from app.connectors.redact import scrub_secrets
from app.ipir.enums import TransactionType
from tests.connectors.conftest import make_registry_entry


def test_scrub_secrets_redacts_bearer_token():
    text = "request failed, header was Authorization: Bearer abcDEF123.456-token"
    scrubbed = scrub_secrets(text)
    assert "abcDEF123" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_scrub_secrets_redacts_google_api_key_shape():
    text = "used key AIzaSyD-abcdefghijklmnopqrstuvwxyz0123 for auth"
    scrubbed = scrub_secrets(text)
    assert "AIzaSyD-abcdefghijklmnopqrstuvwxyz0123" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_scrub_secrets_leaves_non_secret_text_untouched():
    text = "connector_id=rating-engine-demo status=timeout"
    assert scrub_secrets(text) == text


@pytest.mark.asyncio
async def test_secret_never_appears_unredacted_in_captured_log_output(caplog):
    """Exercises the real client's failure-logging path (a transport error
    whose exception text embeds a Bearer-token-shaped string) and asserts
    the captured log record never contains the raw secret."""
    secret_value = "Bearer sup3r-s3cret-service-token-should-not-leak"

    class ExplodingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"connection refused, tried auth header {secret_value}")

    entry = make_registry_entry()
    client = ConnectorClient(transport=ExplodingTransport(), max_attempts=1)
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=date(2026, 10, 15),
        transaction_type=TransactionType.RENEWAL,
        inputs={"roof_age": 25, "dwelling_limit": "300000.00"},
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ConnectorException):
            await client.send_quote_to_entry(entry, request)

    full_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "sup3r-s3cret-service-token-should-not-leak" not in full_log_text
