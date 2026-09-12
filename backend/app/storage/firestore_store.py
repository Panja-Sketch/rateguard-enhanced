import logging
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.storage.interfaces import LEASE_TTL_SECONDS, BaseRunStore, LeaseOutcome
from app.storage.memory_store import InMemoryRunStore
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus, EvidenceRecord, RunEvent

logger = logging.getLogger(__name__)


def sanitize_for_firestore(val: Any) -> Any:
    """Recursively normalizes input objects into Firestore-supported JSON-safe native types."""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, BaseModel):
        return sanitize_for_firestore(val.model_dump(mode="json"))
    if isinstance(val, dict):
        return {str(k): sanitize_for_firestore(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [sanitize_for_firestore(v) for v in val]
    if hasattr(val, "model_dump"):
        return sanitize_for_firestore(val.model_dump(mode="json"))
    if hasattr(val, "__dict__"):
        return sanitize_for_firestore(val.__dict__)
    return str(val)


DEFAULT_COLLECTION_NAME = "assurance_runs"


class FirestoreRunStore(BaseRunStore):
    """Google Cloud Firestore adapter for persistent run state and evidence lineage."""

    def __init__(
        self,
        project_id: str = "rateguard-ai",
        database_id: str | None = None,
        fallback_on_error: bool = True,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        """`collection_name` namespaces every run/event/evidence document under a
        single top-level Firestore collection (events/evidence are always
        subcollections of that same collection's run documents — see the
        class docstring on each method touching them). This is what lets a
        candidate/staging deployment (`RATEGUARD_FIRESTORE_COLLECTION=
        assurance_runs_staging`) read and write completely isolated documents
        from production (`assurance_runs`) while sharing the same Firestore
        database — never the same documents, never cross-contamination.
        """
        self.project_id = project_id
        self.database_id = database_id
        self.fallback_on_error = fallback_on_error
        self.collection_name = collection_name or DEFAULT_COLLECTION_NAME
        self._fallback_store = InMemoryRunStore()
        self._db: Any = None

        try:
            from google.cloud import firestore

            kwargs: dict[str, Any] = {"project": project_id}
            # Defensive normalization only — NOT the fix for the historical "400
            # Invalid database id %28default%29" incident (that was a
            # google-api-core==2.35.0 percent-encoding regression, fixed by the
            # google-api-core==2.34.0 pin in pyproject.toml). "(default)" is a
            # valid Firestore database id and passing it explicitly is normally
            # fine; we simply avoid passing an explicit `database=` kwarg for
            # the common "unset or default" case so the client library's own
            # default-database resolution path is used instead of ours.
            if database_id and database_id != "(default)":
                kwargs["database"] = database_id
            self._db = firestore.Client(**kwargs)
            logger.info("Successfully initialized Firestore client for project '%s'", project_id)
        except Exception as e:
            if fallback_on_error:
                logger.warning(
                    "Failed to initialize Firestore client (%s). Falling back to InMemoryRunStore.",
                    e,
                )
                self._db = None
            else:
                raise

    def create_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        self._fallback_store.create_run(record)
        if self._db is not None:
            try:
                doc_ref = self._db.collection(self.collection_name).document(record.run_id)
                clean_payload = sanitize_for_firestore(record.model_dump(mode="json"))
                doc_ref.set(clean_payload)
            except Exception as e:
                logger.error("Firestore error in create_run: %s", e)
                if not self.fallback_on_error:
                    raise
        return record

    def get_run(self, run_id: str) -> AssuranceRunRecord | None:
        if self._db is not None:
            try:
                doc_ref = self._db.collection(self.collection_name).document(run_id)
                doc = doc_ref.get()
                if doc.exists:
                    data = doc.to_dict()
                    return AssuranceRunRecord.model_validate(data)
            except Exception as e:
                logger.error("Firestore get_run error for '%s': %s", run_id, e)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.get_run(run_id)

    def update_run(self, record: AssuranceRunRecord) -> AssuranceRunRecord:
        self._fallback_store.update_run(record)
        if self._db is not None:
            try:
                doc_ref = self._db.collection(self.collection_name).document(record.run_id)
                clean_payload = sanitize_for_firestore(record.model_dump(mode="json"))
                doc_ref.set(clean_payload, merge=True)
            except Exception as e:
                logger.error("Firestore error in update_run: %s", e)
                if not self.fallback_on_error:
                    raise
        return record

    def list_runs(self, limit: int = 50) -> list[AssuranceRunRecord]:
        if self._db is not None:
            try:
                from google.cloud import firestore

                col_ref = (
                    self._db.collection(self.collection_name)
                    .order_by("created_at", direction=firestore.Query.DESCENDING)
                    .limit(limit)
                )
                docs = col_ref.stream()
                runs: list[AssuranceRunRecord] = []
                for doc in docs:
                    runs.append(AssuranceRunRecord.model_validate(doc.to_dict()))
                if runs:
                    return runs
            except Exception as e:
                logger.error("Firestore error in list_runs: %s", e)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.list_runs(limit)

    def add_event(self, run_id: str, event: RunEvent) -> RunEvent:
        self._fallback_store.add_event(run_id, event)
        if self._db is not None:
            try:
                doc_ref = (
                    self._db.collection(self.collection_name)
                    .document(run_id)
                    .collection("events")
                    .document(event.event_id)
                )
                clean_payload = sanitize_for_firestore(event.model_dump(mode="json"))
                doc_ref.set(clean_payload)
            except Exception as e:
                logger.error("Firestore error in add_event: %s", e)
                if not self.fallback_on_error:
                    raise
        return event

    def get_events(self, run_id: str) -> list[RunEvent]:
        if self._db is not None:
            try:
                col_ref = (
                    self._db.collection(self.collection_name).document(run_id).collection("events")
                )
                docs = col_ref.stream()
                events = [RunEvent.model_validate(doc.to_dict()) for doc in docs]
                if events:
                    return events
            except Exception as e:
                logger.error("Firestore error in get_events: %s", e)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.get_events(run_id)

    def add_evidence(self, run_id: str, evidence: EvidenceRecord) -> EvidenceRecord:
        self._fallback_store.add_evidence(run_id, evidence)
        if self._db is not None:
            try:
                doc_ref = (
                    self._db.collection(self.collection_name)
                    .document(run_id)
                    .collection("evidence")
                    .document(evidence.evidence_id)
                )
                clean_payload = sanitize_for_firestore(evidence.model_dump(mode="json"))
                doc_ref.set(clean_payload)
            except Exception as e:
                logger.error("Firestore error in add_evidence: %s", e)
                if not self.fallback_on_error:
                    raise
        return evidence

    def get_evidence(self, run_id: str) -> list[EvidenceRecord]:
        if self._db is not None:
            try:
                col_ref = (
                    self._db.collection(self.collection_name).document(run_id).collection("evidence")
                )
                docs = col_ref.stream()
                evidence_list = [EvidenceRecord.model_validate(doc.to_dict()) for doc in docs]
                if evidence_list:
                    return evidence_list
            except Exception as e:
                logger.error("Firestore error in get_evidence: %s", e)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.get_evidence(run_id)

    def delete_run(self, run_id: str) -> bool:
        """Permanently deletes the Firestore document (and its events/evidence
        subcollections, which Firestore does not cascade-delete automatically)
        for a run, returning True only after Firestore confirms the document no
        longer exists. Never reports success without that confirmation."""
        if self._db is None:
            return self._fallback_store.delete_run(run_id)
        try:
            doc_ref = self._db.collection(self.collection_name).document(run_id)
            snapshot = doc_ref.get()
            if not snapshot.exists:
                return False
            # Firestore does not cascade-delete subcollections; clean them up
            # explicitly since these records are only ever disposable/eligible-demo.
            for sub_name in ("events", "evidence"):
                for sub_doc in doc_ref.collection(sub_name).stream():
                    sub_doc.reference.delete()
            doc_ref.delete()
            confirm = doc_ref.get()
            deleted = not confirm.exists
            if deleted:
                self._fallback_store.delete_run(run_id)
            return deleted
        except Exception as e:
            logger.error("Firestore error in delete_run for '%s': %s", run_id, e)
            if not self.fallback_on_error:
                raise
            return False

    def acquire_lease(
        self, run_id: str, job_id: str, lease_seconds: int = LEASE_TTL_SECONDS
    ) -> tuple[LeaseOutcome, AssuranceRunRecord | None]:
        """Transactionally atomic lease acquisition: uses a Firestore transaction
        so two concurrent Pub/Sub deliveries for the same mission can never both
        observe the record as unleased and both begin executing it."""
        if self._db is None:
            return self._fallback_store.acquire_lease(run_id, job_id, lease_seconds)

        from google.cloud import firestore

        from app.services.mission_transitions import TERMINAL_STATUSES

        doc_ref = self._db.collection(self.collection_name).document(run_id)

        @firestore.transactional
        def _txn(transaction: Any) -> tuple[LeaseOutcome, AssuranceRunRecord | None]:
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return LeaseOutcome.NOT_FOUND, None

            record = AssuranceRunRecord.model_validate(snapshot.to_dict())
            status_str = record.status.value if hasattr(record.status, "value") else str(record.status)
            if status_str in TERMINAL_STATUSES:
                return LeaseOutcome.ALREADY_TERMINAL, record

            meta = record.metadata if isinstance(record.metadata, dict) else {}
            last_hb = meta.get("last_heartbeat_at")
            if status_str == "RUNNING" and last_hb:
                try:
                    hb_dt = datetime.fromisoformat(last_hb) if isinstance(last_hb, str) else last_hb
                    if hb_dt.tzinfo is None:
                        hb_dt = hb_dt.replace(tzinfo=UTC)
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

            clean_payload = sanitize_for_firestore(record.model_dump(mode="json"))
            transaction.set(doc_ref, clean_payload, merge=True)
            return LeaseOutcome.ACQUIRED, record

        try:
            transaction = self._db.transaction()
            outcome, record = _txn(transaction)
            if record is not None:
                self._fallback_store.update_run(record)
            return outcome, record
        except Exception as e:
            logger.error("Firestore error in acquire_lease for '%s': %s", run_id, e)
            if not self.fallback_on_error:
                raise
            return self._fallback_store.acquire_lease(run_id, job_id, lease_seconds)
