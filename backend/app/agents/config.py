import os

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Operational guardrails (mandatory; never removed). Canonical, RATEGUARD_-prefixed
# environment variables, range-checked here (AgentConfig) and again at startup
# (app.core.runtime_config.resolve_guardrails). Defaults preserve the behaviour
# these limits had when they were module constants.
DEFAULT_MAX_GEMINI_CALLS_PER_MISSION = 6
DEFAULT_MAX_PROBE_ROUNDS = 1
DEFAULT_LOW_CONFIDENCE_REVIEW_THRESHOLD = 0.60


def _env_number(name: str, default: float, cast: type) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid {cast.__name__}.") from exc


def validate_gemini_model(model_name: str) -> str:
    """Validates the model against the locked model ID
    (docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md section 11.1).
    `gemini-2.0-flash-001` is explicitly discontinued per that doc; any other
    pre-3.1 model is rejected as below the locked floor."""
    norm = model_name.lower().strip()

    rejected_prefixes = [
        "gemini-1.0",
        "gemini-1.5",
        "gemini-2.0",
        "gemini-2.5",
    ]

    for prefix in rejected_prefixes:
        if norm.startswith(prefix):
            raise ValueError(
                f"Configured model '{model_name}' is below the locked model floor. "
                "RateGuard Enhanced requires 'gemini-3.1-flash-lite' (the locked model ID)."
            )

    return model_name


class AgentConfig(BaseModel):
    """Configuration settings for the Google GenAI SDK / Gemini structured-decision supervisor."""

    model_config = ConfigDict(validate_default=True)

    # Hard cap on Gemini calls in one mission (1-50).
    max_gemini_calls_per_mission: int = Field(
        default_factory=lambda: int(_env_number("RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION", DEFAULT_MAX_GEMINI_CALLS_PER_MISSION, int)),
        ge=1, le=50,
    )
    # Extra adaptive probe rounds Gemini may request (0-10).
    max_probe_rounds: int = Field(
        default_factory=lambda: int(_env_number("RATEGUARD_MAX_PROBE_ROUNDS", DEFAULT_MAX_PROBE_ROUNDS, int)),
        ge=0, le=10,
    )
    # Extraction confidence below this always requires human review (0.5-1.0).
    low_confidence_review_threshold: float = Field(
        default_factory=lambda: float(_env_number("RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD", DEFAULT_LOW_CONFIDENCE_REVIEW_THRESHOLD, float)),
        ge=0.5, le=1.0,
    )

    gemini_model: str = os.getenv("RATEGUARD_GEMINI_MODEL", "gemini-3.1-flash-lite")
    agent_enabled: bool = os.getenv("RATEGUARD_AGENT_ENABLED", "true").lower() == "true"
    run_store: str = os.getenv("RATEGUARD_RUN_STORE", "memory")
    google_cloud_project: str = os.getenv("RATEGUARD_GOOGLE_CLOUD_PROJECT", "rateguard-enhanced")
    # Vertex AI location: single canonical variable VERTEX_AI_LOCATION (locked
    # doc section 11.1 -> `us`). Enforced at startup by
    # app.core.runtime_config.resolve_ai_runtime_config.
    location: str = os.getenv("VERTEX_AI_LOCATION", "us")

    @field_validator("gemini_model")
    @classmethod
    def check_model_floor(cls, v: str) -> str:
        return validate_gemini_model(v)


def get_agent_config() -> AgentConfig:
    """Returns agent configuration settings."""
    return AgentConfig()
