"""Typed processing-outcome contract for the asynchronous assurance worker.

`worker_endpoint.py` maps `ProcessingOutcome` to an HTTP status Cloud Pub/Sub
push delivery understands: SUCCEEDED / CANCELLED / DUPLICATE_ALREADY_PROCESSED
are acknowledged (2xx) so Pub/Sub never redelivers the message.
RETRYABLE_FAILURE / TERMINAL_INVALID_MESSAGE are NOT acknowledged (non-2xx) so
Pub/Sub retries delivery and, once the subscription's configured maximum
delivery attempts is exceeded, routes the message to the dead-letter topic
instead of it being silently discarded.

Before this contract existed, every code path in the worker eventually
returned a plain dict and `worker_endpoint.py` always answered Pub/Sub with
HTTP 200 regardless of what actually happened inside — including a Firestore
outage. That masked storage failures as successfully-processed deliveries:
Pub/Sub acked the message (so it was never retried) while the mission's
status was never durably persisted, leaving it stuck in its prior state
forever. See the deployment-parity diagnosis this hardening pass addresses.
"""

from enum import StrEnum

from pydantic import BaseModel


class ProcessingOutcome(StrEnum):
    """Exhaustive set of terminal outcomes for one worker processing attempt."""

    SUCCEEDED = "SUCCEEDED"
    DUPLICATE_ALREADY_PROCESSED = "DUPLICATE_ALREADY_PROCESSED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_INVALID_MESSAGE = "TERMINAL_INVALID_MESSAGE"
    CANCELLED = "CANCELLED"


# The single source of truth for outcome -> HTTP status. Deliberately an
# explicit, exhaustive mapping keyed by enum VALUE — never derived from
# declaration order or a "first N outcomes" slice, which is exactly the kind
# of implicit logic that let a prior status report misdescribe this contract
# (RETRYABLE_FAILURE sits third in declaration order but must NOT ack).
#
#   SUCCEEDED                   -> 200 (ack; Pub/Sub must not redeliver)
#   DUPLICATE_ALREADY_PROCESSED -> 200 (ack; a duplicate delivery of an
#                                  already-completed/already-leased mission
#                                  must never trigger a second execution)
#   CANCELLED                   -> 200 (ack; a safely cancelled mission must
#                                  never be retried)
#   RETRYABLE_FAILURE           -> 503 (do NOT ack; Firestore/Vertex
#                                  transient failures, internal exceptions,
#                                  or a failed best-effort status write must
#                                  cause Pub/Sub to retry, never be silently
#                                  swallowed into a false success)
#   TERMINAL_INVALID_MESSAGE    -> 400 (do NOT ack; a malformed/poison
#                                  envelope is retried by Pub/Sub up to the
#                                  subscription's configured maximum delivery
#                                  attempts, after which it becomes eligible
#                                  for dead-letter forwarding — it must never
#                                  be acknowledged as if it succeeded, nor
#                                  treated as un-retryable at the HTTP layer,
#                                  since dead-lettering is itself driven by
#                                  repeated non-2xx delivery attempts)
OUTCOME_HTTP_STATUS: dict[ProcessingOutcome, int] = {
    ProcessingOutcome.SUCCEEDED: 200,
    ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED: 200,
    ProcessingOutcome.CANCELLED: 200,
    ProcessingOutcome.RETRYABLE_FAILURE: 503,
    ProcessingOutcome.TERMINAL_INVALID_MESSAGE: 400,
}

# Outcomes that must be acknowledged (HTTP 2xx) so Pub/Sub does not redeliver.
# Derived from OUTCOME_HTTP_STATUS (not maintained as a second, independent
# set) so the two can never drift out of sync with each other.
ACK_OUTCOMES = frozenset(
    outcome for outcome, http_status in OUTCOME_HTTP_STATUS.items() if 200 <= http_status < 300
)


class ProcessingResult(BaseModel):
    """Result of one worker processing attempt.

    `detail` is a short, static, human-readable reason only — never a raw
    exception message, stack trace, or payload. Callers that need to log the
    underlying exception for debugging MUST do so server-side via
    `safe_error_text`, not through this model (which may be echoed back in
    the internal endpoint's JSON response body).
    """

    outcome: ProcessingOutcome
    run_id: str | None = None
    job_id: str | None = None
    detail: str | None = None
    decision: str | None = None
    # Whether the best-effort FAILED/WAITING_RETRY/terminal status write to the
    # store succeeded. False means the caller should treat the underlying
    # mission/run record as possibly stale in addition to the processing
    # failure itself — surfaced for observability, not for ack/nack decisions.
    status_write_ok: bool = True

    @property
    def should_ack(self) -> bool:
        return self.outcome in ACK_OUTCOMES


def safe_error_text(exc: BaseException, max_len: int = 300) -> str:
    """Bounded, log-safe stringification of an exception.

    Truncates aggressively so an exception that happens to embed a large
    payload (e.g. a Pydantic ValidationError's `input_value`) cannot dump a
    full policy/pricing payload into logs. This is defense-in-depth, not a
    guarantee that no fragment of input data ever appears in a log line —
    callers handling genuinely sensitive payloads must still avoid logging
    them directly.
    """
    text = str(exc)
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text
