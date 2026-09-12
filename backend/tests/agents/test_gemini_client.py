"""Focused tests for GeminiDecisionClient against the google-genai==2.19.0
response shape. Every test injects a fake `client_factory` (or, for the
credential-resolution tests, monkeypatches only environment variables and
never reaches `google.genai.Client()` at all) — no test may reach a real
model or live GCP endpoint.
"""

from types import SimpleNamespace

import pytest

from app.agents.config import AgentConfig
from app.agents.decision_schemas import DifferencePrioritizationDecision
from app.agents.gemini_client import (
    AUTH_MODE_API_KEY,
    AUTH_MODE_NONE,
    AUTH_MODE_TEST_FAKE,
    AUTH_MODE_VERTEX_AI,
    FAILURE_BLOCKED_RESPONSE,
    FAILURE_DISABLED,
    FAILURE_EMPTY_RESPONSE,
    FAILURE_MALFORMED_RESPONSE,
    FAILURE_NO_CREDENTIALS,
    FAILURE_QUOTA,
    FAILURE_SCHEMA_INVALID,
    FAILURE_TIMEOUT,
    GeminiDecisionClient,
)

VALID_PAYLOAD = {
    "rationale": "Prioritize the two CRITICAL findings.",
    "confidence": 0.88,
    "needs_human_review": False,
    "selected_difference_ids": ["FND-AAAAAA", "FND-BBBBBB"],
}


def _make_response(*, response_id=None, usage=None, parsed=None, text=None, candidates=None, block_reason=None):
    return SimpleNamespace(
        response_id=response_id,
        usage_metadata=usage,
        parsed=parsed,
        text=text,
        candidates=candidates if candidates is not None else [SimpleNamespace()],
        prompt_feedback=SimpleNamespace(block_reason=block_reason) if block_reason is not None else None,
    )


class FakeModelsAPI:
    """Captures the `config` passed to generate_content so tests can assert on
    the actual GenerateContentConfig/HttpOptions the client built."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_call: dict | None = None

    def generate_content(self, model, contents, config):
        self.last_call = {"model": model, "contents": contents, "config": config}
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class FakeRawClient:
    def __init__(self, response=None, raise_exc=None):
        self.models = FakeModelsAPI(response, raise_exc)


def _client(models_api: FakeModelsAPI) -> GeminiDecisionClient:
    raw = SimpleNamespace(models=models_api)
    return GeminiDecisionClient(AgentConfig(), client_factory=lambda: raw)


def test_valid_response_parses_and_captures_response_id_and_usage():
    usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=45)
    parsed = DifferencePrioritizationDecision.model_validate(VALID_PAYLOAD)
    models_api = FakeModelsAPI(response=_make_response(response_id="RESP-123", usage=usage, parsed=parsed))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")

    assert decision is not None
    assert decision.selected_difference_ids == ["FND-AAAAAA", "FND-BBBBBB"]
    assert evidence.success is True
    assert evidence.schema_valid is True
    assert evidence.response_id == "RESP-123"
    assert evidence.input_tokens == 120
    assert evidence.output_tokens == 45
    assert evidence.model_id == "gemini-3.7-flash"
    assert evidence.auth_mode == AUTH_MODE_TEST_FAKE
    assert evidence.rationale == VALID_PAYLOAD["rationale"]


def test_generation_config_disables_automatic_function_calling():
    """No decision point ever registers a Python function tool — every call is
    a single bounded structured-output request. Automatic function calling
    must be explicitly disabled on the config actually sent to the SDK,
    rather than left at the SDK's enabled-by-default setting (which logs a
    "use Chat instead" warning regardless of whether any tool was registered,
    per google-genai 2.19.0's `Models.generate_content` implementation)."""
    parsed = DifferencePrioritizationDecision.model_validate(VALID_PAYLOAD)
    models_api = FakeModelsAPI(response=_make_response(parsed=parsed))
    client = _client(models_api)

    client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")

    sent_config = models_api.last_call["config"]
    assert sent_config.automatic_function_calling is not None
    assert sent_config.automatic_function_calling.disable is True
    # And no tool was ever registered for the SDK to automatically execute.
    assert not sent_config.tools


