import logging

from pydantic import ValidationError

from app.agents.supervisor import AssuranceSupervisor
from app.api.assurance import resolve_demo_package
from app.ipir.package import IPIRPackage
from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome, ProcessingResult, safe_error_text
from app.models.mission import AssuranceMission, MissionStatus, PricingSourceRef
from app.services.mission_transitions import apply_transition
from app.storage import AssuranceRunStatus, get_run_store
from app.storage.artifacts import get_artifact_store
from app.storage.interfaces import LeaseOutcome

logger = logging.getLogger(__name__)


def _resolve_source_package(source_ref: PricingSourceRef) -> IPIRPackage:
    """Resolves a mission source to its IPIR package.

    Real uploaded/compiled sources (source_type == "FILE") are read back from
    the artifact store using the compiled IPIR artifact id assigned at compile
    time (see PricingSourceIngestionService.compile_source). Built-in demo/
    sample sources fall back to the bundled-file resolver.
    """
    if source_ref.source_type == "FILE":
        content = get_artifact_store().get_artifact_content(f"IPIR-{source_ref.source_id}")
        if content:
            return IPIRPackage.model_validate_json(content)
    return resolve_demo_package(source_ref.source_id)


def _safe_log_event(store, run_id: str, stage: str, message: str, details: dict | None = None) -> None:
    """Best-effort event logging: a failure here must never mask or replace the
    real ProcessingOutcome already decided by the caller."""
    try:
        store.log_event(run_id=run_id, stage=stage, message=message, details=details or {})
    except Exception as exc:
        logger.error(
            "STORAGE_DEGRADED: failed to log event (stage=%s) for run '%s': %s",
            stage, run_id, safe_error_text(exc),
        )


