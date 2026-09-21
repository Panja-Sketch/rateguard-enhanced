"""Typed records for connector-backed impact jobs and batches.

Every record carries `tenant_id`; the storage layer keys documents by a
tenant-prefixed identifier and re-checks the tenant on every read, so a
record of another tenant is indistinguishable from a missing one.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ImpactStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class BatchState(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    DONE = "DONE"  # every row resolved (matched / affected / permanent-inconclusive / out of scope)
    INCOMPLETE = "INCOMPLETE"  # transient failures remain; eligible for a bounded re-attempt


class LeaseResult(StrEnum):
    ACQUIRED = "ACQUIRED"
    ALREADY_DONE = "ALREADY_DONE"
    LEASED_ELSEWHERE = "LEASED_ELSEWHERE"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    NOT_FOUND = "NOT_FOUND"
    JOB_CLOSED = "JOB_CLOSED"


TERMINAL_JOB_STATUSES = frozenset(
    {ImpactStatus.COMPLETE, ImpactStatus.PARTIAL, ImpactStatus.CANCELLED, ImpactStatus.FAILED}
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def compute_job_id(
    tenant_id: str,
    mission_id: str,
    attempt_number: int,
    snapshot_sha256: str,
    source_sha256: str,
    connector_id: str,
    engine_version: str,
    batch_size: int,
    row_limit: int,
) -> str:
    """Deterministic idempotency identity: the same tenant, mission attempt,
    portfolio snapshot, authoritative source and connector always map to the
    same job (so a redelivered mission resumes it), while a mission retry
    (new attempt) or a changed snapshot/connector starts a fresh one."""
    material = "|".join(
        [tenant_id, mission_id, str(attempt_number), snapshot_sha256, source_sha256,
         connector_id, engine_version, str(batch_size), str(row_limit)]
    )
    return "IJ-" + hashlib.sha256(material.encode()).hexdigest()[:24]


class ImpactJob(BaseModel):
    tenant_id: str
    job_id: str
    mission_id: str
    attempt_number: int = 1
    created_at: str = Field(default_factory=now_iso)
    as_of: str  # ISO date pinned at creation so 30/60/90-day windows are reproducible
    status: ImpactStatus = ImpactStatus.QUEUED
    product: str
    jurisdiction: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    connector: dict[str, Any] = Field(default_factory=dict)
    batch_size: int
    batch_count: int
    rows_total: int
    rows_in_scope: int  # rows covered by batches (<= rows_total; smaller when the row budget truncates)
    budgets: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    halt_reason: str | None = None
    budget_exhausted: list[str] = Field(default_factory=list)
    # Live progress counters written by the coordinator each poll (no PII, no rows).
    progress: dict[str, Any] = Field(default_factory=dict)
    aggregate: dict[str, Any] | None = None
    finalized_at: str | None = None


class BatchRecord(BaseModel):
    tenant_id: str
    job_id: str
    batch_no: int
    row_start: int
    row_end: int  # exclusive
    state: BatchState = BatchState.PENDING
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    matched: int = 0
    # [row_index, reason] — rows the authoritative source does not apply to
    # (product mismatch, or effective date outside the source's effective period).
    out_of_scope: list[list[Any]] = Field(default_factory=list)
    # [row_index, expected_premium, candidate_premium] — opaque row references only.
    affected: list[list[Any]] = Field(default_factory=list)
    # [row_index, error_code, transient]
    inconclusive: list[list[Any]] = Field(default_factory=list)
    retries: int = 0
    requests: int = 0
    duration_ms: int = 0
    error_classes: dict[str, int] = Field(default_factory=dict)
    breaker_opened: bool = False
    connector_revision: str | None = None
    completed_at: str | None = None
    updated_at: str = Field(default_factory=now_iso)

    @property
    def rows(self) -> int:
        return self.row_end - self.row_start

    @property
    def unresolved_transient(self) -> list[int]:
        return [int(r[0]) for r in self.inconclusive if r[2]]
