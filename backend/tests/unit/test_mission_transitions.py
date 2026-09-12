import pytest

from app.models.mission import MissionStatus
from app.services.mission_transitions import (
    ALLOWED_TRANSITIONS,
    MAX_RETRY_ATTEMPTS,
    apply_transition,
    eligible_actions,
    is_deletable,
    is_retryable,
    validate_transition,
)
from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store, reset_run_store


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_run_store()
    yield
    reset_run_store()


@pytest.mark.parametrize(
    "current,target,expected",
    [
        ("QUEUED", "RUNNING", True),
        ("QUEUED", "CANCELLED", True),
        ("RUNNING", "COMPLETED", True),
        ("RUNNING", "WAITING_RETRY", True),
        ("WAITING_RETRY", "QUEUED", True),
        ("COMPLETED", "ARCHIVED", True),
        ("CANCELLED", "ARCHIVED", True),
        ("FAILED", "WAITING_RETRY", True),
        # Rejected transitions:
        ("CANCELLED", "COMPLETED", False),
        ("CANCELLED", "RUNNING", False),
        ("ARCHIVED", "RUNNING", False),
        ("COMPLETED", "RUNNING", False),
        ("QUEUED", "ARCHIVED", False),
        ("DRAFT", "RUNNING", False),
    ],
)
def test_validate_transition_matrix(current, target, expected):
    assert validate_transition(current, target) is expected


def test_validate_transition_is_idempotent_noop():
    assert validate_transition("RUNNING", "RUNNING") is True
    assert validate_transition("ARCHIVED", "ARCHIVED") is True


def test_every_mission_v2_status_has_an_explicit_transition_entry():
    """Covers the 10 Mission V2 lifecycle statuses specifically (MissionStatus).
    AssuranceRunStatus additionally carries legacy-run-only values (CREATED,
    PENDING, PROCESSING, IN_PROGRESS) that never flow through apply_transition —
    the legacy worker path updates status directly, not via this policy."""
    for status in MissionStatus:
        assert status.value in ALLOWED_TRANSITIONS, f"{status.value} missing from transition policy"


def test_apply_transition_rejects_invalid_and_leaves_record_untouched():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-T1", status=AssuranceRunStatus.CANCELLED))

    result = apply_transition(store, "MIS-T1", AssuranceRunStatus.COMPLETED)

    assert result.ok is False
    # Record must remain CANCELLED — never overwritten by a disallowed transition.
    rec = store.get_run("MIS-T1")
    assert rec.status == AssuranceRunStatus.CANCELLED


def test_apply_transition_never_overwrites_cancelled_with_completed():
    """Directly exercises the 'worker must not overwrite CANCELLED with COMPLETED' rule."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-T2", status=AssuranceRunStatus.RUNNING))

    # User cancels while a (simulated) worker is mid-flight.
    apply_transition(store, "MIS-T2", AssuranceRunStatus.CANCELLED, status_reason="User cancelled.")
    assert store.get_run("MIS-T2").status == AssuranceRunStatus.CANCELLED

    # The worker's late-arriving completion must be refused.
    late_result = apply_transition(store, "MIS-T2", AssuranceRunStatus.COMPLETED)
    assert late_result.ok is False
    assert store.get_run("MIS-T2").status == AssuranceRunStatus.CANCELLED


def test_apply_transition_compare_and_set_rejects_stale_expected_status():
    """Direct test of the race-prevention mechanism: two callers both observing
    FAILED must not both succeed — the second's expectation is stale once the
    first has written QUEUED."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CAS-1", status=AssuranceRunStatus.FAILED))

    first = apply_transition(store, "MIS-CAS-1", AssuranceRunStatus.QUEUED, expected_current_status="FAILED")
    assert first.ok is True

    second = apply_transition(store, "MIS-CAS-1", AssuranceRunStatus.QUEUED, expected_current_status="FAILED")
    assert second.ok is False
    assert "Concurrent modification" in second.error
    # Only the first writer's attempt is reflected; status wasn't touched again.
    assert store.get_run("MIS-CAS-1").status == AssuranceRunStatus.QUEUED


