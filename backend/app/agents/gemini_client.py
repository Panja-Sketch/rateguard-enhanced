"""Thin, honest wrapper around a single structured Gemini call.

Every call goes through `GeminiDecisionClient.decide()`, which always returns
a `GeminiInvocationEvidence` record — whether the call succeeded, failed, or
was never attempted (Gemini disabled, no credentials configured). Callers use
that evidence to build an `AgentAction` for the timeline and to decide whether
to fall back to deterministic behavior. Nothing here ever fabricates a
response or silently upgrades a failure into a fake success.

Reviewed against the installed google-genai==2.19.0 SDK:
  - the SDK retries up to 5 times (including on HTTP 429) by default, so every
    call here explicitly pins `retry_options=HttpRetryOptions(attempts=1)` —
    a quota error must surface immediately as a classified fallback, not
    disappear into ~30s of hidden exponential backoff.
  - the client is constructed fresh per call inside a `with` block (it
    implements the context-manager protocol) so its underlying HTTP session
    is always closed, rather than caching one Client for the process lifetime.
  - exactly one authentication mode is resolved explicitly by this class
    (Vertex AI ADC, then an API key, then none) and recorded on the evidence —
    the bare SDK default of letting `Client()` sniff environment variables
    itself is not used, so the active mode is always observable, not implicit.
  - `APIError.__str__` is built only from `(code, status, response_json)` —
    the server's JSON error body — and auth reaches the SDK via HTTP headers
    (`x-goog-api-key` / `Authorization: Bearer`), never a URL query string, so
    no exception raised by this SDK version can echo a credential back into a
    log line. `_scrub_secrets` below is kept anyway as defense-in-depth.
  - the SDK enables automatic function calling (AFC) by default on every
    `Models.generate_content` call unless `automatic_function_calling.disable`
    is explicitly set — logging its "use Chat instead" warning regardless of
    whether any callable tool was ever registered. No decision point here
    ever passes `tools=`; every call is a single bounded structured-output
    request, so AFC is explicitly disabled rather than left at the default.
"""

import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.config import AgentConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FailureCategory = str  # one of the FAILURE_* constants below

FAILURE_DISABLED = "DISABLED"
FAILURE_NO_CREDENTIALS = "NO_CREDENTIALS"
FAILURE_TIMEOUT = "TIMEOUT"
FAILURE_QUOTA = "QUOTA_EXCEEDED"
FAILURE_UNAVAILABLE = "MODEL_UNAVAILABLE"
FAILURE_BLOCKED_RESPONSE = "BLOCKED_RESPONSE"
FAILURE_EMPTY_RESPONSE = "EMPTY_RESPONSE"
FAILURE_MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
FAILURE_SCHEMA_INVALID = "SCHEMA_INVALID"

AUTH_MODE_VERTEX_AI = "VERTEX_AI"
AUTH_MODE_API_KEY = "API_KEY"
AUTH_MODE_NONE = "NONE"
AUTH_MODE_TEST_FAKE = "TEST_FAKE"

DEFAULT_TIMEOUT_MS = 20_000
PROMPT_VERSION = "assurance-supervisor-v1"

# Defense-in-depth only (see module docstring: this SDK never places a
# credential where an exception could echo it). Redacts anything that looks
# like a bearer token or a Google API key before it reaches a log line.
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z_-]{10,}"),
)


def _scrub_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class GeminiInvocationEvidence(BaseModel):
    """Persisted, user-facing evidence for exactly one Gemini call attempt."""

    invocation_id: str
    model_id: str
    auth_mode: str = AUTH_MODE_NONE
    response_id: str | None = None
    prompt_version: str = PROMPT_VERSION
    decision_type: str
    requested_tool: str | None = None
    started_at: str
    ended_at: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    success: bool
    schema_valid: bool | None = None
    failure_category: str | None = None
    rationale: str | None = None
    confidence: float | None = None
    needs_human_review: bool = False


