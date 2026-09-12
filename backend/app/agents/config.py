import os

from pydantic import BaseModel, field_validator


def validate_gemini_model(model_name: str) -> str:
    """Validates model meets or exceeds Gemini 3.5+ hackathon floor."""
    norm = model_name.lower().strip()

    # Rejected pre-3.5 models
    rejected_prefixes = [
        "gemini-1.0",
        "gemini-1.5",
        "gemini-2.0",
        "gemini-2.5",
    ]

    for prefix in rejected_prefixes:
        if norm.startswith(prefix):
            raise ValueError(
                f"Configured model '{model_name}' is below Gemini 3.5+ floor. "
                "RateGuard AI requires Gemini 3.5 or newer (default: 'gemini-3.7-flash')."
            )

    return model_name


class AgentConfig(BaseModel):
    """Configuration settings for the Google GenAI SDK / Gemini structured-decision supervisor."""

    gemini_model: str = os.getenv("RATEGUARD_GEMINI_MODEL", "gemini-3.7-flash")
    agent_enabled: bool = os.getenv("RATEGUARD_AGENT_ENABLED", "true").lower() == "true"
    run_store: str = os.getenv("RATEGUARD_RUN_STORE", "memory")
    google_cloud_project: str = os.getenv("RATEGUARD_GOOGLE_CLOUD_PROJECT", "rateguard-ai")
    location: str = os.getenv("RATEGUARD_GEMINI_LOCATION", "us-central1")

    @field_validator("gemini_model")
    @classmethod
    def check_model_floor(cls, v: str) -> str:
        return validate_gemini_model(v)


def get_agent_config() -> AgentConfig:
    """Returns agent configuration settings."""
    return AgentConfig()