def test_response_delivered_as_raw_text_is_parsed_and_validated():
    """Some SDK paths return only `.text` (no auto-parsed `.parsed`); the
    client must still validate it against the Pydantic schema."""
    import json
    models_api = FakeModelsAPI(response=_make_response(text=json.dumps(VALID_PAYLOAD)))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is not None
    assert evidence.success is True


def test_blocked_response_is_a_classified_failure_not_a_crash():
    models_api = FakeModelsAPI(response=_make_response(candidates=[], block_reason="SAFETY"))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.success is False
    assert evidence.failure_category == FAILURE_BLOCKED_RESPONSE


def test_empty_candidates_without_block_reason_is_a_classified_failure():
    models_api = FakeModelsAPI(response=_make_response(candidates=[]))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.success is False
    assert evidence.failure_category == FAILURE_EMPTY_RESPONSE


def test_missing_parsed_and_missing_text_is_malformed_response():
    models_api = FakeModelsAPI(response=_make_response(parsed=None, text=None))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.failure_category == FAILURE_MALFORMED_RESPONSE


def test_invalid_json_text_is_schema_invalid_not_a_crash():
    models_api = FakeModelsAPI(response=_make_response(text="this is not valid json { at all"))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.failure_category == FAILURE_SCHEMA_INVALID


def test_quota_error_is_classified_and_retry_options_pin_a_single_attempt():
    from google.genai import errors as genai_errors

    exc = genai_errors.APIError(code=429, response_json={"error": {"message": "quota exceeded"}})
    models_api = FakeModelsAPI(raise_exc=exc)
    client = _client(models_api)

    decision, evidence = client.decide("PROPOSE_REMEDIATION", DifferencePrioritizationDecision, "sys", "prompt")

    assert decision is None
    assert evidence.failure_category == FAILURE_QUOTA
    # The call must still have been attempted with retries pinned to 1 —
    # a quota error must never disappear into hidden exponential backoff.
    sent_config = models_api.last_call["config"]
    assert sent_config.http_options.retry_options.attempts == 1


def test_timeout_exception_is_classified():
    models_api = FakeModelsAPI(raise_exc=TimeoutError("deadline exceeded"))
    client = _client(models_api)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.failure_category == FAILURE_TIMEOUT


def test_disabled_config_never_touches_the_client():
    models_api = FakeModelsAPI(response=_make_response(parsed=None, text=None))
    raw = SimpleNamespace(models=models_api)
    client = GeminiDecisionClient(AgentConfig(agent_enabled=False), client_factory=lambda: raw)

    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.failure_category == FAILURE_DISABLED
    assert models_api.last_call is None, "a disabled client must never invoke generate_content"


@pytest.mark.parametrize(
    ("env", "expected_mode"),
    [
        ({}, AUTH_MODE_NONE),
        ({"GOOGLE_API_KEY": "fake-key-value"}, AUTH_MODE_API_KEY),
        ({"GEMINI_API_KEY": "fake-key-value"}, AUTH_MODE_API_KEY),
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, AUTH_MODE_VERTEX_AI),
        # Vertex AI takes precedence when both are present, and only one mode
        # is ever selected — never both simultaneously.
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_API_KEY": "fake-key-value"}, AUTH_MODE_VERTEX_AI),
    ],
)
def test_auth_mode_resolution_selects_exactly_one_mode(monkeypatch, env, expected_mode):
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    client = GeminiDecisionClient(AgentConfig())
    mode, kwargs = client._resolve_auth_mode()
    assert mode == expected_mode
    assert len(kwargs) <= 1, "exactly one authentication kwarg (or none) must be selected, never both"