class GeminiDecisionClient:
    """Executes one structured-output Gemini call per decision point.

    `client_factory`, when provided, replaces the real `google.genai.Client()`
    construction — tests inject a fake factory so no test ever reaches a real
    model or live GCP endpoint.
    """

    def __init__(self, config: AgentConfig, client_factory: Any = None) -> None:
        self.config = config
        self._client_factory = client_factory

    def _resolve_auth_mode(self) -> tuple[str, dict[str, Any]]:
        """Explicitly resolves exactly one authentication mode, in a fixed,
        observable precedence: Vertex AI (ADC) > API key > none. This class
        decides — it never lets the bare SDK's own environment-sniffing
        silently pick a mode for us, so the active mode is always recorded."""
        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("1", "true"):
            return AUTH_MODE_VERTEX_AI, {"vertexai": True}
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            return AUTH_MODE_API_KEY, {"api_key": api_key}
        return AUTH_MODE_NONE, {}

    def describe_runtime(self) -> dict[str, Any]:
        """Reports the AI runtime this client would use for its next call —
        model id, provider, framework, resolved auth mode, and (for Vertex AI
        only) the effective location — using the exact same
        `_resolve_auth_mode()` this class's own `decide()` uses, so runtime
        execution and this report can never drift apart.

        Read-only by construction: reads only `self.config` and environment
        variables already read by `_resolve_auth_mode()`. Never constructs a
        `google.genai.Client`, never makes a network call, never validates a
        credential, and never returns a credential value (only the enum-like
        `auth_mode` label). Safe to call from a diagnostics endpoint.

        A disabled agent short-circuits to AUTH_MODE_NONE here too, matching
        `decide()`'s own behavior — when disabled, `decide()` never reaches
        `_resolve_auth_mode()`, so reporting whatever credentials happen to
        be present would misleadingly suggest Gemini could be reached when
        it will not be invoked at all.
        """
        if not self.config.agent_enabled:
            auth_mode = AUTH_MODE_NONE
        else:
            auth_mode, _ = self._resolve_auth_mode()

        if auth_mode == AUTH_MODE_VERTEX_AI:
            provider = "Google Vertex AI"
            # The google-genai SDK's Vertex AI mode (`Client(vertexai=True)`)
            # resolves project/location from these exact env vars when no
            # explicit kwargs are passed — which is how every real call in
            # `decide()` constructs its client. This is deliberately NOT
            # `settings.google_cloud_region` (the general Cloud Run/GCP
            # deployment region, e.g. "us-central1") — that is a different
            # concept and reporting it here as the Gemini location is exactly
            # the defect this method fixes.
            configured_location = os.getenv("GOOGLE_CLOUD_LOCATION")
        elif auth_mode == AUTH_MODE_API_KEY:
            provider = "Google Gemini API"
            configured_location = None  # the Gemini Developer API has no location concept
        else:
            provider = "Not configured"
            configured_location = None

        return {
            "configured_model_id": self.config.gemini_model,
            "provider": provider,
            "framework": "Google GenAI SDK (google-genai structured output)",
            "auth_mode": auth_mode,
            "configured_location": configured_location,
            "agent_enabled": self.config.agent_enabled,
        }

    def decide(
        self,
        decision_type: str,
        response_schema: type[T],
        system_instruction: str,
        prompt: str,
    ) -> tuple[T | None, GeminiInvocationEvidence]:
        """Attempts one structured Gemini decision call.

        Returns (parsed_decision_or_None, evidence). `parsed_decision` is None
        whenever the call was skipped, failed, or returned a schema-invalid
        response — callers MUST use the deterministic fallback in that case.
        """
        invocation_id = f"GEM-{uuid.uuid4().hex[:10].upper()}"
        started_iso = datetime.now(UTC).isoformat()

        if not self.config.agent_enabled:
            return None, self._evidence(
                invocation_id, decision_type, started_iso, started_iso,
                success=False, failure_category=FAILURE_DISABLED,
            )

        if self._client_factory is not None:
            auth_mode, client_kwargs = AUTH_MODE_TEST_FAKE, {}
        else:
            auth_mode, client_kwargs = self._resolve_auth_mode()
            if auth_mode == AUTH_MODE_NONE:
                return None, self._evidence(
                    invocation_id, decision_type, started_iso, started_iso,
                    success=False, failure_category=FAILURE_NO_CREDENTIALS, auth_mode=auth_mode,
                )

        t0 = time.monotonic()
        try:
            from google.genai import types

            generation_config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.0,
                # No `tools=` are ever passed here — every decision point is a
                # single bounded structured-output call, never a Python
                # function the SDK could invoke on our behalf. Automatic
                # function calling is explicitly disabled rather than left at
                # the SDK's enabled-by-default setting, both to be honest
                # about what this client actually does and to avoid the
                # "Direct use of AFC in Models.generate_content is not
                # recommended" warning that default otherwise logs even with
                # zero registered tools.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                http_options=types.HttpOptions(
                    timeout=DEFAULT_TIMEOUT_MS,
                    # Bounds total latency and guarantees a quota error (429)
                    # surfaces immediately as a classified fallback instead of
                    # the SDK's default of up to 5 attempts with backoff.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )

            if self._client_factory is not None:
                client = self._client_factory()
                response = client.models.generate_content(
                    model=self.config.gemini_model, contents=prompt, config=generation_config,
                )
            else:
                from google import genai

                with genai.Client(**client_kwargs) as client:
                    response = client.models.generate_content(
                        model=self.config.gemini_model, contents=prompt, config=generation_config,
                    )
        except Exception as exc:  # noqa: BLE001 — classified below, never re-raised
            latency_ms = (time.monotonic() - t0) * 1000
            ended_iso = datetime.now(UTC).isoformat()
            category = self._classify_failure(exc)
            logger.warning(
                "GEMINI_CALL_FAILED decision_type=%s category=%s error=%s",
                decision_type, category, _scrub_secrets(str(exc)),
            )
            return None, self._evidence(
                invocation_id, decision_type, started_iso, ended_iso,
                success=False, failure_category=category, latency_ms=latency_ms, auth_mode=auth_mode,
            )

        latency_ms = (time.monotonic() - t0) * 1000
        ended_iso = datetime.now(UTC).isoformat()
        response_id = getattr(response, "response_id", None)
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None

        common_kwargs = {
            "latency_ms": latency_ms, "response_id": response_id,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "auth_mode": auth_mode,
        }

        try:
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = getattr(feedback, "block_reason", None) if feedback else None
            if block_reason is not None and "UNSPECIFIED" not in str(block_reason):
                logger.warning("GEMINI_RESPONSE_BLOCKED decision_type=%s reason=%s", decision_type, block_reason)
                return None, self._evidence(
                    invocation_id, decision_type, started_iso, ended_iso,
                    success=False, failure_category=FAILURE_BLOCKED_RESPONSE, **common_kwargs,
                )

            if not getattr(response, "candidates", None):
                return None, self._evidence(
                    invocation_id, decision_type, started_iso, ended_iso,
                    success=False, failure_category=FAILURE_EMPTY_RESPONSE, **common_kwargs,
                )

            parsed = getattr(response, "parsed", None)
            if parsed is None:
                raw_text = getattr(response, "text", None)
                if not raw_text:
                    return None, self._evidence(
                        invocation_id, decision_type, started_iso, ended_iso,
                        success=False, failure_category=FAILURE_MALFORMED_RESPONSE, **common_kwargs,
                    )
                try:
                    parsed = response_schema.model_validate_json(raw_text)
                except (ValidationError, ValueError) as exc:
                    logger.warning("GEMINI_SCHEMA_INVALID decision_type=%s error=%s", decision_type, exc)
                    return None, self._evidence(
                        invocation_id, decision_type, started_iso, ended_iso,
                        success=False, failure_category=FAILURE_SCHEMA_INVALID, **common_kwargs,
                    )
            elif not isinstance(parsed, response_schema):
                try:
                    parsed = response_schema.model_validate(parsed)
                except (ValidationError, ValueError) as exc:
                    logger.warning("GEMINI_SCHEMA_INVALID decision_type=%s error=%s", decision_type, exc)
                    return None, self._evidence(
                        invocation_id, decision_type, started_iso, ended_iso,
                        success=False, failure_category=FAILURE_SCHEMA_INVALID, **common_kwargs,
                    )
        except Exception as exc:  # noqa: BLE001 — final defensive net around response parsing
            logger.warning("GEMINI_RESPONSE_PARSE_FAILED decision_type=%s error=%s", decision_type, _scrub_secrets(str(exc)))
            return None, self._evidence(
                invocation_id, decision_type, started_iso, ended_iso,
                success=False, failure_category=FAILURE_MALFORMED_RESPONSE, **common_kwargs,
            )

        evidence = self._evidence(
            invocation_id, decision_type, started_iso, ended_iso,
            success=True, schema_valid=True, **common_kwargs,
            rationale=getattr(parsed, "rationale", None),
            confidence=getattr(parsed, "confidence", None),
            needs_human_review=bool(getattr(parsed, "needs_human_review", False)),
            requested_tool=getattr(parsed, "requested_tool", None),
        )
        return parsed, evidence

    def _classify_failure(self, exc: Exception) -> str:
        try:
            from google.genai import errors as genai_errors
        except Exception:  # pragma: no cover - genai always importable here
            genai_errors = None

        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            if code == 429:
                return FAILURE_QUOTA
            if code == 408:
                return FAILURE_TIMEOUT
            if code is not None and code >= 500:
                return FAILURE_UNAVAILABLE
            return FAILURE_UNAVAILABLE

        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "timeout" in name or "timeout" in message or "deadline" in message:
            return FAILURE_TIMEOUT
        if "quota" in message or "rate limit" in message or "429" in message:
            return FAILURE_QUOTA
        return FAILURE_UNAVAILABLE

    def _evidence(
        self,
        invocation_id: str,
        decision_type: str,
        started_iso: str,
        ended_iso: str,
        *,
        success: bool,
        failure_category: str | None = None,
        schema_valid: bool | None = None,
        latency_ms: float = 0.0,
        response_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rationale: str | None = None,
        confidence: float | None = None,
        needs_human_review: bool = False,
        requested_tool: str | None = None,
        auth_mode: str = AUTH_MODE_NONE,
    ) -> GeminiInvocationEvidence:
        return GeminiInvocationEvidence(
            invocation_id=invocation_id,
            model_id=self.config.gemini_model,
            auth_mode=auth_mode,
            response_id=response_id,
            decision_type=decision_type,
            requested_tool=requested_tool,
            started_at=started_iso,
            ended_at=ended_iso,
            latency_ms=round(latency_ms, 2),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            success=success,
            schema_valid=schema_valid,
            failure_category=failure_category,
            rationale=rationale,
            confidence=confidence,
            needs_human_review=needs_human_review,
        )
