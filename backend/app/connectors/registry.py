"""Admin/config-controlled connector registry (locked doc section 13.2:
"GET /connectors - list safe metadata for configured connectors"; section
8.2: "Connector hosts are administrator allowlisted" / "Users cannot
provide a URL per mission").

Implementation-shape decision (see docs/implementation/DECISIONS.md D7):
this is a small, explicit, Pydantic-validated list loaded from
`Settings`/environment variables (`app.core.config`) — the same
config-driven pattern already used throughout this codebase — not a
database-backed CRUD admin UI. The locked doc's own section 18.1 scopes
Administration to "minimal connector and threshold display", not full
management; a persistent, mutable connector store is a frontend/future-
session concern, not this session's.

Exactly one real service is registered (under two wire shapes): the
`backend/rating_engine` demo service ("RateGuard Demo Insurer Rating Engine",
a black-box REST reference engine), with `canonical-v1`/`defective-v1` as its
declared allowed engine versions, mirroring the versions the engine itself
serves (`rating_engine/engines/versions.py`). RateGuard knows the engine only
through this registry and the REST contract, never through its code.

`select_connector` is the only mission-facing selection function. It takes
a `connector_id` and an `engine_version` — never a URL — and fails closed
(typed, specific exceptions) for an unregistered connector or an
undeclared engine version. It never falls back to a default connector.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator

from app.connectors.auth_config import entry_problems, normalize_audience
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.core.config import get_settings


class ConnectorRegistryEntry(BaseModel):
    """One administrator-configured connector. Never exposed to callers
    directly with its `base_url`/auth fields intact over an untrusted
    boundary — `list_connectors_metadata()` below strips those down to
    `ConnectorMetadata` for anything resembling the locked `GET
    /connectors` response."""

    model_config = ConfigDict(extra="forbid")

    connector_id: str
    display_name: str
    base_url: str
    allowed_engine_versions: tuple[str, ...]
    is_local_dev: bool = False
    # Optional bearer-style auth wiring for a *future* authenticated target.
    # The real `backend/rating_engine` demo target enforces no auth today
    # (see its own module docstring / D3) — these fields exist so the
    # connector client can attach a header from config-driven secret
    # material without any code change once a real target requires it.
    auth_header_name: str | None = None
    auth_token_env_var: str | None = None
    # "none" | "google_id_token" (see Settings.rating_engine_connector_auth_mode).
    auth_mode: str = "none"
    # Google ID-token audience: the stable (untagged) service URL. Configured
    # explicitly; never derived from `base_url` (which may be a traffic-tagged
    # candidate endpoint). Required for `google_id_token`, forbidden otherwise.
    audience: str | None = None
    # Which wire shape `app.connectors.client` speaks to this target's
    # single-quote endpoint (batch/capabilities are always the RateGuard-
    # native shape today; no registered target advertises a differently-
    # shaped batch contract yet). "rateguard_native_v1" is this repo's own
    # flat `rating_engine.models.QuoteRequest`/`QuoteResponse` contract;
    # "vendor_gateway_v1" is a genuinely different, nested/camelCase demo
    # contract (`rating_engine.vendor_gateway`) proving the client adapts to
    # more than one shape, not just its own contract under a new name.
    wire_format: str = "rateguard_native_v1"

    @model_validator(mode="after")
    def _validate_auth_configuration(self) -> ConnectorRegistryEntry:
        problems = entry_problems(self.connector_id, self.base_url, self.is_local_dev, self.auth_mode, self.audience)
        if problems:
            raise ValueError("; ".join(problems))
        if self.audience:
            object.__setattr__(self, "audience", normalize_audience(self.audience))
        return self

    def host_and_port(self) -> tuple[str, int]:
        parts = urlsplit(self.base_url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return host, port

    def scheme(self) -> str:
        return urlsplit(self.base_url).scheme


class ConnectorMetadata(BaseModel):
    """Safe, credential-free metadata only (locked doc section 13.2) —
    never the base URL, never a raw secret or auth-token env-var name."""

    model_config = ConfigDict(extra="forbid")

    connector_id: str
    display_name: str
    allowed_engine_versions: tuple[str, ...]
    wire_format: str
    last_health_check_status: str | None = None


class UnknownConnectorError(ConnectorException):
    """Raised when `connector_id` is not present in the configured
    registry. Fails closed — never silently falls back to a default
    connector."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(
            code="CONNECTOR_NOT_REGISTERED",
            message=f"Connector '{connector_id}' is not in the configured registry.",
            category=ConnectorFailureCategory.NON_RETRYABLE,
            details=[{"connector_id": connector_id}],
        )


