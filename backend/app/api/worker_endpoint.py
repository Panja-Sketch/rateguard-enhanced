import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.messaging import AssuranceJob, AssuranceWorker
from app.messaging.outcomes import OUTCOME_HTTP_STATUS, ProcessingOutcome, safe_error_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/pubsub", tags=["internal-worker"])


class PubSubMessage(BaseModel):
    """Pub/Sub push message body format."""

    data: str = Field(description="Base64-encoded AssuranceJob JSON payload")
    message_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PubSubPushEnvelope(BaseModel):
    """Envelope wrapper for Pub/Sub push subscription HTTP requests."""

    message: PubSubMessage
    subscription: str | None = None


@router.post("/assurance")
def process_pubsub_assurance_job(envelope: PubSubPushEnvelope, response: Response) -> dict[str, Any]:
    """Internal endpoint for processing Cloud Pub/Sub push jobs via AssuranceWorker.

    Designed for authenticated invocation from Google Cloud Pub/Sub push subscriptions
    or Cloud Run service-to-service IAM invocation.

    HTTP status is looked up explicitly per outcome via
    `app.messaging.outcomes.OUTCOME_HTTP_STATUS` — NOT an unconditional 200,
    and not inferred from enum declaration order. Pub/Sub treats any non-2xx
    response as "redeliver this message", and — once the subscription's
    configured maximum delivery attempts is exceeded — routes it to the
    dead-letter topic instead of silently discarding it. A prior version of
    this endpoint always returned 200 regardless of what
    `AssuranceWorker.process_job` actually did, which acknowledged (and
    therefore permanently and silently discarded) messages that failed for
    retryable reasons — a Firestore outage chief among them. See the
    deployment-parity diagnosis this endpoint's behavior fixes.
    """
    try:
        raw_bytes = base64.b64decode(envelope.message.data)
        job_dict = json.loads(raw_bytes.decode("utf-8"))
        job = AssuranceJob.model_validate(job_dict)
    except Exception as e:
        # A malformed envelope can never succeed by retrying it unchanged — it
        # is a poison message. Logged with a bounded, safe reason (never the
        # raw payload) and NOT acknowledged, so it becomes eligible for
        # dead-letter handling after the subscription's configured maximum
        # delivery attempts, instead of being silently discarded as if it had
        # succeeded.
        logger.error("POISON_MESSAGE: Failed to decode Pub/Sub envelope data: %s", safe_error_text(e))
        response.status_code = OUTCOME_HTTP_STATUS[ProcessingOutcome.TERMINAL_INVALID_MESSAGE]
        return {
            "status": ProcessingOutcome.TERMINAL_INVALID_MESSAGE.value,
            "error": "Invalid Pub/Sub job message payload.",
        }

    try:
        worker = AssuranceWorker()
        result = worker.process_job(job)
    except Exception as e:
        # AssuranceWorker.process_job (and MissionExecutionService.execute_job)
        # are expected to catch their own internal exceptions and return a
        # typed ProcessingResult. This is a final defensive net for anything
        # that still escapes — e.g. a bug in the dispatcher itself, or the
        # AssuranceWorker() constructor failing outright. It must NEVER be
        # converted into a 200, which would silently acknowledge a failed
        # delivery.
        logger.exception(
            "UNEXPECTED_WORKER_EXCEPTION: job_id='%s' run_id='%s': %s",
            job.job_id, job.run_id, safe_error_text(e),
        )
        response.status_code = OUTCOME_HTTP_STATUS[ProcessingOutcome.RETRYABLE_FAILURE]
        return {
            "status": ProcessingOutcome.RETRYABLE_FAILURE.value,
            "job_id": job.job_id,
            "run_id": job.run_id,
            "error": "Unexpected worker exception.",
        }

    # Explicit, exhaustive lookup — never derived from should_ack + an
    # else-branch guess, so every ProcessingOutcome's HTTP status is a single
    # unambiguous fact in one place (see OUTCOME_HTTP_STATUS).
    response.status_code = OUTCOME_HTTP_STATUS[result.outcome]

    return {
        "status": result.outcome.value,
        "job_id": result.job_id or job.job_id,
        "run_id": result.run_id or job.run_id,
        "detail": result.detail,
        "decision": result.decision,
        "status_write_ok": result.status_write_ok,
    }