def test_apply_transition_compare_and_set_matches_current_status_succeeds():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CAS-2", status=AssuranceRunStatus.FAILED))

    result = apply_transition(store, "MIS-CAS-2", AssuranceRunStatus.QUEUED, expected_current_status="FAILED")
    assert result.ok is True
    assert store.get_run("MIS-CAS-2").status == AssuranceRunStatus.QUEUED


def test_apply_transition_not_found():
    store = get_run_store()
    result = apply_transition(store, "MIS-DOES-NOT-EXIST", AssuranceRunStatus.RUNNING)
    assert result.ok is False
    assert result.error == "NOT_FOUND"


def test_eligible_actions_queued_can_cancel_not_delete_not_retry():
    rec = AssuranceRunRecord(run_id="MIS-E1", status=AssuranceRunStatus.QUEUED)
    actions = eligible_actions(rec)
    assert actions == {"cancel": True, "retry": False, "archive": False, "delete": False}


def test_eligible_actions_running_can_only_request_cancel():
    rec = AssuranceRunRecord(run_id="MIS-E2", status=AssuranceRunStatus.RUNNING)
    actions = eligible_actions(rec)
    assert actions["cancel"] is True
    assert actions["delete"] is False


def test_eligible_actions_completed_can_archive_not_delete():
    rec = AssuranceRunRecord(run_id="MIS-E3", status=AssuranceRunStatus.COMPLETED)
    actions = eligible_actions(rec)
    assert actions["archive"] is True
    assert actions["delete"] is False
    assert actions["cancel"] is False


def test_eligible_actions_completed_demo_still_cannot_delete():
    """Completed missions are never directly deletable, demo or not — archive only."""
    rec = AssuranceRunRecord(
        run_id="MIS-E4",
        status=AssuranceRunStatus.COMPLETED,
        metadata={"is_demo_sample": True},
    )
    actions = eligible_actions(rec)
    assert actions["delete"] is False
    assert actions["archive"] is True


def test_eligible_actions_failed_demo_can_delete():
    rec = AssuranceRunRecord(
        run_id="MIS-E5",
        status=AssuranceRunStatus.FAILED,
        metadata={"is_demo_sample": True},
    )
    assert is_deletable(rec) is True


def test_eligible_actions_failed_non_demo_cannot_delete():
    rec = AssuranceRunRecord(run_id="MIS-E6", status=AssuranceRunStatus.FAILED, metadata={})
    assert is_deletable(rec) is False


def test_eligible_actions_draft_and_cancelled_are_deletable():
    assert is_deletable(AssuranceRunRecord(run_id="MIS-E7", status=AssuranceRunStatus.DRAFT)) is True
    assert is_deletable(AssuranceRunRecord(run_id="MIS-E8", status=AssuranceRunStatus.CANCELLED)) is True


def test_is_retryable_failed_and_waiting_retry_under_max_attempts():
    failed = AssuranceRunRecord(run_id="MIS-R1", status=AssuranceRunStatus.FAILED, metadata={"attempt_number": 1})
    waiting = AssuranceRunRecord(run_id="MIS-R2", status=AssuranceRunStatus.WAITING_RETRY, metadata={"attempt_number": 2})
    assert is_retryable(failed) is True
    assert is_retryable(waiting) is True


def test_is_retryable_false_once_max_attempts_reached():
    exhausted = AssuranceRunRecord(
        run_id="MIS-R3",
        status=AssuranceRunStatus.FAILED,
        metadata={"attempt_number": MAX_RETRY_ATTEMPTS},
    )
    assert is_retryable(exhausted) is False


def test_is_retryable_false_for_non_retryable_status():
    completed = AssuranceRunRecord(run_id="MIS-R4", status=AssuranceRunStatus.COMPLETED)
    assert is_retryable(completed) is False
