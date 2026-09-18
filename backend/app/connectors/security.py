"""SSRF / destination-safety checks for the REST rating-engine connector
(locked doc sections 8.2 and 15.4: "Connector allowlist prevents SSRF").

Design notes and the honest, documented residual limitation on DNS
rebinding live here (see also docs/implementation/DECISIONS.md D7):

`resolve_and_validate_host` resolves the connector's configured hostname
itself (via `socket.getaddrinfo`, or by parsing it directly if it is
already an IP literal) and validates every resolved address against
private/loopback/link-local/reserved ranges *immediately before* a request
is attempted, narrow-exempting `127.0.0.1`/`::1`/`localhost` only when the
specific registry entry being used is itself explicitly marked
`is_local_dev=True` (never a blanket bypass).

This mitigates, but does not eliminate, a DNS-rebinding race: the
resolve-then-validate pass here happens right before the connection is
opened, but the underlying HTTP transport (`httpx`) still connects by
hostname, not by pinning the specific IP object validated here. A resolver
that changed its answer in the (very small) window between this check and
the transport's own `connect()` call would not be caught by this function
alone. A fully rebinding-proof implementation would require a custom
`httpx` transport that dials a pre-resolved IP directly while still
sending the original `Host` header/SNI — judged out of scope for this
challenge; documented here rather than silently assumed away.
"""

from __future__ import annotations

import ipaddress
import socket

from app.connectors.errors import ConnectorException, ConnectorFailureCategory

# Only these exact loopback spellings are eligible for the local-dev
# exception, and only when the specific connector registry entry in use is
# itself explicitly marked `is_local_dev=True`.
LOCAL_DEV_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_disallowed_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Covers RFC1918 private ranges, loopback, link-local (including the
    169.254.169.254 / fd00:ec2::254-style cloud metadata addresses, which
    `ipaddress` classifies as link-local), and any other reserved/
    unspecified/multicast address."""
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def enforce_https_or_local_dev(scheme: str, host: str, *, is_local_dev: bool) -> None:
    """Locked doc section 8.2: "HTTPS required outside local development"."""
    if scheme == "https":
        return
    if scheme == "http" and is_local_dev and host in LOCAL_DEV_LOOPBACK_HOSTNAMES:
        return
    raise ConnectorException(
        code="CONNECTOR_INSECURE_SCHEME",
        message=(
            f"Connector destination uses scheme '{scheme}' for host '{host}'; "
            "HTTPS is required outside local development."
        ),
        category=ConnectorFailureCategory.NON_RETRYABLE,
        details=[{"scheme": scheme, "host": host}],
    )


def resolve_and_validate_host(
    host: str,
    port: int,
    *,
    allow_local_dev_loopback: bool,
) -> list[str]:
    """Resolves `host` and validates every resulting address. Returns the
    validated IP address strings. Raises `ConnectorException` (fail closed)
    on DNS failure or on any disallowed resolved address."""
    if host == "localhost":
        candidate_ips = ["127.0.0.1"]
    else:
        try:
            ipaddress.ip_address(host)
            candidate_ips = [host]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            except socket.gaierror as exc:
                raise ConnectorException(
                    code="CONNECTOR_DNS_RESOLUTION_FAILED",
                    message=f"Could not resolve connector host '{host}': {exc}",
                    category=ConnectorFailureCategory.RETRYABLE,
                ) from exc
            candidate_ips = sorted({info[4][0] for info in infos})

    validated: list[str] = []
    for ip_str in candidate_ips:
        ip_obj = ipaddress.ip_address(ip_str)
        if ip_obj.is_loopback and allow_local_dev_loopback:
            validated.append(ip_str)
            continue
        if _is_disallowed_ip(ip_obj):
            raise ConnectorException(
                code="CONNECTOR_DESTINATION_FORBIDDEN",
                message=(
                    f"Connector destination '{host}' resolved to disallowed "
                    f"address {ip_str} (private/loopback/link-local/reserved)."
                ),
                category=ConnectorFailureCategory.NON_RETRYABLE,
                details=[{"host": host, "resolved_ip": ip_str}],
            )
        validated.append(ip_str)

    if not validated:
        raise ConnectorException(
            code="CONNECTOR_DNS_RESOLUTION_FAILED",
            message=f"No usable addresses resolved for connector host '{host}'.",
            category=ConnectorFailureCategory.RETRYABLE,
        )
    return validated
