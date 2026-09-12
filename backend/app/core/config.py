from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings managed via pydantic-settings with environment variable overrides."""

    app_name: str = "RateGuard AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    google_cloud_project: str = "rateguard-ai"
    google_cloud_region: str = "us-central1"
    log_level: str = "INFO"

    # Configurable data directory root
    data_dir: str | None = None

    # CORS configuration
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

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
    2. Repository root data directory (.../rateguard-ai/data)
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