class MissionExecutionService:
    """Authoritative execution service for Mission V2 10-stage assurance pipeline."""

    @staticmethod
    def execute_job(job: AssuranceJob) -> ProcessingResult:
        """Executes a Mission V2 assurance job using an atomic store-level lease so
        duplicate Pub/Sub delivery (or two workers racing on the same mission) can
        never both begin executing it concurrently.

        Uses the strict run store (`get_run_store(strict=True)`): a Firestore
        failure during lease acquisition or status persistence raises instead of
        being silently absorbed into an empty in-memory fallback and
        misclassified as "mission not found". That exact silent-fallback
        pattern was the root cause of missions staying stuck in QUEUED forever
        after a Firestore outage — see the deployment-parity diagnosis and
        `app.storage.get_run_store`.
        """
        store = get_run_store(strict=True)
        mission_id = job.run_id

        try:
            outcome, record = store.acquire_lease(mission_id, job.job_id)
        except Exception as exc:
            logger.error(
                "STORAGE_UNAVAILABLE: acquire_lease failed for mission '%s': %s",
                mission_id, safe_error_text(exc),
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.RETRYABLE_FAILURE,
                run_id=mission_id,
                job_id=job.job_id,
                detail="Storage layer unavailable during lease acquisition.",
                status_write_ok=False,
            )

        if outcome == LeaseOutcome.NOT_FOUND:
            logger.error("MISSION_LOADED_FAILED: Mission '%s' not found in store.", mission_id)
            return ProcessingResult(
                outcome=ProcessingOutcome.TERMINAL_INVALID_MESSAGE,
                run_id=mission_id,
                job_id=job.job_id,
                detail="Referenced mission_id does not exist in the store.",
            )

        if outcome == LeaseOutcome.ALREADY_TERMINAL:
            status_str = record.status.value if hasattr(record.status, "value") else str(record.status)
            logger.info(
                "EXECUTION_LEASE_SKIPPED: Mission '%s' is in terminal status '%s'. Skipping job '%s'.",
                mission_id, status_str, job.job_id,
            )
            _safe_log_event(
                store, mission_id, status_str,
                f"Idempotency check: Job '{job.job_id}' received for terminal mission status [{status_str}].",
                {"job_id": job.job_id},
            )
            if status_str == "CANCELLED":
                return ProcessingResult(
                    outcome=ProcessingOutcome.CANCELLED, run_id=mission_id, job_id=job.job_id,
                    detail="Mission already CANCELLED.",
                )
            return ProcessingResult(
                outcome=ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED, run_id=mission_id, job_id=job.job_id,
                detail=f"Mission already in terminal status '{status_str}'.",
            )

        if outcome == LeaseOutcome.ALREADY_LEASED:
            # Another in-flight delivery already holds the lease — this is a
            # duplicate delivery, not a failure. Acking it (without starting a
            # second execution) is exactly what "no duplicate execution under
            # redelivery" requires.
            logger.info(
                "EXECUTION_LEASE_LOCKED: Mission '%s' is actively leased by another worker delivery.",
                mission_id,
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED, run_id=mission_id, job_id=job.job_id,
                detail="Mission already leased by another in-flight delivery.",
            )

        if outcome == LeaseOutcome.INVALID_TRANSITION:
            logger.error(
                "EXECUTION_LEASE_INVALID_TRANSITION: Mission '%s' could not transition to RUNNING.",
                mission_id,
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.RETRYABLE_FAILURE, run_id=mission_id, job_id=job.job_id,
                detail="Lease state transition to RUNNING was rejected.",
            )

        assert outcome == LeaseOutcome.ACQUIRED
        assert record is not None

        meta = record.metadata if isinstance(record.metadata, dict) else {}
        meta["job_type"] = job.job_type
        record.metadata = meta
        try:
            store.update_run(record)
        except Exception as exc:
            # Non-fatal: the lease transition itself already committed inside
            # `acquire_lease`'s own transaction. This second write is metadata
            # bookkeeping only — log and continue rather than abandoning an
            # already-acquired lease.
            logger.error(
                "STORAGE_DEGRADED: failed to persist job_type metadata for mission '%s': %s",
                mission_id, safe_error_text(exc),
            )

        _safe_log_event(
            store, mission_id, "RUNNING",
            f"EXECUTION_LEASE_ACQUIRED: Worker acquired lease for job '{job.job_id}' (Correlation ID: {job.correlation_id}).",
            {"job_id": job.job_id, "correlation_id": job.correlation_id},
        )

        mission_dict = meta.get("mission_object")
        try:
            if mission_dict and isinstance(mission_dict, dict):
                mission = AssuranceMission.model_validate(mission_dict)
            else:
                # No stored full mission object to reconstruct from. We
                # deliberately do NOT fabricate a placeholder `objective`
                # (product/jurisdiction/effective_period_start) — inventing
                # mission intent data would be worse than rejecting the
                # message outright. This raises ValidationError below and is
                # correctly classified as a poison message.
                source_a_id = job.left_package_id or job.left_source_id or "AZ_HO3_2026_09"
                source_b_id = job.right_package_id or job.right_source_id
                mission = AssuranceMission(
                    mission_id=mission_id,
                    name=meta.get("name", "Assurance Mission"),
                    mode=meta.get("mode", "RELEASE_CONFORMANCE"),
                    status=MissionStatus.RUNNING,
                    objective=meta["objective"],
                    source_a={"source_id": source_a_id, "source_type": "SAMPLE_RELEASE", "name": "Source A"},
                    source_b=(
                        {"source_id": source_b_id, "source_type": "SAMPLE_RELEASE", "name": "Source B"}
                        if source_b_id
                        else None
                    ),
                )
        except (ValidationError, KeyError) as exc:
            logger.error(
                "POISON_MESSAGE: Mission '%s' envelope failed validation: %s",
                mission_id, safe_error_text(exc),
            )
            status_write_ok = _fail_mission(store, mission_id, f"Message envelope validation failed: {safe_error_text(exc)}")
            return ProcessingResult(
                outcome=ProcessingOutcome.TERMINAL_INVALID_MESSAGE,
                run_id=mission_id, job_id=job.job_id,
                detail="Mission envelope failed schema validation.",
                status_write_ok=status_write_ok,
            )

        logger.info("SUPERVISOR_STARTED: Running AssuranceSupervisor for mission '%s'", mission_id)

        def _cancellation_requested() -> bool:
            """Re-reads the mission record so cooperative cancellation requested via
            POST /missions/{id}/cancel while the supervisor is mid-flight is honored
            between stages. A storage failure here must not crash the mission —
            it just means this particular check can't confirm cancellation."""
            try:
                current = store.get_run(mission_id)
            except Exception as exc:
                logger.error(
                    "STORAGE_DEGRADED: cancellation check failed for mission '%s': %s",
                    mission_id, safe_error_text(exc),
                )
                return False
            if current is None:
                return False
            current_meta = current.metadata if isinstance(current.metadata, dict) else {}
            return bool(current.cancellation_requested or current_meta.get("cancellation_requested"))

        try:
            left_pkg = _resolve_source_package(mission.source_a)
            right_pkg = _resolve_source_package(mission.source_b) if mission.source_b else None

            supervisor = AssuranceSupervisor(store)
            result = supervisor.run_mission(mission, left_pkg, right_pkg, cancellation_check=_cancellation_requested)

            term_status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)
            logger.info("MISSION_COMPLETED: Mission '%s' finished with status '%s'", mission_id, term_status)

            dec_val = (
                result.release_decision.data.status
                if result.release_decision and result.release_decision.data
                else "UNKNOWN"
            )

            if term_status == "CANCELLED":
                return ProcessingResult(
                    outcome=ProcessingOutcome.CANCELLED, run_id=mission_id, job_id=job.job_id,
                    decision=dec_val, detail="Mission cancelled cooperatively during execution.",
                )

            return ProcessingResult(
                outcome=ProcessingOutcome.SUCCEEDED, run_id=mission_id, job_id=job.job_id, decision=dec_val,
            )
        except Exception as exc:
            logger.exception("SUPERVISOR_FAILED: Mission '%s' failed during execution: %s", mission_id, exc)
            status_write_ok = _fail_mission(store, mission_id, f"Supervisor Execution Failure: {safe_error_text(exc)}")
            return ProcessingResult(
                outcome=ProcessingOutcome.RETRYABLE_FAILURE,
                run_id=mission_id, job_id=job.job_id,
                detail="Supervisor execution raised an unexpected exception.",
                status_write_ok=status_write_ok,
            )


def _fail_mission(store, mission_id: str, reason: str) -> bool:
    """Best-effort transition to FAILED, routed through `apply_transition` so a
    mission already CANCELLED (e.g. a cancel request that raced in during
    execution) is never overwritten. Returns whether the status write itself
    succeeded — a failure here must be logged and must NOT be presented as a
    successful status update, but must also never crash the caller (which
    still needs to return a non-2xx ProcessingResult either way)."""
    try:
        transition = apply_transition(
            store, mission_id, AssuranceRunStatus.FAILED,
            status_reason=reason, workflow_stage="FAILED",
            extra_metadata={"last_error": reason},
        )
        _safe_log_event(
            store, mission_id, "FAILED", f"Mission execution failed: {reason}",
            {"status_write_applied": transition.ok},
        )
        return transition.ok
    except Exception as exc:
        logger.error(
            "STORAGE_DEGRADED: best-effort FAILED status write for mission '%s' also failed: %s",
            mission_id, safe_error_text(exc),
        )
        return False
