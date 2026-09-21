"""Durable checkpoint storage for impact jobs and batches.

Two implementations share one contract: `InMemoryImpactStore` (tests, local
mode) and `FirestoreImpactStore` (production). The contract that matters:

- documents are keyed by a tenant-prefixed id and carry `tenant_id`; a job of
  another tenant is indistinguishable from a missing one;
- batch leasing is an atomic compare-and-set with a lease owner acting as a
  fencing token: only the current owner may write a batch result, so a stale
  or redelivered handler can never overwrite or double-count a result;
- a DONE batch is immutable;
- `finalize_job` is a transactional first-writer-wins operation.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.impact.models import (
    TERMINAL_JOB_STATUSES,
    BatchRecord,
    BatchState,
    ImpactJob,
    ImpactStatus,
    LeaseResult,
)

logger = logging.getLogger(__name__)

BATCH_RETENTION_DAYS = 90
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _lease_decision(
    batch: BatchRecord, owner: str, lease_seconds: int, max_attempts: int, now: datetime
) -> tuple[LeaseResult, BatchRecord | None]:
    """Pure lease state machine shared by both stores."""
    if batch.state == BatchState.DONE:
        return LeaseResult.ALREADY_DONE, batch
    expires = _parse(batch.lease_expires_at)
    if batch.state == BatchState.LEASED and expires is not None and expires > now:
        return LeaseResult.LEASED_ELSEWHERE, batch
    if batch.attempts >= max_attempts:
        return LeaseResult.ATTEMPTS_EXHAUSTED, batch
    leased = batch.model_copy(deep=True)
    leased.state = BatchState.LEASED
    leased.attempts += 1
    leased.lease_owner = owner
    leased.lease_expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
    leased.updated_at = now.isoformat()
    return LeaseResult.ACQUIRED, leased


class ImpactJobStore(ABC):
    @abstractmethod
    def create_job(self, job: ImpactJob) -> ImpactJob:
        """Create-if-absent; returns the stored job (the existing one on a duplicate)."""

    @abstractmethod
    def get_job(self, tenant_id: str, job_id: str) -> ImpactJob | None: ...

    @abstractmethod
    def update_job(self, tenant_id: str, job_id: str, fields: dict[str, Any]) -> ImpactJob | None: ...

    @abstractmethod
    def find_jobs_for_mission(self, tenant_id: str, mission_id: str) -> list[ImpactJob]:
        """All jobs of a mission (one per attempt), newest first. Tenant-scoped."""

    @abstractmethod
    def ensure_batches(self, tenant_id: str, job_id: str, specs: list[tuple[int, int, int]]) -> None:
        """Idempotently creates PENDING batch records `(batch_no, row_start, row_end)`."""

    @abstractmethod
    def get_batch(self, tenant_id: str, job_id: str, batch_no: int) -> BatchRecord | None: ...

    @abstractmethod
    def list_batches(self, tenant_id: str, job_id: str) -> list[BatchRecord]: ...

    @abstractmethod
    def lease_batch(
        self, tenant_id: str, job_id: str, batch_no: int, owner: str, lease_seconds: int, max_attempts: int
    ) -> tuple[LeaseResult, BatchRecord | None]: ...

    @abstractmethod
    def complete_batch(
        self, tenant_id: str, job_id: str, batch_no: int, owner: str, result: dict[str, Any], state: BatchState
    ) -> bool:
        """Writes a batch result iff `owner` still holds the lease (fencing)."""

    @abstractmethod
    def finalize_job(
        self, tenant_id: str, job_id: str, status: ImpactStatus, aggregate: dict[str, Any]
    ) -> tuple[ImpactJob | None, bool]:
        """First-writer-wins finalization. Returns (job, created); `created` is
        False when the job had already been finalized (the stored aggregate wins)."""

    def request_cancel(self, tenant_id: str, job_id: str) -> ImpactJob | None:
        return self.update_job(tenant_id, job_id, {"cancel_requested": True})


class InMemoryImpactStore(ImpactJobStore):
    def __init__(self, clock: Clock = _utcnow) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[tuple[str, str], ImpactJob] = {}
        self._batches: dict[tuple[str, str], dict[int, BatchRecord]] = {}
        self._clock = clock

    def create_job(self, job: ImpactJob) -> ImpactJob:
        with self._lock:
            key = (job.tenant_id, job.job_id)
            self._jobs.setdefault(key, job.model_copy(deep=True))
            return self._jobs[key].model_copy(deep=True)

    def get_job(self, tenant_id: str, job_id: str) -> ImpactJob | None:
        with self._lock:
            job = self._jobs.get((tenant_id, job_id))
            return job.model_copy(deep=True) if job else None

    def update_job(self, tenant_id: str, job_id: str, fields: dict[str, Any]) -> ImpactJob | None:
        with self._lock:
            job = self._jobs.get((tenant_id, job_id))
            if job is None:
                return None
            self._jobs[(tenant_id, job_id)] = job.model_copy(update=fields)
            return self._jobs[(tenant_id, job_id)].model_copy(deep=True)

    def find_jobs_for_mission(self, tenant_id: str, mission_id: str) -> list[ImpactJob]:
        with self._lock:
            found = [
                j.model_copy(deep=True) for (t, _), j in self._jobs.items()
                if t == tenant_id and j.mission_id == mission_id
            ]
        return sorted(found, key=lambda j: j.created_at, reverse=True)

    def ensure_batches(self, tenant_id: str, job_id: str, specs: list[tuple[int, int, int]]) -> None:
        with self._lock:
            bucket = self._batches.setdefault((tenant_id, job_id), {})
            for no, start, end in specs:
                bucket.setdefault(
                    no, BatchRecord(tenant_id=tenant_id, job_id=job_id, batch_no=no, row_start=start, row_end=end)
                )

    def get_batch(self, tenant_id: str, job_id: str, batch_no: int) -> BatchRecord | None:
        with self._lock:
            b = self._batches.get((tenant_id, job_id), {}).get(batch_no)
            return b.model_copy(deep=True) if b else None

    def list_batches(self, tenant_id: str, job_id: str) -> list[BatchRecord]:
        with self._lock:
            bucket = self._batches.get((tenant_id, job_id), {})
            return [bucket[k].model_copy(deep=True) for k in sorted(bucket)]

    def lease_batch(self, tenant_id, job_id, batch_no, owner, lease_seconds, max_attempts):
        with self._lock:
            job = self._jobs.get((tenant_id, job_id))
            if job is None:
                return LeaseResult.NOT_FOUND, None
            if job.status in TERMINAL_JOB_STATUSES or job.cancel_requested:
                return LeaseResult.JOB_CLOSED, None
            batch = self._batches.get((tenant_id, job_id), {}).get(batch_no)
            if batch is None:
                return LeaseResult.NOT_FOUND, None
            outcome, updated = _lease_decision(batch, owner, lease_seconds, max_attempts, self._clock())
            if outcome == LeaseResult.ACQUIRED and updated is not None:
                self._batches[(tenant_id, job_id)][batch_no] = updated
                return outcome, updated.model_copy(deep=True)
            return outcome, batch.model_copy(deep=True)

    def complete_batch(self, tenant_id, job_id, batch_no, owner, result, state) -> bool:
        with self._lock:
            batch = self._batches.get((tenant_id, job_id), {}).get(batch_no)
            if batch is None or batch.state != BatchState.LEASED or batch.lease_owner != owner:
                return False
            update = {**result, "state": state, "lease_owner": None, "lease_expires_at": None,
                      "updated_at": self._clock().isoformat()}
            if "matched" in result:  # a real result (not a lease release after a crash)
                update["completed_at"] = self._clock().isoformat()
            done = batch.model_copy(update=update)
            self._batches[(tenant_id, job_id)][batch_no] = done
            return True

    def finalize_job(self, tenant_id, job_id, status, aggregate):
        with self._lock:
            job = self._jobs.get((tenant_id, job_id))
            if job is None:
                return None, False
            if job.finalized_at is not None:
                return job.model_copy(deep=True), False
            self._jobs[(tenant_id, job_id)] = job.model_copy(
                update={"status": status, "aggregate": aggregate, "finalized_at": self._clock().isoformat()}
            )
            return self._jobs[(tenant_id, job_id)].model_copy(deep=True), True


class FirestoreImpactStore(ImpactJobStore):
    """Firestore-backed store. Top-level collection `impact_jobs`, document id
    `{tenant_id}--{job_id}`, batches in the `batches` subcollection (TTL on
    `expires_at`). Only the API/worker Admin identities reach it; the deployed
    Firestore rules deny every client SDK access."""

    def __init__(self, project_id: str, database_id: str | None = None, collection: str = "impact_jobs") -> None:
        from google.cloud import firestore

        kwargs: dict[str, Any] = {"project": project_id}
        if database_id and database_id != "(default)":
            kwargs["database"] = database_id
        self._db = firestore.Client(**kwargs)
        self._collection = collection
        self._firestore = firestore

    def _job_ref(self, tenant_id: str, job_id: str):
        return self._db.collection(self._collection).document(f"{tenant_id}--{job_id}")

    def _batch_ref(self, tenant_id: str, job_id: str, batch_no: int):
        return self._job_ref(tenant_id, job_id).collection("batches").document(f"{batch_no:05d}")

    @staticmethod
    def _clean(payload: dict[str, Any]) -> dict[str, Any]:
        from app.storage.firestore_store import sanitize_for_firestore

        return sanitize_for_firestore(payload)

    def create_job(self, job: ImpactJob) -> ImpactJob:
        ref = self._job_ref(job.tenant_id, job.job_id)
        transactional = self._firestore.transactional

        @transactional
        def _txn(txn: Any) -> dict[str, Any]:
            snap = ref.get(transaction=txn)
            if snap.exists:
                return snap.to_dict()
            data = self._clean(job.model_dump(mode="json"))
            txn.set(ref, data)
            return data

        stored = _txn(self._db.transaction())
        return ImpactJob.model_validate(stored)

    def get_job(self, tenant_id: str, job_id: str) -> ImpactJob | None:
        snap = self._job_ref(tenant_id, job_id).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.get("tenant_id") != tenant_id:
            return None
        return ImpactJob.model_validate(data)

    def update_job(self, tenant_id: str, job_id: str, fields: dict[str, Any]) -> ImpactJob | None:
        ref = self._job_ref(tenant_id, job_id)
        if self.get_job(tenant_id, job_id) is None:
            return None
        ref.update(self._clean(fields))
        return self.get_job(tenant_id, job_id)

    def find_jobs_for_mission(self, tenant_id: str, mission_id: str) -> list[ImpactJob]:
        # Two equality filters: served by Firestore's built-in index merging.
        query = (
            self._db.collection(self._collection)
            .where("tenant_id", "==", tenant_id)
            .where("mission_id", "==", mission_id)
            .limit(20)
        )
        jobs = [ImpactJob.model_validate(d.to_dict()) for d in query.stream()]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def ensure_batches(self, tenant_id: str, job_id: str, specs: list[tuple[int, int, int]]) -> None:
        expires = _utcnow() + timedelta(days=BATCH_RETENTION_DAYS)
        existing = {int(d.id) for d in self._job_ref(tenant_id, job_id).collection("batches").select([]).stream()}
        writer = self._db.bulk_writer()
        for no, start, end in specs:
            if no in existing:
                continue
            rec = BatchRecord(tenant_id=tenant_id, job_id=job_id, batch_no=no, row_start=start, row_end=end)
            data = self._clean(rec.model_dump(mode="json"))
            data["expires_at"] = expires
            writer.create(self._batch_ref(tenant_id, job_id, no), data)
        writer.close()

    def _to_batch(self, data: dict[str, Any] | None, tenant_id: str) -> BatchRecord | None:
        if not data or data.get("tenant_id") != tenant_id:
            return None
        return BatchRecord.model_validate({k: v for k, v in data.items() if k != "expires_at"})

    def get_batch(self, tenant_id: str, job_id: str, batch_no: int) -> BatchRecord | None:
        snap = self._batch_ref(tenant_id, job_id, batch_no).get()
        return self._to_batch(snap.to_dict() if snap.exists else None, tenant_id)

    def list_batches(self, tenant_id: str, job_id: str) -> list[BatchRecord]:
        out = []
        for d in self._job_ref(tenant_id, job_id).collection("batches").stream():
            rec = self._to_batch(d.to_dict(), tenant_id)
            if rec is not None:
                out.append(rec)
        return sorted(out, key=lambda b: b.batch_no)

    def lease_batch(self, tenant_id, job_id, batch_no, owner, lease_seconds, max_attempts):
        job_ref = self._job_ref(tenant_id, job_id)
        ref = self._batch_ref(tenant_id, job_id, batch_no)

        @self._firestore.transactional
        def _txn(txn: Any) -> tuple[LeaseResult, BatchRecord | None]:
            job_snap = job_ref.get(transaction=txn)
            if not job_snap.exists or job_snap.to_dict().get("tenant_id") != tenant_id:
                return LeaseResult.NOT_FOUND, None
            jd = job_snap.to_dict()
            if jd.get("status") in {s.value for s in TERMINAL_JOB_STATUSES} or jd.get("cancel_requested"):
                return LeaseResult.JOB_CLOSED, None
            snap = ref.get(transaction=txn)
            batch = self._to_batch(snap.to_dict() if snap.exists else None, tenant_id)
            if batch is None:
                return LeaseResult.NOT_FOUND, None
            outcome, updated = _lease_decision(batch, owner, lease_seconds, max_attempts, _utcnow())
            if outcome == LeaseResult.ACQUIRED and updated is not None:
                txn.update(ref, self._clean({
                    "state": updated.state, "attempts": updated.attempts, "lease_owner": updated.lease_owner,
                    "lease_expires_at": updated.lease_expires_at, "updated_at": updated.updated_at,
                }))
                return outcome, updated
            return outcome, batch

        return _txn(self._db.transaction())

    def complete_batch(self, tenant_id, job_id, batch_no, owner, result, state) -> bool:
        ref = self._batch_ref(tenant_id, job_id, batch_no)

        @self._firestore.transactional
        def _txn(txn: Any) -> bool:
            snap = ref.get(transaction=txn)
            data = snap.to_dict() if snap.exists else None
            if (not data or data.get("tenant_id") != tenant_id or data.get("state") != BatchState.LEASED.value
                    or data.get("lease_owner") != owner):
                return False
            now = _utcnow().isoformat()
            payload = {**result, "state": state, "lease_owner": None, "lease_expires_at": None, "updated_at": now}
            if "matched" in result:  # a real result (not a lease release after a crash)
                payload["completed_at"] = now
            txn.update(ref, self._clean(payload))
            return True

        return _txn(self._db.transaction())

    def finalize_job(self, tenant_id, job_id, status, aggregate):
        ref = self._job_ref(tenant_id, job_id)

        @self._firestore.transactional
        def _txn(txn: Any) -> tuple[dict[str, Any] | None, bool]:
            snap = ref.get(transaction=txn)
            if not snap.exists or snap.to_dict().get("tenant_id") != tenant_id:
                return None, False
            data = snap.to_dict()
            if data.get("finalized_at"):
                return data, False
            update = self._clean({"status": status, "aggregate": aggregate, "finalized_at": _utcnow().isoformat()})
            txn.update(ref, update)
            return {**data, **update}, True

        stored, created = _txn(self._db.transaction())
        return (ImpactJob.model_validate(stored) if stored else None), created


_STORE: ImpactJobStore | None = None


def get_impact_store() -> ImpactJobStore:
    """Process-wide store. Firestore when the run store is Firestore-backed,
    otherwise in-memory (local mode / tests)."""
    global _STORE
    if _STORE is None:
        import os

        from app.core.config import get_settings

        settings = get_settings()
        if os.environ.get("RATEGUARD_RUN_STORE", "").lower() == "firestore" or os.environ.get("FIRESTORE_EMULATOR_HOST"):
            _STORE = FirestoreImpactStore(
                settings.google_cloud_project, os.environ.get("RATEGUARD_FIRESTORE_DATABASE") or None
            )
        else:
            _STORE = InMemoryImpactStore()
    return _STORE


def set_impact_store(store: ImpactJobStore | None) -> None:
    """Test hook."""
    global _STORE
    _STORE = store
