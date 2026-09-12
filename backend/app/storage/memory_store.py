import threading
from datetime import UTC, datetime

from app.storage.interfaces import LEASE_TTL_SECONDS, BaseRunStore, LeaseOutcome
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus, EvidenceRecord, RunEvent


class InMemoryRunStore(BaseRunStore):
    """Thread-safe in-memory store adapter for development and unit testing."""

    def __init__(self) -> None:
        self._runs: dict[str, AssuranceRunRecord] = {}
        self._events: dict[str, list[RunEvent]] = {}
        self._evidence: dict[str, list[EvidenceRecord]] = {}
        self._lock = threading.Lock()

    def create_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        with self._lock:
            self._runs[record.run_id] = record
            if record.run_id not in self._events:
                self._events[record.run_id] = []
            if record.run_id not in self._evidence:
                self._evidence[record.run_id] = []
            return record

    def get_run(self, run_id: str) -> AssuranceRunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def update_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        with self._lock:
            record.updated_at = datetime.now(UTC)
            self._runs[record.run_id] = record
            return record

    def list_runs(self, limit: int = 50) -> list[AssuranceRunRecord]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
            return runs[:limit]

    def add_event(self, run_id: str, event: RunEvent) -> RunEvent:
        with self._lock:
            if run_id not in self._events:
                self._events[run_id] = []
            self._events[run_id].append(event)
            return event

    def get_events(self, run_id: str) -> list[RunEvent]:
        with self._lock:
            return list(self._events.get(run_id, []))

    def add_evidence(self, run_id: str, evidence: EvidenceRecord) -> EvidenceRecord:
        with self._lock:
            if run_id not in self._evidence:
                self._evidence[run_id] = []
            self._evidence[run_id].append(evidence)
            if run_id in self._runs:
                if evidence.evidence_id not in self._runs[run_id].evidence_refs:
                    self._runs[run_id].evidence_refs.append(evidence.evidence_id)
            return evidence

    def get_evidence(self, run_id: str) -> list[EvidenceRecord]:
        with self._lock:
            return list(self._evidence.get(run_id, []))

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            existed = run_id in self._runs
            self._runs.pop(run_id, None)
            self._events.pop(run_id, None)
            self._evidence.pop(run_id, None)
            return existed

    def acquire_lease(
        self, run_id: str, job_id: str, lease_seconds: int = LEASE_TTL_SECONDS
    ) -> tuple[LeaseOutcome, AssuranceRunRecord | None]:
        """Lock-protected atomic check-and-set: safe against duplicate Pub/Sub
        delivery within a single process, since two callers can never both observe
        an unleased record between the read and the write."""
        from app.services.mission_transitions import TERMINAL_STATUSES

        with self._lock:
            record = self._runs.get(run_id)
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

            record.status = AssuranceRunStatus.RUNNING
            record.workflow_stage = "RUNNING"
            if record.started_at is None:
                record.started_at = datetime.now(UTC)
            if not isinstance(record.metadata, dict):
                record.metadata = {}
            record.metadata["last_heartbeat_at"] = datetime.now(UTC).isoformat()
            record.metadata["lease_owner"] = job_id
            record.updated_at = datetime.now(UTC)
            self._runs[run_id] = record
            return LeaseOutcome.ACQUIRED, record