def test_no_credentials_short_circuits_before_touching_any_client(monkeypatch):
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(var, raising=False)

    client = GeminiDecisionClient(AgentConfig())  # no client_factory: real path, but must short-circuit first
    decision, evidence = client.decide("PRIORITIZE_DIFFERENCES", DifferencePrioritizationDecision, "sys", "prompt")
    assert decision is None
    assert evidence.failure_category == FAILURE_NO_CREDENTIALS
    assert evidence.auth_mode == AUTH_MODE_NONE


class TestDescribeRuntime:
    """Focused tests for GeminiDecisionClient.describe_runtime() -- the
    single source of truth /api/v1/system/status now reads from, so that
    endpoint and real decide() calls can never report different auth modes
    or locations. Every test here monkeypatches only environment variables
    and never reaches google.genai.Client() -- describe_runtime() must not
    either (see test_describe_runtime_never_constructs_a_client below)."""

    @staticmethod
    def _clear_auth_env(monkeypatch):
        for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_LOCATION"):
            monkeypatch.delenv(var, raising=False)

    def test_vertex_ai_configuration(self, monkeypatch):
        self._clear_auth_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()

        assert runtime["configured_model_id"] == "gemini-3.7-flash"
        assert runtime["provider"] == "Google Vertex AI"
        assert "Google GenAI SDK" in runtime["framework"]
        assert runtime["auth_mode"] == AUTH_MODE_VERTEX_AI
        assert runtime["configured_location"] == "global"
        assert runtime["agent_enabled"] is True

    def test_global_location_is_reported_exactly_as_configured_not_the_gcp_region(self, monkeypatch):
        """Regression: the candidate/production deployment sets
        GOOGLE_CLOUD_LOCATION=global (see infrastructure/runtime-env.yaml),
        which is what GeminiDecisionClient's own Vertex AI client resolution
        actually uses -- never the general Cloud Run/GCP deployment region
        (e.g. 'us-central1'), which is a different, unrelated setting."""
        self._clear_auth_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()

        assert runtime["configured_location"] == "global"
        assert runtime["configured_location"] != "us-central1"

    def test_api_key_configuration(self, monkeypatch):
        self._clear_auth_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-value")

        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()

        assert runtime["provider"] == "Google Gemini API"
        assert runtime["auth_mode"] == AUTH_MODE_API_KEY
        # The Gemini Developer API has no location concept -- must not
        # fabricate one, and must never echo the API key itself.
        assert runtime["configured_location"] is None
        assert "fake-key-value" not in str(runtime)

    def test_disabled_or_no_auth_configuration(self, monkeypatch):
        self._clear_auth_env(monkeypatch)

        # No credentials configured at all, agent enabled.
        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()
        assert runtime["auth_mode"] == AUTH_MODE_NONE
        assert runtime["provider"] == "Not configured"
        assert runtime["configured_location"] is None

        # Agent explicitly disabled, even with Vertex AI credentials present --
        # decide() would never reach _resolve_auth_mode() in this case, so
        # describe_runtime() must not misleadingly report VERTEX_AI either.
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        disabled_client = GeminiDecisionClient(AgentConfig(agent_enabled=False))
        disabled_runtime = disabled_client.describe_runtime()
        assert disabled_runtime["auth_mode"] == AUTH_MODE_NONE
        assert disabled_runtime["agent_enabled"] is False

    def test_describe_runtime_never_constructs_a_client_or_touches_the_network(self, monkeypatch):
        self._clear_auth_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

        def _fail_if_constructed(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("describe_runtime() must never construct a google.genai.Client")

        monkeypatch.setattr("google.genai.Client", _fail_if_constructed)

        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()  # must not raise
        assert runtime["auth_mode"] == AUTH_MODE_VERTEX_AI

    def test_describe_runtime_never_exposes_secret_shaped_values(self, monkeypatch):
        self._clear_auth_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaFAKESECRETVALUEDONOTUSE0000")

        client = GeminiDecisionClient(AgentConfig(agent_enabled=True))
        runtime = client.describe_runtime()

        serialized = str(runtime)
        for forbidden in ("AIza", "Bearer ", "-----BEGIN", "AIzaFAKESECRETVALUEDONOTUSE0000"):
            assert forbidden not in serialized
