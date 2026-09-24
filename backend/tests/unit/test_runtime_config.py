"""Startup/configuration proof of the effective Gemini model and location, and
of the refusal to start on any missing/unsupported/inconsistent value."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.runtime_config import (
    LOCKED_GEMINI_MODEL,
    LOCKED_VERTEX_AI_LOCATION,
    RuntimeConfigError,
    resolve_ai_runtime_config,
)
from app.core.startup_checks import validate_startup_configuration
from app.ratelimit.policy import DEFAULT_POLICIES

GOOD = {"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite", "VERTEX_AI_LOCATION": "us"}


def test_locked_values_are_exactly_the_documented_ones():
    assert LOCKED_GEMINI_MODEL == "gemini-3.1-flash-lite"
    assert LOCKED_VERTEX_AI_LOCATION == "us"


def test_effective_model_and_location_are_proven_at_startup():
    settings = Settings(firebase_project_id="rateguard-test")
    summary = validate_startup_configuration(settings, GOOD)
    assert summary["gemini_model"] == "gemini-3.1-flash-lite"
    assert summary["vertex_ai_location"] == "us"


def test_process_environment_resolves_to_locked_values():
    """conftest pins the hermetic environment; this proves AgentConfig, the
    startup resolver and the Gemini client all agree on it."""
    from app.agents.config import AgentConfig
    from app.agents.gemini_client import GeminiDecisionClient

    cfg = resolve_ai_runtime_config()
    assert (cfg.model, cfg.location) == ("gemini-3.1-flash-lite", "us")
    agent = AgentConfig()
    assert (agent.gemini_model, agent.location) == ("gemini-3.1-flash-lite", "us")
    runtime = GeminiDecisionClient(agent).describe_runtime()
    assert runtime["configured_model_id"] == "gemini-3.1-flash-lite"


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"VERTEX_AI_LOCATION": "us"}, "RATEGUARD_GEMINI_MODEL is missing"),
        ({**GOOD, "RATEGUARD_GEMINI_MODEL": ""}, "RATEGUARD_GEMINI_MODEL is missing"),
        ({**GOOD, "RATEGUARD_GEMINI_MODEL": "gemini-2.0-flash-001"}, "unsupported model"),
        ({**GOOD, "RATEGUARD_GEMINI_MODEL": "gemini-3.7-flash"}, "unsupported model"),
        ({**GOOD, "RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite-preview"}, "unsupported model"),
        ({"RATEGUARD_GEMINI_MODEL": "gemini-3.1-flash-lite"}, "VERTEX_AI_LOCATION is missing"),
        ({**GOOD, "VERTEX_AI_LOCATION": "us-central1"}, "must be exactly 'us'"),
        ({**GOOD, "VERTEX_AI_LOCATION": "global"}, "must be exactly 'us'"),
        ({**GOOD, "GOOGLE_CLOUD_LOCATION": "us-central1"}, "inconsistent"),
        ({**GOOD, "GOOGLE_GENAI_USE_VERTEXAI": "false"}, "GOOGLE_GENAI_USE_VERTEXAI"),
    ],
)
def test_invalid_configuration_fails_startup(env, needle):
    with pytest.raises(RuntimeConfigError) as exc:
        resolve_ai_runtime_config(env)
    assert needle in str(exc.value)


@pytest.mark.parametrize(
    "legacy",
    ["GEMINI_MODEL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "FIREBASE_ADMIN_KEY_SECRET", "RATEGUARD_GEMINI_LOCATION"],
)
def test_forbidden_legacy_variables_fail_startup_without_echoing_values(legacy):
    secret_value = "AIzaSyDONOTLEAKTHISVALUE000000000000"
    with pytest.raises(RuntimeConfigError) as exc:
        resolve_ai_runtime_config({**GOOD, legacy: secret_value})
    assert legacy in str(exc.value)
    assert secret_value not in str(exc.value)


def test_conflicting_gemini_model_and_rateguard_gemini_model_is_rejected():
    with pytest.raises(RuntimeConfigError):
        resolve_ai_runtime_config({**GOOD, "GEMINI_MODEL": "gemini-2.0-flash-001"})


def test_local_env_file_is_validated_too(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("VERTEX_AI_LOCATION=us-central1\nGEMINI_MODEL=gemini-2.0-flash-001\n")
    with pytest.raises(RuntimeConfigError) as exc:
        # environ=None -> real process env + the .env file; drop the pinned test
        # values so the stale file content is what gets evaluated.
        import os

        from app.core import runtime_config

        saved = {k: os.environ.pop(k) for k in ("VERTEX_AI_LOCATION", "RATEGUARD_GEMINI_MODEL") if k in os.environ}
        try:
            runtime_config.resolve_ai_runtime_config(None, env_file)
        finally:
            os.environ.update(saved)
    message = str(exc.value)
    assert "GEMINI_MODEL must not be set" in message and "VERTEX_AI_LOCATION must be exactly 'us'" in message


def test_startup_requires_firebase_project_and_explicit_cors():
    with pytest.raises(RuntimeConfigError, match="FIREBASE_PROJECT_ID"):
        validate_startup_configuration(Settings(firebase_project_id=""), GOOD)
    with pytest.raises(RuntimeConfigError, match="wildcard"):
        validate_startup_configuration(Settings(firebase_project_id="p", cors_origins=["*"]), GOOD)
    with pytest.raises(RuntimeConfigError, match="at least one"):
        validate_startup_configuration(Settings(firebase_project_id="p", cors_origins=[]), GOOD)


def test_deployed_environments_reject_plain_http_origins_and_auth_emulator():
    with pytest.raises(RuntimeConfigError, match="https"):
        validate_startup_configuration(
            Settings(firebase_project_id="p", environment="candidate", cors_origins=["http://web.example.test"]), GOOD
        )
    with pytest.raises(RuntimeConfigError, match="EMULATOR"):
        validate_startup_configuration(
            Settings(firebase_project_id="p", environment="production", cors_origins=["https://w.example.test"]),
            {**GOOD, "FIREBASE_AUTH_EMULATOR_HOST": "localhost:9099"},
        )


def test_application_refuses_to_start_on_bad_model(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("RATEGUARD_GEMINI_MODEL", "gemini-2.0-flash-001")
    app = create_app(Settings(firebase_project_id="rateguard-test"))
    with pytest.raises(RuntimeConfigError), TestClient(app):
        pass


def test_application_starts_with_locked_configuration():
    from app.main import create_app

    with TestClient(create_app(Settings(firebase_project_id="rateguard-test"))) as c:
        assert c.get("/health/live").status_code == 200


def test_gemini_client_never_reads_or_passes_an_api_key(monkeypatch):
    from app.agents.config import AgentConfig
    from app.agents.gemini_client import AUTH_MODE_NONE, AUTH_MODE_VERTEX_AI, GeminiDecisionClient

    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaFAKE000000000000000000000000000")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKE000000000000000000000000000")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
    assert client._resolve_auth_mode() == (AUTH_MODE_NONE, {})

    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    mode, kwargs = client._resolve_auth_mode()
    assert mode == AUTH_MODE_VERTEX_AI
    assert kwargs["vertexai"] is True and kwargs["location"] == "us" and "api_key" not in kwargs


def test_no_tracked_runtime_file_references_retired_models_or_firebase_key():
    """Repo-wide guard: retired model ids, the old model variable, API keys and
    the Firebase private-key secret must not appear in runtime/deploy files."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    scan = [
        root / "backend" / ".env.example",
        root / "frontend" / ".env.example",
        *(root / "infrastructure").glob("*.yaml"),
        *(root / "infrastructure").glob("*.sh"),
        *(root / "infrastructure").glob("*.ps1"),
        *(root / "backend" / "app").rglob("*.py"),
    ]
    import re

    banned = re.compile(
        r"gemini-2\.0-flash-001|gemini-3\.7-flash|FIREBASE_ADMIN_KEY|(?<![A-Za-z_])GEMINI_MODEL(?![A-Za-z_])"
    )
    allowed_context = (
        "must not", "Do not", "never", "forbidden", "discontinued", "retired", "legacy",
        "Never", "NEVER", "no ", "No ",
    )
    offenders = []
    for path in scan:
        if not path.exists() or path.name == "runtime_config.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if banned.search(line) and not any(a in line for a in allowed_context):
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders)


