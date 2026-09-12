"""API-level tests for Group 1 (input integrity) and Group 2/3 (lifecycle, cancel,
retry, delete, eligible_actions) of the Foundation implementation."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store, reset_run_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_run_store()
    yield
    reset_run_store()


def _valid_release_conformance_payload(**overrides):
    payload = {
        "name": "Lifecycle Test Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Group 1: no hidden demo defaults, mode-specific validation, structured 422
# ---------------------------------------------------------------------------


def test_omitted_source_a_and_source_b_are_rejected_not_defaulted():
    """Omitting source_a/source_b must never silently resolve to the bundled
    Arizona demo packages — it must be rejected with a structured 422."""
    payload = {
        "name": "No Sources Provided",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
    }
    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 422
    fields = {i["field"] for i in res.json()["detail"]["issues"]}
    assert "source_a" in fields


def test_explicit_null_source_b_is_rejected_for_release_conformance():
    payload = _valid_release_conformance_payload(source_b=None)
    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 422
    fields = {i["field"] for i in res.json()["detail"]["issues"]}
    assert "source_b" in fields


def test_equivalence_mode_requires_both_sources_explicitly():
    payload = {
        "name": "Equivalence Missing B",
        "mode": "EQUIVALENCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "A"},
    }
    res = client.post("/api/v1/missions", json=payload)
    assert res.status_code == 422
    fields = {i["field"] for i in res.json()["detail"]["issues"]}
    assert "source_b" in fields


def test_structured_422_issues_have_field_code_message():
    res = client.post("/api/v1/missions", json={"mode": "RELEASE_CONFORMANCE"})
    assert res.status_code == 422
    for issue in res.json()["detail"]["issues"]:
        assert set(issue.keys()) >= {"field", "code", "message"}


# ---------------------------------------------------------------------------
# Group 2: cancel / retry / archive / delete lifecycle
# ---------------------------------------------------------------------------


def test_cancel_queued_mission_transitions_directly_to_cancelled():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CANCEL-01", status=AssuranceRunStatus.QUEUED))

    res = client.post("/api/v1/missions/MIS-CANCEL-01/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELLED"
    assert store.get_run("MIS-CANCEL-01").status == AssuranceRunStatus.CANCELLED


def test_cancel_running_mission_sets_cancellation_requested_not_terminal_state():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CANCEL-02", status=AssuranceRunStatus.RUNNING))

    res = client.post("/api/v1/missions/MIS-CANCEL-02/cancel")
    assert res.status_code == 200
    body = res.json()
    assert body["cancellation_requested"] is True
    rec = store.get_run("MIS-CANCEL-02")
    assert rec.status == AssuranceRunStatus.RUNNING  # cooperative, not immediate
    assert rec.cancellation_requested is True


def test_cancel_is_idempotent_on_already_cancelled_mission():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CANCEL-03", status=AssuranceRunStatus.CANCELLED))

    res = client.post("/api/v1/missions/MIS-CANCEL-03/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELLED"


def test_cancel_completed_mission_returns_409():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-CANCEL-04", status=AssuranceRunStatus.COMPLETED))

    res = client.post("/api/v1/missions/MIS-CANCEL-04/cancel")
    assert res.status_code == 409


def test_cancel_unknown_mission_returns_404():
    res = client.post("/api/v1/missions/MIS-DOES-NOT-EXIST/cancel")
    assert res.status_code == 404


def test_retry_failed_mission_increments_attempt_and_requeues():
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(
            run_id="MIS-RETRY-01",
            status=AssuranceRunStatus.FAILED,
            left_package_id="AZ_HO3_2026_09",
            right_package_id="AZ_HO3_2026_09_CLEAN",
            metadata={"attempt_number": 1, "source_a": "AZ_HO3_2026_09", "source_b": "AZ_HO3_2026_09_CLEAN"},
        )
    )

    mock_publisher = MagicMock()
    with patch("app.api.missions.get_message_publisher", return_value=mock_publisher):
        res = client.post("/api/v1/missions/MIS-RETRY-01/retry")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "QUEUED"
    assert body["attempt_number"] == 2
    rec = store.get_run("MIS-RETRY-01")
    assert rec.status == AssuranceRunStatus.QUEUED
    assert rec.attempt_number == 2
    assert mock_publisher.publish_assurance_job.called


def test_retry_endpoint_uses_compare_and_set_against_observed_status():
    """The retry handler passes expected_current_status to apply_transition so a
    genuinely concurrent second caller (which read the same pre-write status) is
    refused instead of double-publishing. See test_mission_transitions.py for the
    direct compare-and-set unit test of apply_transition itself."""
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(run_id="MIS-RETRY-CAS", status=AssuranceRunStatus.FAILED, metadata={"attempt_number": 1})
    )

    with patch("app.api.missions.apply_transition") as mock_apply:
        mock_apply.return_value = MagicMock(ok=True, record=store.get_run("MIS-RETRY-CAS"))
        mock_publisher = MagicMock()
        with patch("app.api.missions.get_message_publisher", return_value=mock_publisher):
            client.post("/api/v1/missions/MIS-RETRY-CAS/retry")

    assert mock_apply.call_args.kwargs.get("expected_current_status") == "FAILED"


def test_retry_completed_mission_returns_409():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-RETRY-02", status=AssuranceRunStatus.COMPLETED))

    res = client.post("/api/v1/missions/MIS-RETRY-02/retry")
    assert res.status_code == 409


def test_retry_exhausted_attempts_returns_409():
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(
            run_id="MIS-RETRY-03",
            status=AssuranceRunStatus.FAILED,
            metadata={"attempt_number": 5},
        )
    )
    res = client.post("/api/v1/missions/MIS-RETRY-03/retry")
    assert res.status_code == 409


def test_archive_completed_mission_preserves_status_not_forced_to_completed():
    """Archiving a FAILED mission must not force it to COMPLETED."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-ARCHIVE-01", status=AssuranceRunStatus.FAILED))

    res = client.post("/api/v1/missions/MIS-ARCHIVE-01/archive")
    assert res.status_code == 200
    rec = store.get_run("MIS-ARCHIVE-01")
    assert rec.status == AssuranceRunStatus.ARCHIVED
    assert rec.metadata.get("archived") is True


