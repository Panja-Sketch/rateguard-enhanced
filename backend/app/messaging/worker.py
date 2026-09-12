import logging

from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome, ProcessingResult
from app.storage import AssuranceRunStatus, BaseRunStore, get_run_store

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = (
    AssuranceRunStatus.COMPLETED,
    AssuranceRunStatus.FAILED,
    AssuranceRunStatus.NEEDS_REVIEW,
    AssuranceRunStatus.CANCELLED,
    AssuranceRunStatus.ARCHIVED,
)


class AssuranceWorker:
    """Idempotent background worker dispatcher processing assurance jobs."""

    def __init__(self, run_store: BaseRunStore | None = None) -> None:
        # `strict=True`: a Firestore failure must raise here, not silently fall
        # back to an empty in-memory store and be misread as "run not found" —
        # see `app.messaging.outcomes` / `app.storage.get_run_store` for the
        # full rationale (this exact silent-fallback pattern caused missions
        # to be stuck in QUEUED forever after a transient Firestore outage).
        self.run_store = run_store or get_run_store(strict=True)

    def process_job(self, job: AssuranceJob) -> ProcessingResult:
        """Processes an assurance job with strict idempotency and event timeline tracking.

        Every run_id this worker ever dispatches uses the Mission V2 'MIS-*'
        ID scheme -- that prefix, not `job_type`, is the authoritative
        dispatch key. `job_type` is independently validated as a consistency
        check: any 'MIS-*' job whose job_type isn't 'ASSURANCE_MISSION_V2' is
        a genuine bug and rejected loudly rather than guessed at. A
        non-'MIS-*' run_id is a poison message -- the pre-V2 'RUN-*' agentic
        pipeline this worker used to also dispatch to has been retired (no
        code path can create a new 'RUN-*' job any more) -- UNLESS it is a
        stale redelivery of an already-terminal historical 'RUN-*' record,
        which is acked as a harmless duplicate rather than retried forever.
        Historical 'RUN-*' records remain readable via
        GET /api/v1/assurance/runs/{run_id}/events and .../evidence, which
        are generic over the run store and unrelated to job dispatch.

        Always returns a `ProcessingResult` — never raises for an expected
        failure class. `worker_endpoint.py` maps the outcome to an HTTP status;
        it must never be converted into an unconditional 200.
        """
        logger.info(
            "PUBSUB_RECEIVED: Worker received job '%s' (Run ID: %s, Job Type: %s)",
            job.job_id,
            job.run_id,
            job.job_type,
        )

        if not job.run_id.startswith("MIS-"):
            # No code path can create a NEW non-'MIS-' job any more (the
            # legacy pipeline is retired), but a stale Pub/Sub redelivery of
            # an already-completed historical 'RUN-*' job is still a
            # harmless duplicate, not poison -- it must be acked, not left
            # to retry forever against a pipeline that no longer exists.
            try:
                existing = self.run_store.get_run(job.run_id)
            except Exception as exc:
                logger.error(
                    "STORAGE_UNAVAILABLE: legacy-id lookup failed for run '%s': %s",
                    job.run_id, exc,
                )
                return ProcessingResult(
                    outcome=ProcessingOutcome.RETRYABLE_FAILURE,
                    run_id=job.run_id, job_id=job.job_id,
                    detail="Storage layer unavailable while checking a non-Mission-V2 run_id.",
                    status_write_ok=False,
                )
            if existing is not None and existing.status in _TERMINAL_STATUSES:
                logger.info(
                    "LEGACY_DUPLICATE_ACKED: run_id='%s' already in terminal status '%s'; "
                    "acking stale redelivery.",
                    job.run_id, existing.status,
                )
                return ProcessingResult(
                    outcome=ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED,
                    run_id=job.run_id, job_id=job.job_id,
                    detail=f"Legacy run already in terminal status '{existing.status}'.",
                )

            logger.error(
                "POISON_MESSAGE: job_id='%s' run_id='%s' does not use the Mission V2 'MIS-' "
                "ID scheme; the legacy pipeline that used to accept this has been retired.",
                job.job_id, job.run_id,
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.TERMINAL_INVALID_MESSAGE,
                run_id=job.run_id,
                job_id=job.job_id,
                detail="run_id does not use the Mission V2 'MIS-' ID scheme; the legacy pipeline is retired.",
            )

        if job.job_type != "ASSURANCE_MISSION_V2":
            logger.error(
                "JOB_ROUTING_MISMATCH: job_id='%s' run_id='%s' uses the Mission V2 ID "
                "scheme but job_type='%s' (expected 'ASSURANCE_MISSION_V2'). Refusing "
                "to guess; this job is not dispatched.",
                job.job_id,
                job.run_id,
                job.job_type,
            )
            # A structurally wrong job_type will never become correct by
            # retrying the identical message — this is a poison envelope.
            return ProcessingResult(
                outcome=ProcessingOutcome.TERMINAL_INVALID_MESSAGE,
                run_id=job.run_id,
                job_id=job.job_id,
                detail="JOB_ROUTING_MISMATCH: MIS- run_id with unexpected job_type.",
            )

        from app.services.mission_execution_service import MissionExecutionService

        return MissionExecutionService.execute_job(job)
