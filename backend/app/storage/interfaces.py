from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.storage.models import AssuranceRunRecord, AssuranceRunStatus, EvidenceRecord, RunEvent

LEASE_TTL_SECONDS = 120


class LeaseOutcome(StrEnum):
    """Result of attempting to acquire an execution lease for a run."""

    ACQUIRED = "ACQUIRED"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    ALREADY_LEASED = "ALREADY_LEASED"
    INVALID_TRANSITION = "INVALID_TRANSITION"


class BaseRunStore(ABC):
    """Abstract interface for assurance run storage adapters."""

    @abstractmethod
    def create_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        """Creates a new assurance run record."""
        pass

    @abstractmethod
    def delete_run(self, run_id: str) -> bool:
        """Permanently deletes a run record and returns True only if a document
        was actually confirmed deleted from the backing store. Callers MUST
        enforce deletion eligibility (see mission_transitions.is_deletable)
        BEFORE calling this — this method performs the deletion, not the policy
        check, and never hard-deletes historical RUN-* records implicitly."""
        pass

    def save_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        """Alias/convenience for create_run/update_run."""
        if self.get_run(record.run_id):
            return self.update_run(record)
        return self.create_run(record)

    @abstractmethod
    def get_run(self, run_id: str) -> AssuranceRunRecord | None:
        """Fetches an assurance run record by ID."""
        pass

    @abstractmethod
    def update_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        """Updates an existing assurance run record."""
        pass

    @abstractmethod
    def list_runs(self, limit: int = 50) -> list[AssuranceRunRecord]:
        """Lists assurance runs sorted newest first (created_at descending)."""
        pass

    def update_run_status(
        self,
        run_id: str,
        status: AssuranceRunStatus | str,
        workflow_stage: str | None = None,
        decision: str | None = None,
        summary: str | None = None,
        report: Any = None,
    ) -> AssuranceRunRecord:
        """Updates status and metadata for a run."""
        record = self.get_run(run_id)
        if not record:
            record = AssuranceRunRecord(run_id=run_id)

        if isinstance(status, str):
            status = AssuranceRunStatus(status)
        record.status = status

        if workflow_stage is not None:
            record.workflow_stage = workflow_stage
        if decision is not None:
            record.decision = decision
        if summary is not None:
            record.summary = summary
        if report is not None:
            record.report = report

        return self.update_run(record)

    @abstractmethod
    def add_event(self, run_id: str, event: RunEvent) -> RunEvent:
        """Appends an audit log event to a run."""
        pass

    def log_event(
        self,
        run_id: str,
        stage: str,
        message: str,
        agent_name: str = "AssuranceWorker",
        details: dict[str, Any] | None = None,
    ) -> RunEvent:
        """Logs a structured workflow milestone event."""
        import uuid

        event = RunEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            run_id=run_id,
            stage=stage,
            agent_name=agent_name,
            action=message,
            details=details or {},
        )
        return self.add_event(run_id, event)

    @abstractmethod
    def get_events(self, run_id: str) -> list[RunEvent]:
        """Retrieves all audit events for a run."""
        pass

    @abstractmethod
    def add_evidence(self, run_id: str, evidence: EvidenceRecord) -> EvidenceRecord:
        """Persists a typed evidence lineage record."""
        pass

    @abstractmethod
    def get_evidence(self, run_id: str) -> list[EvidenceRecord]:
        """Retrieves all evidence lineage records for a run."""
        pass

    def acquire_lease(
        self, run_id: str, job_id: str, lease_seconds: int = LEASE_TTL_SECONDS
    ) -> tuple[LeaseOutcome, AssuranceRunRecord | None]:
        """Attempts to acquire an execution lease for `run_id`, transitioning it to
        RUNNING and stamping lease ownership/heartbeat metadata.

        Base implementation is a best-effort (non-atomic) read-modify-write and is
        only safe against duplicate delivery on a single-process store guarded
        elsewhere by a lock. Store adapters backed by a real database MUST override
        this with a transactionally atomic implementation.
        """
        from app.services.mission_transitions import TERMINAL_STATUSES, apply_transition

        record = self.get_run(run_id)
        if record is None:
            return LeaseOutcome.NOT_FOUND, None

        status_str = record.status.value if hasattr(record.status, "value") else str(record.status)
        if status_str in TERMINAL_STATUSES:
            return LeaseOutcome.ALREADY_TERMINAL, record

        meta = record.metadata if isinstance(record.metadata, dict) else {}
        last_hb = meta.get("last_heartbeat_at")
        if status_str == "RUNNING" and last_hb:
            try:
                hb_dt = datetime.fromisoformat(last_hb) if isinstance(last_hb, str) else last_hb
                if (datetime.now(UTC) - hb_dt).total_seconds() < lease_seconds:
                    return LeaseOutcome.ALREADY_LEASED, record
            except Exception:
                pass

        now_iso = datetime.now(UTC).isoformat()
        result = apply_transition(
            self,
            run_id,
            AssuranceRunStatus.RUNNING,
            workflow_stage="RUNNING",
            extra_metadata={"last_heartbeat_at": now_iso, "lease_owner": job_id},
        )
        if not result.ok:
            return LeaseOutcome.INVALID_TRANSITION, result.record
        return LeaseOutcome.ACQUIRED, result.record