def test_archive_queued_mission_returns_409():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-ARCHIVE-02", status=AssuranceRunStatus.QUEUED))

    res = client.post("/api/v1/missions/MIS-ARCHIVE-02/archive")
    assert res.status_code == 409


def test_archived_mission_hidden_from_default_list_but_visible_via_status_filter():
    """Archived missions must not appear in the default (unfiltered) history
    view, but must still be retrievable by explicitly filtering status=ARCHIVED."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(
        run_id="MIS-ARCHIVE-03", status=AssuranceRunStatus.COMPLETED,
        metadata={"record_type": "ASSURANCE_MISSION_V2"},
    ))
    archive_res = client.post("/api/v1/missions/MIS-ARCHIVE-03/archive")
    assert archive_res.status_code == 200

    default_res = client.get("/api/v1/missions")
    assert default_res.status_code == 200
    default_ids = [m["mission_id"] for m in default_res.json()["missions"]]
    assert "MIS-ARCHIVE-03" not in default_ids

    archived_res = client.get("/api/v1/missions?status=ARCHIVED")
    assert archived_res.status_code == 200
    archived_ids = [m["mission_id"] for m in archived_res.json()["missions"]]
    assert "MIS-ARCHIVE-03" in archived_ids


def test_delete_queued_mission_returns_409_not_deleted():
    """QUEUED missions are not directly deletable — they must be cancelled first."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-DELETE-01", status=AssuranceRunStatus.QUEUED))

    res = client.delete("/api/v1/missions/MIS-DELETE-01")
    assert res.status_code == 409
    assert store.get_run("MIS-DELETE-01") is not None


def test_delete_cancelled_mission_succeeds_and_is_confirmed_gone():
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-DELETE-02", status=AssuranceRunStatus.CANCELLED))

    res = client.delete("/api/v1/missions/MIS-DELETE-02")
    assert res.status_code == 200
    assert res.json()["status"] == "DELETED"
    assert store.get_run("MIS-DELETE-02") is None


def test_delete_returns_404_for_unknown_mission():
    res = client.delete("/api/v1/missions/MIS-NEVER-EXISTED")
    assert res.status_code == 404


def test_delete_never_reports_success_when_store_deletion_fails():
    """Even if a mission is policy-eligible for deletion, the endpoint must not
    report success unless the backing store confirms the record is actually gone."""
    store = get_run_store()
    store.save_run(AssuranceRunRecord(run_id="MIS-DELETE-03", status=AssuranceRunStatus.CANCELLED))

    with patch.object(type(store), "delete_run", return_value=False):
        res = client.delete("/api/v1/missions/MIS-DELETE-03")

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "MISSION_DELETE_FAILED"


# ---------------------------------------------------------------------------
# Group 3: eligible_actions parity between list/detail and actual enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_value,expected",
    [
        (AssuranceRunStatus.QUEUED, {"cancel": True, "retry": False, "archive": False, "delete": False}),
        (AssuranceRunStatus.RUNNING, {"cancel": True, "retry": False, "archive": False, "delete": False}),
        (AssuranceRunStatus.CANCELLED, {"cancel": False, "retry": False, "archive": True, "delete": True}),
        (AssuranceRunStatus.COMPLETED, {"cancel": False, "retry": False, "archive": True, "delete": False}),
        (AssuranceRunStatus.FAILED, {"cancel": False, "retry": True, "archive": True, "delete": False}),
    ],
)
def test_eligible_actions_in_list_matches_actual_endpoint_enforcement(status_value, expected):
    store = get_run_store()
    run_id = f"MIS-ELIGIBLE-{status_value.value}"
    store.save_run(AssuranceRunRecord(run_id=run_id, status=status_value, metadata={"attempt_number": 1}))

    list_res = client.get("/api/v1/missions")
    row = next(m for m in list_res.json()["missions"] if m["mission_id"] == run_id)
    assert row["eligible_actions"] == expected

    detail_res = client.get(f"/api/v1/missions/{run_id}")
    assert detail_res.json()["eligible_actions"] == expected

    # Cross-check: if eligible_actions says delete is False, the actual DELETE call must 409.
    if not expected["delete"]:
        del_res = client.delete(f"/api/v1/missions/{run_id}")
        assert del_res.status_code == 409
