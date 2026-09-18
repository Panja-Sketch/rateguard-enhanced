"""SSRF/destination-safety unit tests (locked doc sections 8.2, 15.4) and
one integration-level test proving a private/loopback/link-local/metadata
destination is rejected before the client ever attempts a connection, via
the *real* selection+validation path (`ConnectorClient.send_quote_to_entry`)
rather than only the underlying pure function. None of these tests make a
real DNS query or network call — every host used here is either an IP
literal (no resolver invoked at all) or `localhost`/a loopback IP served
by the OS-local hosts mechanism, not a real internet lookup.
"""

from datetime import date

import pytest

from app.connectors.client import ConnectorClient
from app.connectors.contract import ConnectorQuoteRequest
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.connectors.security import enforce_https_or_local_dev, resolve_and_validate_host
from app.ipir.enums import TransactionType
from tests.connectors.conftest import make_registry_entry

# -- enforce_https_or_local_dev -------------------------------------------------


def test_https_always_allowed():
    enforce_https_or_local_dev("https", "api.example.com", is_local_dev=False)


def test_http_rejected_outside_local_dev():
    with pytest.raises(ConnectorException) as excinfo:
        enforce_https_or_local_dev("http", "api.example.com", is_local_dev=False)
    assert excinfo.value.error.code == "CONNECTOR_INSECURE_SCHEME"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


def test_http_rejected_even_in_local_dev_for_non_loopback_host():
    """The local-dev exception is narrow: it only covers loopback
    hostnames, never a blanket bypass for any host once is_local_dev=True."""
    with pytest.raises(ConnectorException) as excinfo:
        enforce_https_or_local_dev("http", "internal.corp.example.com", is_local_dev=True)
    assert excinfo.value.error.code == "CONNECTOR_INSECURE_SCHEME"


def test_http_allowed_for_loopback_in_local_dev():
    enforce_https_or_local_dev("http", "127.0.0.1", is_local_dev=True)
    enforce_https_or_local_dev("http", "localhost", is_local_dev=True)


# -- resolve_and_validate_host -------------------------------------------------


def test_loopback_ip_literal_rejected_without_local_dev_flag():
    with pytest.raises(ConnectorException) as excinfo:
        resolve_and_validate_host("127.0.0.1", 80, allow_local_dev_loopback=False)
    assert excinfo.value.error.code == "CONNECTOR_DESTINATION_FORBIDDEN"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


def test_loopback_ip_literal_allowed_with_local_dev_flag():
    validated = resolve_and_validate_host("127.0.0.1", 80, allow_local_dev_loopback=True)
    assert validated == ["127.0.0.1"]


def test_private_rfc1918_address_rejected():
    with pytest.raises(ConnectorException) as excinfo:
        resolve_and_validate_host("10.1.2.3", 443, allow_local_dev_loopback=True)
    assert excinfo.value.error.code == "CONNECTOR_DESTINATION_FORBIDDEN"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE


def test_cloud_metadata_address_rejected():
    """The AWS/GCP-style link-local metadata address must be rejected even
    when the local-dev loopback exception is enabled — the exception is
    scoped to loopback only, never link-local generally."""
    with pytest.raises(ConnectorException) as excinfo:
        resolve_and_validate_host("169.254.169.254", 80, allow_local_dev_loopback=True)
    assert excinfo.value.error.code == "CONNECTOR_DESTINATION_FORBIDDEN"


def test_ipv6_metadata_style_link_local_rejected():
    with pytest.raises(ConnectorException) as excinfo:
        resolve_and_validate_host("fd00:ec2::254", 80, allow_local_dev_loopback=True)
    assert excinfo.value.error.code == "CONNECTOR_DESTINATION_FORBIDDEN"


def test_public_ip_literal_allowed():
    # A real public IP literal (documentation range TEST-NET-1 style value
    # substituted with a genuinely public, non-reserved-looking address)
    # is not disallowed by the private/loopback/link-local/reserved checks.
    # No network call is made - this only exercises the ipaddress checks.
    validated = resolve_and_validate_host("93.184.216.34", 443, allow_local_dev_loopback=False)
    assert validated == ["93.184.216.34"]


def test_localhost_hostname_treated_as_loopback():
    validated = resolve_and_validate_host("localhost", 80, allow_local_dev_loopback=True)
    assert validated == ["127.0.0.1"]


# -- integration: rejection happens before any request is attempted ------------


@pytest.mark.asyncio
async def test_client_rejects_private_destination_before_connecting():
    """A connector registry entry misconfigured (or compromised) to point
    at a private/metadata address must be rejected by the client itself,
    even with no transport wired up at all - proving the check runs before
    any connection attempt, not as an afterthought on a failed connect."""
    entry = make_registry_entry(
        base_url="https://169.254.169.254",
        is_local_dev=False,
    )
    client = ConnectorClient(transport=None)  # no transport - a real attempt would need real network
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=date(2026, 10, 15),
        transaction_type=TransactionType.RENEWAL,
        inputs={"roof_age": 25, "dwelling_limit": "300000.00"},
    )

    with pytest.raises(ConnectorException) as excinfo:
        await client.send_quote_to_entry(entry, request)

    assert excinfo.value.error.code == "CONNECTOR_DESTINATION_FORBIDDEN"
    assert excinfo.value.category == ConnectorFailureCategory.NON_RETRYABLE
