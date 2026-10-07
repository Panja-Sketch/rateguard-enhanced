from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings managed via pydantic-settings with environment variable overrides."""

    app_name: str = "RateGuard AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    google_cloud_project: str = "rateguard-enhanced"
    google_cloud_region: str = "us-central1"
    log_level: str = "INFO"

    # Configurable data directory root
    data_dir: str | None = None

    # CORS configuration
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Optional scoped, read-only demo API key: lets a judge/insurer's own
    # script call the API directly (see README "External API Access")
    # without a Firebase session. Unset (default) disables the feature
    # entirely -- no header is ever accepted as a credential unless this is
    # explicitly configured. Always maps to VIEWER role only, never higher.
    demo_api_key: str | None = None
    demo_api_key_tenant_id: str = "demo"

    # BigQuery Configuration
    bigquery_enabled: bool = False
    bigquery_dataset: str = "rateguard"
    bigquery_portfolio_table: str = "synthetic_policies"
    bigquery_results_table: str = "portfolio_exposure_results"
    bigquery_location: str = "US"

    # Pub/Sub Async Workflow Configuration
    async_enabled: bool = True
    execution_mode: str = "pubsub"  # "pubsub" (production default) | "local" (in-memory test mode)
    pubsub_topic: str = "assurance-runs"
    pubsub_subscription: str = "assurance-worker"

    # REST rating-engine connector (locked doc section 8) — admin/config-
    # controlled registry entry for the one real demo connector
    # (`backend/rating_engine`). Never a per-mission arbitrary URL; see
    # app/connectors/registry.py. `rating_engine_connector_is_local_dev`
    # gates both the HTTPS-outside-local-development rule and the narrow
    # loopback SSRF exception (section 8.2) — never a blanket bypass.
    rating_engine_connector_base_url: str = "http://127.0.0.1:8000"
    rating_engine_connector_is_local_dev: bool = True
    # Optional bearer-style auth wiring for a *future* authenticated target;
    # the real demo target enforces no auth today (see D3). Never a
    # hardcoded secret value — only an env-var *name* to read the token
    # from at request time (never logged).
    rating_engine_connector_auth_header_name: str | None = None
    rating_engine_connector_auth_token_env_var: str | None = None
    # "none" (default) | "google_id_token": attach a Google-signed ID token
    # (audience = the connector's base URL) minted from the runtime service
    # account via ADC, for a private Cloud Run target. No secret is stored.
    rating_engine_connector_auth_mode: str = "none"
    # Google ID-token audience for `google_id_token` auth: the STABLE (untagged)
    # HTTPS service URL of the target. It is configured explicitly and is never
    # derived from the request endpoint, because a candidate endpoint is a
    # traffic-tagged URL (`<tag>---<service>...`) while Cloud Run validates the
    # token against the service URL. Validated at startup and on registration.
    rating_engine_connector_audience: str | None = None
    # Audience for the "vendor-gateway-demo" entry; falls back to the value above
    # (the same demo service exposes both wire shapes).
    vendor_gateway_connector_audience: str | None = None
    # Base URL for the second, differently-wire-shaped demo connector
    # ("vendor-gateway-demo", see app/connectors/registry.py). Defaults to
    # `rating_engine_connector_base_url` (the same demo service exposes
    # both `/quote*` and `/vendor/rate-quote`) so no extra deployment is
    # required; a real second vendor target only needs this overridden.
    vendor_gateway_connector_base_url: str | None = None

    # Which routes this process serves. The API and worker share one image
    # (locked doc 12.1) but must not share a surface: `api` serves the
    # Firebase-authenticated business API and NOT the internal Pub/Sub route;
    # `worker` serves only the Pub/Sub route (+ health); `all` (default, local
    # dev/tests) serves both.
    service_role: str = "all"

    # Authentication / authorization (locked doc 4.1.A, 15.2). The Firebase
    # project id is public configuration, not a secret; token verification
    # itself uses Application Default Credentials only.
    firebase_project_id: str = ""
    # Optional revocation check (needs roles/firebaseauth.viewer on the runtime
    # service account). Off by default; `users/{uid}.disabled` is the
    # server-side kill switch that never needs extra IAM.
    firebase_check_revoked: bool = False
    # Single-tenant challenge deployment: the tenant the bootstrap script
    # assigns by default. Server-controlled configuration only -- never read
    # from a request.
    challenge_tenant_id: str = "rateguard-demo"
    # Explicit migration switch for pre-tenant ("legacy") run records. Unset
    # (default) = legacy records are hidden from every user. Set to a tenant id
    # = legacy records are treated as belonging to that tenant.
    legacy_record_tenant_id: str | None = None
    # Distributed rate limiting (locked doc 15.2). Firestore-backed fixed window per
    # tenant + uid + operation; overrides look like {"mission_create": "10/3600"}
    # (limit/window_seconds). Defaults: app/ratelimit/policy.py.
    rate_limit_enabled: bool = True
    rate_limits: dict[str, str] = {}
    # Upper bound on any request body (uploads are separately capped by the
    # ingestion service); rejected with 413 before the body is fully buffered.
    max_request_bytes: int = 25 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_prefix="RATEGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings retriever."""
    return Settings()


def get_data_dir() -> Path:
    """Resolves the authoritative data directory for RateGuard AI runtime.

    Checks:
    1. RATEGUARD_DATA_DIR environment variable via Settings
    2. Repository root data directory (.../rateguard-enhanced/data)
    3. Container /app/data directory
    """
    settings = get_settings()
    if settings.data_dir:
        p = Path(settings.data_dir)
        if p.exists():
            return p

    # Check repository root data directory relative to app/core/config.py
    current = Path(__file__).resolve()
    backend_dir = current.parent.parent.parent
    repo_root_data = backend_dir.parent / "data"
    if repo_root_data.exists():
        return repo_root_data

    container_data = backend_dir / "data"
    if container_data.exists():
        return container_data

    return Path("data")
