"""Startup-time validation of the locked AI runtime configuration.

Locked doc section 11 fixes exactly one model (`gemini-3.1-flash-lite`) served
from Vertex AI region `us` using Application Default Credentials. Section 11.2
and 15.1 additionally forbid Gemini API keys and a Firebase Admin private key
at runtime. This module is the single place that resolves and enforces those
rules; the application refuses to start (see `app.main`'s lifespan) when any
of them is violated, rather than silently running against a wrong model,
region, or credential type.

Canonical environment variables (one name per concept, repository-wide):

    RATEGUARD_GEMINI_MODEL   the model id (required, must equal the locked id)
    VERTEX_AI_LOCATION       the Vertex AI location (required, must be `us`)

Forbidden legacy names (startup fails if any is present in the process
environment or in the local `.env` file): GEMINI_MODEL, GEMINI_API_KEY,
GOOGLE_API_KEY, FIREBASE_ADMIN_KEY_SECRET, RATEGUARD_GEMINI_LOCATION. The
`GOOGLE_CLOUD_LOCATION` variable read by the google-genai SDK is tolerated only
when it equals `VERTEX_AI_LOCATION`.

Nothing here ever logs or returns a credential *value*; violations name only
the offending variable.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

LOCKED_GEMINI_MODEL = "gemini-3.1-flash-lite"
LOCKED_VERTEX_AI_LOCATION = "us"

MODEL_ENV_VAR = "RATEGUARD_GEMINI_MODEL"
LOCATION_ENV_VAR = "VERTEX_AI_LOCATION"
ENV_FILE_OVERRIDE_VAR = "RATEGUARD_ENV_FILE"

FORBIDDEN_ENV_VARS: dict[str, str] = {
    "GEMINI_MODEL": f"use {MODEL_ENV_VAR} (the single canonical model variable)",
    "GEMINI_API_KEY": "API keys are forbidden; Vertex AI uses Application Default Credentials",
    "GOOGLE_API_KEY": "API keys are forbidden; Vertex AI uses Application Default Credentials",
    "FIREBASE_ADMIN_KEY_SECRET": "a Firebase private key is forbidden; Firebase Admin uses ADC",
    "RATEGUARD_GEMINI_LOCATION": f"use {LOCATION_ENV_VAR} (the single canonical location variable)",
    # Legacy unprefixed guardrail names: conflict with the canonical RATEGUARD_* ones.
    "MAX_GEMINI_CALLS_PER_MISSION": "use RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION",
    "MAX_PROBE_ROUNDS": "use RATEGUARD_MAX_PROBE_ROUNDS",
    "LOW_CONFIDENCE_REVIEW_THRESHOLD": "use RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD",
}

GUARDRAIL_SPECS = {
    # name: (cast, minimum, maximum, default)
    "RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION": (int, 1, 50, 6),
    "RATEGUARD_MAX_PROBE_ROUNDS": (int, 0, 10, 1),
    "RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD": (float, 0.5, 1.0, 0.60),
}


class RuntimeConfigError(RuntimeError):
    """Raised when the effective runtime configuration violates the locked doc.

    The message lists variable *names* and the rule broken — never values that
    could be credentials."""


@dataclass(frozen=True)
class AIRuntimeConfig:
    model: str
    location: str
    project: str | None


def _env_file_path() -> Path:
    return Path(os.environ.get(ENV_FILE_OVERRIDE_VAR, ".env"))


def _merged_environment(environ: Mapping[str, str] | None, env_file: Path | None) -> dict[str, str]:
    """Process environment wins over the `.env` file, matching pydantic-settings."""
    merged: dict[str, str] = {}
    path = env_file if env_file is not None else _env_file_path()
    if environ is None and path.is_file():
        merged.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    merged.update(dict(os.environ if environ is None else environ))
    return merged


def resolve_ai_runtime_config(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> AIRuntimeConfig:
    """Resolves and validates the effective AI runtime configuration.

    `environ=None` means "the real process environment plus the local .env
    file"; passing a mapping (tests) uses exactly that mapping and no file.
    Raises `RuntimeConfigError` describing *every* violation found."""
    env = _merged_environment(environ, env_file)
    problems: list[str] = []

    for name, remedy in FORBIDDEN_ENV_VARS.items():
        if env.get(name, "").strip():
            problems.append(f"{name} must not be set: {remedy}.")

    model = env.get(MODEL_ENV_VAR, "").strip()
    if not model:
        problems.append(f"{MODEL_ENV_VAR} is missing; it must be '{LOCKED_GEMINI_MODEL}'.")
    elif model != LOCKED_GEMINI_MODEL:
        problems.append(
            f"{MODEL_ENV_VAR} is set to an unsupported model; it must be exactly '{LOCKED_GEMINI_MODEL}'."
        )

    location = env.get(LOCATION_ENV_VAR, "").strip()
    if not location:
        problems.append(f"{LOCATION_ENV_VAR} is missing; it must be '{LOCKED_VERTEX_AI_LOCATION}'.")
    elif location != LOCKED_VERTEX_AI_LOCATION:
        problems.append(
            f"{LOCATION_ENV_VAR} must be exactly '{LOCKED_VERTEX_AI_LOCATION}' (found '{location}')."
        )

    sdk_location = env.get("GOOGLE_CLOUD_LOCATION", "").strip()
    if sdk_location and location and sdk_location != location:
        problems.append(
            f"GOOGLE_CLOUD_LOCATION ('{sdk_location}') is inconsistent with {LOCATION_ENV_VAR} ('{location}')."
        )

    if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("0", "false"):
        problems.append("GOOGLE_GENAI_USE_VERTEXAI must not be false; Vertex AI (ADC) is the only Gemini path.")

    if problems:
        raise RuntimeConfigError("Invalid AI runtime configuration: " + " ".join(problems))

    project = (
        env.get("RATEGUARD_GOOGLE_CLOUD_PROJECT", "").strip()
        or env.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or env.get("GCP_PROJECT_ID", "").strip()
        or None
    )
    return AIRuntimeConfig(model=model, location=location, project=project)


def resolve_guardrails(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> dict[str, float]:
    """Validates the operational guardrail variables (Gemini call cap, probe-round
    cap, low-confidence review threshold). Unset means the documented default;
    a set value must parse and be inside its range or startup fails."""
    env = _merged_environment(environ, env_file)
    values: dict[str, float] = {}
    problems: list[str] = []
    for name, (cast, low, high, default) in GUARDRAIL_SPECS.items():
        raw = env.get(name, "").strip()
        if not raw:
            values[name] = default
            continue
        try:
            value = cast(raw)
        except ValueError:
            problems.append(f"{name} must be a valid {cast.__name__}.")
            continue
        if not (low <= value <= high):
            problems.append(f"{name} must be between {low} and {high}.")
            continue
        values[name] = value
    if problems:
        raise RuntimeConfigError("Invalid guardrail configuration: " + " ".join(problems))
    return values
