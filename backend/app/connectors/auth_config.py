"""Validation of connector authentication configuration.

A `google_id_token` connector sends a Google-signed ID token minted for an
*audience*. Cloud Run only accepts a token whose audience is the service's own
stable URL, but a candidate deployment reaches the service through a
traffic-tagged URL (`<tag>---<service>-<hash>-<region>.a.run.app`). Deriving the
audience from the request endpoint therefore fails Cloud Run authentication
(HTTP 401) before the request reaches the engine. The audience is consequently an
explicit, separately validated setting, and the checks here also stop a token
minted for one service being sent to a different one.
"""

from __future__ import annotations

from urllib.parse import urlsplit

AUTH_MODES = ("none", "google_id_token")
DEPLOYED_ENVIRONMENTS = ("production", "staging", "candidate")
TAG_SEPARATOR = "---"


def normalize_audience(audience: str) -> str:
    return audience.strip().rstrip("/")


def audience_problems(base_url: str, audience: str | None) -> list[str]:
    """Shape and consistency problems for an ID-token audience (empty if valid)."""
    if not audience or not audience.strip():
        return ["an explicit https ID-token audience (the stable service URL) is required"]
    parts = urlsplit(normalize_audience(audience))
    problems: list[str] = []
    if parts.scheme != "https":
        problems.append("the ID-token audience must use https")
    host = (parts.hostname or "").lower()
    if not host:
        problems.append("the ID-token audience must include a host")
        return problems
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/") or parts.port:
        problems.append("the ID-token audience must be a bare service origin (no credentials, port, path or query)")
    if TAG_SEPARATOR in host.split(".")[0]:
        problems.append("the ID-token audience must be the stable service URL, not a traffic-tagged URL")
    base_host = (urlsplit(base_url).hostname or "").lower()
    if base_host and base_host != host and not (
        base_host.endswith(TAG_SEPARATOR + host) and len(base_host) > len(TAG_SEPARATOR + host)
    ):
        problems.append(
            "the ID-token audience must be the same service as the connector endpoint "
            "(the endpoint may differ only by a traffic tag)"
        )
    return problems


def entry_problems(
    connector_id: str, base_url: str, is_local_dev: bool, auth_mode: str, audience: str | None
) -> list[str]:
    """Environment-independent consistency problems for one connector entry."""
    prefix = f"connector {connector_id}: "
    if auth_mode not in AUTH_MODES:
        return [prefix + f"auth mode must be one of {list(AUTH_MODES)}"]
    problems: list[str] = []
    if auth_mode == "google_id_token":
        if is_local_dev:
            problems.append(prefix + "google_id_token auth is not permitted for a local-dev connector")
        problems += [prefix + p for p in audience_problems(base_url, audience)]
    elif audience:
        problems.append(prefix + "an ID-token audience is set but auth mode is none")
    return problems


def deployed_problems(connector_id: str, base_url: str, is_local_dev: bool, auth_mode: str) -> list[str]:
    """Extra requirements in production/staging/candidate: the private engine must
    be reached over https with service-to-service authentication, never as an
    unauthenticated local-dev target."""
    prefix = f"connector {connector_id}: "
    problems: list[str] = []
    if is_local_dev:
        problems.append(prefix + "a local-dev connector is not permitted in a deployed environment")
    if auth_mode != "google_id_token":
        problems.append(prefix + "auth mode must be google_id_token in a deployed environment")
    if urlsplit(base_url).scheme != "https":
        problems.append(prefix + "the connector endpoint must use https in a deployed environment")
    return problems


def settings_problems(settings, *, deployed: bool) -> list[str]:
    """Problems across every connector the registry will build from `settings`
    (the two demo entries share one service). Empty means the configuration is
    consistent. Used at process startup so a bad configuration never serves."""
    demo_base = settings.rating_engine_connector_base_url
    demo_audience = settings.rating_engine_connector_audience
    targets = [
        ("rating-engine-demo", demo_base, demo_audience),
        (
            "vendor-gateway-demo",
            settings.vendor_gateway_connector_base_url or demo_base,
            settings.vendor_gateway_connector_audience or demo_audience,
        ),
    ]
    problems: list[str] = []
    for connector_id, base_url, audience in targets:
        is_local_dev = settings.rating_engine_connector_is_local_dev
        auth_mode = settings.rating_engine_connector_auth_mode
        problems += entry_problems(connector_id, base_url, is_local_dev, auth_mode, audience)
        if deployed:
            problems += deployed_problems(connector_id, base_url, is_local_dev, auth_mode)
    return problems
