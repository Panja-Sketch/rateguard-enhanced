"""Fail-fast startup validation (locked doc 11, 15.1, 15.2).

`validate_startup_configuration` is called from the FastAPI lifespan; an
invalid configuration raises `RuntimeConfigError` and the process never starts
serving. It returns a small, non-sensitive summary (used by tests and the
startup log line) proving the *effective* model and location."""

import logging
import os
from collections.abc import Mapping
from typing import Any

from app.connectors.auth_config import DEPLOYED_ENVIRONMENTS, settings_problems
from app.core.config import Settings
from app.core.runtime_config import (
    RuntimeConfigError,
    resolve_ai_runtime_config,
    resolve_guardrails,
)
from app.impact.config import resolve_impact_config
from app.ratelimit.policy import RateLimitConfigError, resolve_policies

logger = logging.getLogger(__name__)


def validate_startup_configuration(
    settings: Settings,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    ai = resolve_ai_runtime_config(environ)
    guardrails = resolve_guardrails(environ)
    impact = resolve_impact_config(environ)  # unsafe/invalid impact budgets abort startup
    problems: list[str] = []

    if not settings.firebase_project_id.strip():
        problems.append("RATEGUARD_FIREBASE_PROJECT_ID is required (Firebase ID-token verification).")

    origins = settings.cors_origins
    if not origins:
        problems.append("RATEGUARD_CORS_ORIGINS must list at least one explicit web origin.")
    for origin in origins:
        if origin.strip() == "*" or "*" in origin:
            problems.append("RATEGUARD_CORS_ORIGINS must not contain a wildcard origin.")
            break

    if settings.environment.lower() in ("production", "staging", "candidate"):
        # The Firebase Admin SDK skips signature verification entirely when the
        # Auth emulator variable is present. Never allowed on a deployed service.
        if (environ if environ is not None else os.environ).get("FIREBASE_AUTH_EMULATOR_HOST"):
            problems.append("FIREBASE_AUTH_EMULATOR_HOST must not be set outside development.")
        for origin in origins:
            host_is_local = origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
            if not origin.startswith("https://") and not host_is_local:
                problems.append("Non-local CORS origins must use https.")
                break

    # Connector authentication: an explicit ID-token audience, consistent with the
    # endpoint, and (when deployed) authenticated https only - never derived
    # implicitly from a possibly traffic-tagged endpoint.
    problems.extend(settings_problems(settings, deployed=settings.environment.lower() in DEPLOYED_ENVIRONMENTS))

    try:
        policies = resolve_policies(settings.rate_limits)
    except RateLimitConfigError as exc:
        problems.append(str(exc))
        policies = {}
    if not settings.rate_limit_enabled and settings.environment.lower() in ("production", "staging", "candidate"):
        problems.append("RATEGUARD_RATE_LIMIT_ENABLED must not be false in a deployed environment.")

    if problems:
        raise RuntimeConfigError("Invalid startup configuration: " + " ".join(problems))

    summary = {
        "gemini_model": ai.model,
        "vertex_ai_location": ai.location,
        "auth": "firebase-adc",
        "cors_origin_count": len(origins),
        "guardrails": guardrails,
        "impact_budgets": {
            "max_policies": impact.max_policies,
            "batch_size": impact.batch_size,
            "global_request_concurrency": impact.global_request_concurrency,
            "max_qps": impact.max_qps,
            "max_mission_seconds": impact.max_mission_seconds,
        },
        "rate_limits": {op: f"{p.limit}/{p.window_seconds}" for op, p in policies.items()},
    }
    logger.info("STARTUP_CONFIG_OK %s", summary)
    return summary
