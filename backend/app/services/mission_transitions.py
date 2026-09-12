"""Explicit server-side status transition policy for Assurance Mission V2.

This module is the single source of truth for which mission lifecycle
transitions are legal, and for computing which UI actions (cancel/retry/
archive/delete) are currently eligible for a given persisted run record.
Every mutating mission endpoint and the worker/supervisor MUST route status
changes through `apply_transition` so a terminal state (e.g. CANCELLED) can
never be silently overwritten by a late-arriving worker result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.storage.models import AssuranceRunRecord, AssuranceRunStatus

# Lifecycle: DRAFT -> VALIDATING -> QUEUED -> RUNNING -> COMPLETED/FAILED/NEEDS_REVIEW
# with WAITING_RETRY as a loop-back and CANCELLED/ARCHIVED as absorbing side branches.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"VALIDATING", "QUEUED", "CANCELLED", "FAILED"},
    "VALIDATING": {"QUEUED", "FAILED", "CANCELLED"},
    "QUEUED": {"RUNNING", "CANCELLED", "FAILED"},
    "RUNNING": {"WAITING_RETRY", "NEEDS_REVIEW", "COMPLETED", "FAILED", "CANCELLED"},
    "WAITING_RETRY": {"QUEUED", "RUNNING", "CANCELLED", "FAILED"},
    "NEEDS_REVIEW": {"COMPLETED", "CANCELLED", "ARCHIVED"},
    "COMPLETED": {"ARCHIVED"},
    "FAILED": {"WAITING_RETRY", "QUEUED", "ARCHIVED", "CANCELLED"},
    "CANCELLED": {"ARCHIVED"},
    "ARCHIVED": set(),
}

# Statuses in which no worker should still be actively executing stages.
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "ARCHIVED"}

# Statuses from which a direct user-initiated cancel transitions immediately to CANCELLED.
DIRECT_CANCELLABLE_STATUSES = {"QUEUED", "VALIDATING", "WAITING_RETRY", "DRAFT"}
# Statuses from which cancel only *requests* cooperative cancellation (worker checks flag).
COOPERATIVE_CANCELLABLE_STATUSES = {"RUNNING"}
CANCELLABLE_STATUSES = DIRECT_CANCELLABLE_STATUSES | COOPERATIVE_CANCELLABLE_STATUSES

RETRYABLE_STATUSES = {"FAILED", "WAITING_RETRY"}
ARCHIVABLE_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}

MAX_RETRY_ATTEMPTS = 5


def _status_str(record: AssuranceRunRecord) -> str:
    status = record.status
    return status.value if hasattr(status, "value") else str(status)


def is_demo_record(record: AssuranceRunRecord) -> bool:
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    if bool(meta.get("is_demo_sample")) or bool(meta.get("disposable_sample_run")):
        return True
    return record.run_id.startswith("RUN-DEMO") or record.run_id.startswith("MIS-SAMPLE")


def is_retryable(record: AssuranceRunRecord) -> bool:
    if _status_str(record) not in RETRYABLE_STATUSES:
        return False
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    attempt_number = int(meta.get("attempt_number", 1) or 1)
    return attempt_number < MAX_RETRY_ATTEMPTS


def is_deletable(record: AssuranceRunRecord) -> bool:
    """Only disposable DRAFT/CANCELLED records, or eligible FAILED demo records, may be hard-deleted.
    Completed or otherwise compliance-retained missions must be archived instead."""
    status = _status_str(record)
    if status in ("DRAFT", "CANCELLED"):
        return True
    if status == "FAILED" and is_demo_record(record):
        return True
    return False


def eligible_actions(record: AssuranceRunRecord) -> dict[str, bool]:
    status = _status_str(record)
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    already_archived = bool(meta.get("archived"))
    return {
        "cancel": status in CANCELLABLE_STATUSES and not meta.get("cancellation_requested"),
        "retry": is_retryable(record),
        "archive": status in ARCHIVABLE_STATUSES and not already_archived,
        "delete": is_deletable(record),
    }


def validate_transition(current_status: str, target_status: str) -> bool:
    """Returns True if the transition is legal, or if it is a no-op (current == target)."""
    if current_status == target_status:
        return True
    return target_status in ALLOWED_TRANSITIONS.get(current_status, set())


@dataclass
class TransitionResult:
    ok: bool
    record: AssuranceRunRecord | None
    error: str | None = None


def apply_transition(
    store: Any,
    run_id: str,
    target_status: AssuranceRunStatus | str,
    *,
    status_reason: str | None = None,
    current_stage: str | None = None,
    workflow_stage: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    expected_current_status: str | None = None,
) -> TransitionResult:
    """Atomically-intentioned status transition honoring the allowed-transition graph.

    Never overwrites a terminal CANCELLED/ARCHIVED record with a non-terminal or
    contradictory status arriving from a stale worker. Callers MUST use this instead
    of writing `record.status` directly whenever the change represents a lifecycle
    transition (as opposed to in-place progress metadata updates).

    `expected_current_status`, when passed, turns this into a compare-and-set guard:
    if the record's current status no longer matches what the caller observed when
    it decided to act (e.g. two concurrent /retry requests both reading FAILED), the
    second caller's transition is refused rather than silently double-dispatching a
    duplicate side effect (e.g. a second Pub/Sub publish).
    """
    record = store.get_run(run_id)
    if record is None:
        return TransitionResult(ok=False, record=None, error="NOT_FOUND")

    current = _status_str(record)

    if expected_current_status is not None and current != expected_current_status:
        return TransitionResult(
            ok=False,
            record=record,
            error=f"Concurrent modification: expected status '{expected_current_status}' but found '{current}'.",
        )

    target_value = target_status.value if hasattr(target_status, "value") else str(target_status)

    if not validate_transition(current, target_value):
        return TransitionResult(
            ok=False,
            record=record,
            error=f"Invalid status transition: {current} -> {target_value}",
        )

    if current != target_value:
        record.status = AssuranceRunStatus(target_value)
    if workflow_stage is not None:
        record.workflow_stage = workflow_stage
    if current_stage is not None:
        record.current_stage = current_stage
    if status_reason is not None:
        record.status_reason = status_reason

    now = datetime.now(UTC)
    if target_value == "RUNNING" and record.started_at is None:
        record.started_at = now
    if target_value in TERMINAL_STATUSES and record.completed_at is None:
        record.completed_at = now

    if extra_metadata:
        if not isinstance(record.metadata, dict):
            record.metadata = {}
        record.metadata.update(extra_metadata)

    store.update_run(record)
    return TransitionResult(ok=True, record=record, error=None)