class UnknownEngineVersionError(ConnectorException):
    """Raised when `engine_version` is not declared for the given
    connector. Fails closed."""

    def __init__(self, connector_id: str, engine_version: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            code="CONNECTOR_ENGINE_VERSION_NOT_ALLOWED",
            message=(
                f"Engine version '{engine_version}' is not declared for connector "
                f"'{connector_id}'. Allowed: {sorted(allowed)}."
            ),
            category=ConnectorFailureCategory.NON_RETRYABLE,
            details=[{"connector_id": connector_id, "engine_version": engine_version, "allowed": sorted(allowed)}],
        )


def _build_registry() -> dict[str, ConnectorRegistryEntry]:
    """Builds the fixed, administrator-configured connector list. Two demo
    entries are registered today, deliberately proving the registry
    generalizes beyond a single hardcoded connector: `rating-engine-demo`
    (this repo's own flat wire contract) and `vendor-gateway-demo` (a
    genuinely differently-shaped nested/camelCase contract modeled on a
    policy-admin-system-style vendor quote API — see
    `rating_engine.vendor_gateway`). Both currently point at the same
    `backend/rating_engine` service (its `/quote*` vs `/vendor/rate-quote`
    routes) purely to avoid standing up a second Cloud Run service for a
    hackathon-scope demo; a real second vendor target only requires adding
    another entry here with its own `base_url` and `wire_format` -- no
    other code changes -- which is the point being demonstrated."""
    settings = get_settings()
    demo_entry = ConnectorRegistryEntry(
        connector_id="rating-engine-demo",
        display_name="RateGuard Demo Insurer Rating Engine (black-box REST reference)",
        base_url=settings.rating_engine_connector_base_url,
        allowed_engine_versions=("canonical-v1", "defective-v1"),
        is_local_dev=settings.rating_engine_connector_is_local_dev,
        auth_header_name=settings.rating_engine_connector_auth_header_name,
        auth_token_env_var=settings.rating_engine_connector_auth_token_env_var,
        auth_mode=settings.rating_engine_connector_auth_mode,
        audience=settings.rating_engine_connector_audience,
        wire_format="rateguard_native_v1",
    )
    vendor_gateway_entry = ConnectorRegistryEntry(
        connector_id="vendor-gateway-demo",
        display_name="Vendor Gateway Demo (policy-admin-style API)",
        base_url=settings.vendor_gateway_connector_base_url or settings.rating_engine_connector_base_url,
        allowed_engine_versions=("canonical-v1", "defective-v1"),
        is_local_dev=settings.rating_engine_connector_is_local_dev,
        auth_header_name=settings.rating_engine_connector_auth_header_name,
        auth_token_env_var=settings.rating_engine_connector_auth_token_env_var,
        auth_mode=settings.rating_engine_connector_auth_mode,
        audience=settings.vendor_gateway_connector_audience or settings.rating_engine_connector_audience,
        wire_format="vendor_gateway_v1",
    )
    return {entry.connector_id: entry for entry in (demo_entry, vendor_gateway_entry)}


_REGISTRY_CACHE: dict[str, ConnectorRegistryEntry] | None = None


def get_registry() -> dict[str, ConnectorRegistryEntry]:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = _build_registry()
    return _REGISTRY_CACHE


def reset_registry_cache() -> None:
    """Test-only hook: forces the next `get_registry()` call to rebuild
    from current `Settings` (e.g. after monkeypatching connector env
    vars). Production code never needs to call this — the registry is
    fixed for the process lifetime, matching the "administrator
    allowlisted, not per-mission-mutable" design."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def select_connector(connector_id: str, engine_version: str) -> ConnectorRegistryEntry:
    """The one mission-facing selection function. Takes a `connector_id`
    (from the fixed registry) and an `engine_version` (validated against
    that connector's declared allowed versions) — never a URL. Fails
    closed on either being unregistered/undeclared."""
    registry = get_registry()
    entry = registry.get(connector_id)
    if entry is None:
        raise UnknownConnectorError(connector_id)
    if engine_version not in entry.allowed_engine_versions:
        raise UnknownEngineVersionError(connector_id, engine_version, entry.allowed_engine_versions)
    return entry


def list_connectors_metadata() -> list[ConnectorMetadata]:
    """Locked doc section 13.2: `GET /connectors` — safe metadata only."""
    return [
        ConnectorMetadata(
            connector_id=entry.connector_id,
            display_name=entry.display_name,
            allowed_engine_versions=entry.allowed_engine_versions,
            wire_format=entry.wire_format,
        )
        for entry in get_registry().values()
    ]