def _connector_settings(env: dict[str, str]) -> dict:
    """The connector fields of `Settings`, taken from a deployed env mapping."""
    return dict(
        rating_engine_connector_base_url=env["RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL"],
        rating_engine_connector_audience=env["RATEGUARD_RATING_ENGINE_CONNECTOR_AUDIENCE"],
        rating_engine_connector_is_local_dev=env["RATEGUARD_RATING_ENGINE_CONNECTOR_IS_LOCAL_DEV"] == "true",
        rating_engine_connector_auth_mode=env["RATEGUARD_RATING_ENGINE_CONNECTOR_AUTH_MODE"],
        vendor_gateway_connector_base_url=env["RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL"],
        vendor_gateway_connector_audience=env["RATEGUARD_VENDOR_GATEWAY_CONNECTOR_AUDIENCE"],
    )


def _deploy_env_block(role: str) -> dict[str, str]:
    """Render the candidate script's env heredoc the way bash would, for `role`."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    script = (root / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    values = dict(re.findall(r'^([A-Z_]+)="([^"]*)"$', script, flags=re.M))
    block = script.split('cat > "$out" <<ENV\n', 1)[1].split("\nENV\n", 1)[0]
    env: dict[str, str] = {}
    for line in block.splitlines():
        key, _, raw = line.partition(": ")
        raw = raw.strip()
        if raw.startswith("'"):
            raw = raw.strip("'")
        else:
            raw = raw.strip('"')
        raw = raw.replace("${role}", role)
        # Values only known at deploy time (discovered after the engine deploys).
        raw = raw.replace("${RATING_ENGINE_TAGGED_URL}", "https://candidate---rateguard-rating-engine-abc123-uc.a.run.app")
        raw = raw.replace("${RATING_ENGINE_STABLE_URL}", "https://rateguard-rating-engine-abc123-uc.a.run.app")
        raw = raw.replace("${BACKEND_DIGEST}", "sha256:" + "0" * 64)
        for name, value in values.items():
            raw = raw.replace("${" + name + "}", value)
        env[key] = raw
    return env


@pytest.mark.parametrize("role", ["api", "worker"])
def test_candidate_cloud_run_env_satisfies_the_startup_contract(role):
    import json

    env = _deploy_env_block(role)
    assert env["RATEGUARD_SERVICE_ROLE"] == role
    assert env["RATEGUARD_GEMINI_MODEL"] == "gemini-3.1-flash-lite"
    assert env["VERTEX_AI_LOCATION"] == "us" and env["GOOGLE_CLOUD_LOCATION"] == "us"
    for forbidden in ("GEMINI_MODEL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "FIREBASE_ADMIN_KEY_SECRET"):
        assert forbidden not in env
    settings = Settings(
        firebase_project_id=env["RATEGUARD_FIREBASE_PROJECT_ID"],
        environment=env["RATEGUARD_ENVIRONMENT"],
        cors_origins=json.loads(env["RATEGUARD_CORS_ORIGINS"]),
        service_role=role,
        rate_limits=json.loads(env["RATEGUARD_RATE_LIMITS"]),
        **_connector_settings(env),
    )
    # Candidate: tagged request endpoint, stable audience - and they differ.
    assert settings.rating_engine_connector_base_url != settings.rating_engine_connector_audience
    assert "---" in settings.rating_engine_connector_base_url and "---" not in settings.rating_engine_connector_audience
    summary = validate_startup_configuration(settings, env)
    assert summary["gemini_model"] == "gemini-3.1-flash-lite" and summary["vertex_ai_location"] == "us"
    # Guardrails and rate limits are explicitly configured, not left to defaults.
    assert env["RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION"] == "10"
    assert env["RATEGUARD_MAX_PROBE_ROUNDS"] == "3"
    assert env["RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD"] == "0.8"
    assert summary["guardrails"]["RATEGUARD_MAX_PROBE_ROUNDS"] == 3
    assert env["RATEGUARD_RATE_LIMIT_ENABLED"] == "true"
    assert set(json.loads(env["RATEGUARD_RATE_LIMITS"])) == set(DEFAULT_POLICIES)


def test_production_baseline_env_file_satisfies_the_startup_contract():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3]
    env = yaml.safe_load((root / "infrastructure" / "runtime-env.rateguard-enhanced.yaml").read_text(encoding="utf-8"))
    import json

    settings = Settings(
        firebase_project_id=env["RATEGUARD_FIREBASE_PROJECT_ID"],
        environment=env["RATEGUARD_ENVIRONMENT"],
        cors_origins=json.loads(env["RATEGUARD_CORS_ORIGINS"]),
        **_connector_settings(env),
    )
    # Production: the stable URL is both the endpoint and the audience.
    assert settings.rating_engine_connector_base_url == settings.rating_engine_connector_audience
    summary = validate_startup_configuration(settings, {k: str(v) for k, v in env.items()})
    assert summary["vertex_ai_location"] == "us"
    assert summary["guardrails"] == {
        "RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION": 10,
        "RATEGUARD_MAX_PROBE_ROUNDS": 3,
        "RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD": 0.8,
    }


def test_env_examples_are_valid_and_carry_no_forbidden_variables():
    from pathlib import Path

    from dotenv import dotenv_values

    root = Path(__file__).resolve().parents[3]
    backend = {k: v for k, v in dotenv_values(root / "backend" / ".env.example").items() if v is not None}
    cfg = resolve_ai_runtime_config(backend)
    assert (cfg.model, cfg.location) == ("gemini-3.1-flash-lite", "us")
    from app.core.runtime_config import resolve_guardrails

    assert resolve_guardrails(backend) == {
        "RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION": 6,
        "RATEGUARD_MAX_PROBE_ROUNDS": 1,
        "RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD": 0.6,
    }
    assert backend["RATEGUARD_RATE_LIMIT_ENABLED"] == "true"
    frontend = dotenv_values(root / "frontend" / ".env.example")
    assert all(k.startswith("NEXT_PUBLIC_") for k in frontend)
    assert not any("KEY_SECRET" in k or "ADMIN" in k for k in {**backend, **frontend})
    assert not (frontend.get("NEXT_PUBLIC_FIREBASE_API_KEY") or "").startswith("AIza")  # placeholder, not a real key
