"""Tests for the typed worker processing-outcome contract (ProcessingOutcome /
ProcessingResult) and the Pub/Sub ack/nack semantics it drives.

None of these tests touch a real Firestore, Pub/Sub, or Vertex AI endpoint —
storage failures are simulated with fake in-process store doubles, and the
supervisor is mocked wherever a full mission run isn't the point of the test.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.messaging.models import AssuranceJob
from app.messaging.outcomes import ProcessingOutcome
from app.messaging.worker import AssuranceWorker
from app.models.mission import (
    AssuranceMission,
    ComparisonMode,
    MissionObjective,
    MissionStatus,
    PricingSourceRef,
)
from app.services.mission_execution_service import MissionExecutionService
from app.storage import AssuranceRunRecord, AssuranceRunStatus, InMemoryRunStore
from app.storage.interfaces import LeaseOutcome

client = TestClient(app)


def _valid_mission_dict(mission_id: str) -> dict:
    mission = AssuranceMission(
        mission_id=mission_id,
        name="Test Mission",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        status=MissionStatus.QUEUED,
        objective=MissionObjective(
            product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01",
        ),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(
            source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="Source B",
        ),
    )
    return mission.model_dump(mode="json")


def _seed_queued_mission(store: InMemoryRunStore, mission_id: str) -> None:
    store.save_run(
        AssuranceRunRecord(
            run_id=mission_id,
            status=AssuranceRunStatus.QUEUED,
            workflow_stage="QUEUED",
            metadata={"mission_object": _valid_mission_dict(mission_id)},
        )
    )


class _RaisingStore:
    """Fake store whose every method raises, simulating a total Firestore
    outage discovered at the very first storage operation."""

    def acquire_lease(self, run_id, job_id, lease_seconds=120):
        raise RuntimeError("Firestore unavailable: 400 Invalid database id")


class _AcquiresThenRaisesStore:
    """Fake store: lease acquisition succeeds once (as if the Firestore
    transaction that acquires it went through), but every operation after
    that raises — simulating Firestore going down mid-mission, including
    during the best-effort FAILED status write."""

    def __init__(self, record: AssuranceRunRecord):
        self._record = record

    def acquire_lease(self, run_id, job_id, lease_seconds=120):
        return LeaseOutcome.ACQUIRED, self._record

    def update_run(self, record):
        raise RuntimeError("Firestore unavailable")

    def get_run(self, run_id):
        raise RuntimeError("Firestore unavailable")

    def log_event(self, **kwargs):
        raise RuntimeError("Firestore unavailable")


def test_firestore_unavailable_during_lease_acquisition_is_retryable() -> None:
    job = AssuranceJob(job_id="JOB-FS-DOWN", run_id="MIS-FS-DOWN", job_type="ASSURANCE_MISSION_V2")

    with patch("app.services.mission_execution_service.get_run_store", return_value=_RaisingStore()):
        result = MissionExecutionService.execute_job(job)

    assert result.outcome == ProcessingOutcome.RETRYABLE_FAILURE
    assert result.should_ack is False
    assert result.status_write_ok is False


def test_status_write_failure_does_not_become_false_success() -> None:
    """When the supervisor itself fails AND the best-effort FAILED status
    write also fails (Firestore is down for both), the outcome must still be
    RETRYABLE_FAILURE — never SUCCEEDED, and status_write_ok must report the
    write failure rather than silently claiming it succeeded."""
    mission_id = "MIS-DOUBLE-FAILURE"
    record = AssuranceRunRecord(
        run_id=mission_id, status=AssuranceRunStatus.RUNNING, workflow_stage="RUNNING",
        metadata={"mission_object": _valid_mission_dict(mission_id)},
    )
    job = AssuranceJob(job_id="JOB-DOUBLE-FAILURE", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    with (
        patch(
            "app.services.mission_execution_service.get_run_store",
            return_value=_AcquiresThenRaisesStore(record),
        ),
        patch("app.services.mission_execution_service.AssuranceSupervisor") as mock_supervisor_cls,
    ):
        mock_supervisor_cls.return_value.run_mission.side_effect = RuntimeError("supervisor boom")
        result = MissionExecutionService.execute_job(job)

    assert result.outcome == ProcessingOutcome.RETRYABLE_FAILURE
    assert result.should_ack is False
    assert result.status_write_ok is False


def test_cancelled_mission_acks() -> None:
    """A mission that transitions to CANCELLED during supervisor execution
    (cooperative cancellation) must be acknowledged (2xx), not treated as a
    failure."""
    mission_id = "MIS-CANCEL-ACK"
    store = InMemoryRunStore()
    _seed_queued_mission(store, mission_id)
    job = AssuranceJob(job_id="JOB-CANCEL-ACK", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    fake_result = MagicMock()
    fake_result.release_decision.data.status = "CANCELLED"

    def _fake_run_mission(mission, left_pkg, right_pkg, cancellation_check=None):
        mission.status = MissionStatus.CANCELLED
        return fake_result

    with (
        patch("app.services.mission_execution_service.get_run_store", return_value=store),
        patch("app.services.mission_execution_service.AssuranceSupervisor") as mock_supervisor_cls,
        patch("app.services.mission_execution_service.resolve_demo_package", return_value=MagicMock()),
    ):
        mock_supervisor_cls.return_value.run_mission.side_effect = _fake_run_mission
        result = MissionExecutionService.execute_job(job)

    assert result.outcome == ProcessingOutcome.CANCELLED
    assert result.should_ack is True


def test_no_duplicate_execution_under_redelivery() -> None:
    """Simulates Pub/Sub redelivering the exact same message after the first
    delivery already completed the mission: the supervisor must be invoked
    exactly once across both deliveries, and the second delivery must be
    classified as a duplicate, not re-executed."""
    mission_id = "MIS-REDELIVER"
    store = InMemoryRunStore()
    _seed_queued_mission(store, mission_id)
    job = AssuranceJob(job_id="JOB-REDELIVER", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    fake_result = MagicMock()
    fake_result.release_decision.data.status = "PASS"

    def _fake_run_mission(mission, left_pkg, right_pkg, cancellation_check=None):
        mission.status = MissionStatus.COMPLETED
        store.update_run_status(run_id=mission_id, status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED")
        return fake_result

    with (
        patch("app.services.mission_execution_service.get_run_store", return_value=store),
        patch("app.services.mission_execution_service.AssuranceSupervisor") as mock_supervisor_cls,
        patch("app.services.mission_execution_service.resolve_demo_package", return_value=MagicMock()),
    ):
        mock_supervisor_cls.return_value.run_mission.side_effect = _fake_run_mission

        first = MissionExecutionService.execute_job(job)
        second = MissionExecutionService.execute_job(job)

        assert mock_supervisor_cls.return_value.run_mission.call_count == 1

    assert first.outcome == ProcessingOutcome.SUCCEEDED
    assert first.should_ack is True
    assert second.outcome == ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED
    assert second.should_ack is True


def test_already_leased_duplicate_does_not_start_second_execution() -> None:
    """A duplicate delivery arriving while the first is still actively leased
    (not yet terminal) must be treated as a duplicate, never a second
    execution attempt."""
    mission_id = "MIS-ALREADY-LEASED"
    store = InMemoryRunStore()
    _seed_queued_mission(store, mission_id)
    job = AssuranceJob(job_id="JOB-LEASED", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    # First delivery acquires the lease and leaves the mission RUNNING
    # (as it would be mid-execution) without completing it.
    with patch(
        "app.services.mission_execution_service.get_run_store", return_value=store,
    ), patch("app.services.mission_execution_service.AssuranceSupervisor") as mock_supervisor_cls, patch(
        "app.services.mission_execution_service.resolve_demo_package", return_value=MagicMock(),
    ):
        mock_supervisor_cls.return_value.run_mission.side_effect = RuntimeError("still running, never returns")
        first = MissionExecutionService.execute_job(job)
        assert first.outcome == ProcessingOutcome.RETRYABLE_FAILURE  # the mocked "hang" surfaces as an exception here

    # Force the record back to RUNNING with a fresh heartbeat to simulate a
    # genuinely in-flight lease (the mock above transitioned it to FAILED).
    running_record = store.get_run(mission_id)
    running_record.status = AssuranceRunStatus.RUNNING
    running_record.metadata["last_heartbeat_at"] = __import__("datetime").datetime.now(
        __import__("datetime").UTC
    ).isoformat()
    store.update_run(running_record)

    with patch("app.services.mission_execution_service.get_run_store", return_value=store), patch(
        "app.services.mission_execution_service.AssuranceSupervisor",
    ) as mock_supervisor_cls_2:
        second = MissionExecutionService.execute_job(job)
        assert mock_supervisor_cls_2.return_value.run_mission.call_count == 0

    assert second.outcome == ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED
    assert second.should_ack is True


def test_poison_message_missing_mission_object_is_terminal() -> None:
    """A lease is acquired against a genuinely existing record, but with no
    `mission_object` in metadata to reconstruct the mission from — this can
    never succeed by retrying and must be classified as terminal, not
    retryable."""
    mission_id = "MIS-NO-OBJECTIVE"
    store = InMemoryRunStore()
    store.save_run(
        AssuranceRunRecord(run_id=mission_id, status=AssuranceRunStatus.QUEUED, workflow_stage="QUEUED")
    )
    job = AssuranceJob(job_id="JOB-NO-OBJECTIVE", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    with patch("app.services.mission_execution_service.get_run_store", return_value=store):
        result = MissionExecutionService.execute_job(job)

    assert result.outcome == ProcessingOutcome.TERMINAL_INVALID_MESSAGE
    assert result.should_ack is False


# --- worker_endpoint.py HTTP-level tests -----------------------------------


def test_endpoint_returns_2xx_for_duplicate_already_completed_legacy_run() -> None:
    from app.storage import get_run_store, reset_run_store

    reset_run_store()
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(run_id="RUN-DUP-ENDPOINT", status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED")
    )

    job = AssuranceJob(job_id="JOB-DUP-ENDPOINT", run_id="RUN-DUP-ENDPOINT")
    b64_data = base64.b64encode(json.dumps(job.model_dump(mode="json")).encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-DUP"}}

    res = client.post("/internal/pubsub/assurance", json=envelope)
    assert res.status_code == 200
    assert res.json()["status"] == ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED.value
    reset_run_store()


def test_endpoint_returns_non_2xx_for_malformed_envelope() -> None:
    """A structurally invalid Pub/Sub envelope (bad base64, or JSON that
    doesn't satisfy AssuranceJob) must never be acknowledged as successful."""
    envelope = {"message": {"data": "not-valid-base64!!!", "message_id": "MSG-BAD"}}
    res = client.post("/internal/pubsub/assurance", json=envelope)
    assert res.status_code >= 400
    assert res.status_code < 500 or res.status_code == 400
    assert res.json()["status"] == ProcessingOutcome.TERMINAL_INVALID_MESSAGE.value


def test_endpoint_returns_non_2xx_for_schema_invalid_job_payload() -> None:
    """Valid base64/JSON, but missing AssuranceJob's required fields — also a
    poison message, must not be acked."""
    bad_job_json = json.dumps({"only_an_unrelated_field": True})
    b64_data = base64.b64encode(bad_job_json.encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-SCHEMA-BAD"}}

    res = client.post("/internal/pubsub/assurance", json=envelope)
    assert res.status_code == 400
    assert res.json()["status"] == ProcessingOutcome.TERMINAL_INVALID_MESSAGE.value


def test_endpoint_returns_non_2xx_when_worker_raises_unexpectedly() -> None:
    """Defensive net: if AssuranceWorker itself raises (a bug in the
    dispatcher, not a classified ProcessingResult), the endpoint must still
    answer non-2xx, never silently ack a failed delivery."""
    job = AssuranceJob(job_id="JOB-UNEXPECTED", run_id="RUN-UNEXPECTED")
    b64_data = base64.b64encode(job.model_dump_json().encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-UNEXPECTED"}}

    with patch("app.api.worker_endpoint.AssuranceWorker") as mock_worker_cls:
        mock_worker_cls.return_value.process_job.side_effect = RuntimeError("dispatcher bug")
        res = client.post("/internal/pubsub/assurance", json=envelope)

    assert res.status_code == 503
    assert res.json()["status"] == ProcessingOutcome.RETRYABLE_FAILURE.value


def test_endpoint_worker_endpoint_never_returns_200_for_retryable_failure() -> None:
    """AssuranceWorker.process_job returning a RETRYABLE_FAILURE
    ProcessingResult (e.g. Firestore was down) must surface as non-2xx at the
    HTTP layer — the endpoint must not collapse every outcome to 200."""
    job = AssuranceJob(job_id="JOB-RETRY-HTTP", run_id="MIS-RETRY-HTTP", job_type="ASSURANCE_MISSION_V2")
    b64_data = base64.b64encode(job.model_dump_json().encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": "MSG-RETRY-HTTP"}}

    from app.messaging.outcomes import ProcessingResult

    with patch("app.api.worker_endpoint.AssuranceWorker") as mock_worker_cls:
        mock_worker_cls.return_value.process_job.return_value = ProcessingResult(
            outcome=ProcessingOutcome.RETRYABLE_FAILURE,
            run_id="MIS-RETRY-HTTP",
            job_id="JOB-RETRY-HTTP",
            detail="Storage layer unavailable.",
            status_write_ok=False,
        )
        res = client.post("/internal/pubsub/assurance", json=envelope)

    assert res.status_code == 503
    assert res.json()["status"] == ProcessingOutcome.RETRYABLE_FAILURE.value
    assert res.json()["status_write_ok"] is False


def test_assurance_worker_uses_strict_store_by_default() -> None:
    """AssuranceWorker's default (no explicit run_store passed) must be the
    strict store variant, not the lenient/legacy-read singleton — this is
    what makes Firestore failures surface as retryable rather than being
    silently absorbed."""
    with patch("app.messaging.worker.get_run_store") as mock_get_store:
        mock_get_store.return_value = InMemoryRunStore()
        AssuranceWorker()
        mock_get_store.assert_called_once_with(strict=True)


@pytest.mark.parametrize("outcome", list(ProcessingOutcome))
def test_should_ack_matches_documented_contract(outcome: ProcessingOutcome) -> None:
    from app.messaging.outcomes import ProcessingResult

    result = ProcessingResult(outcome=outcome)
    expected_ack = outcome in (
        ProcessingOutcome.SUCCEEDED,
        ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED,
        ProcessingOutcome.CANCELLED,
    )
    assert result.should_ack is expected_ack


# --- Exact outcome -> HTTP status mapping (Group 1 requirement) ------------
#
# Explicit table, independent of enum declaration order or any "first N"
# slicing — this is the authoritative contract every ProcessingOutcome must
# satisfy at the HTTP layer:
_EXPECTED_HTTP_STATUS: dict[ProcessingOutcome, int] = {
    ProcessingOutcome.SUCCEEDED: 200,
    ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED: 200,
    ProcessingOutcome.CANCELLED: 200,
    ProcessingOutcome.RETRYABLE_FAILURE: 503,
    ProcessingOutcome.TERMINAL_INVALID_MESSAGE: 400,
}


def test_expected_http_status_table_covers_every_outcome() -> None:
    """Guards against a new ProcessingOutcome being added without also
    deciding its HTTP status — both here and in production code."""
    assert set(_EXPECTED_HTTP_STATUS.keys()) == set(ProcessingOutcome)


@pytest.mark.parametrize("outcome", list(ProcessingOutcome))
def test_outcome_http_status_constant_matches_expected_table(outcome: ProcessingOutcome) -> None:
    """Directly checks the production OUTCOME_HTTP_STATUS constant (the
    single source of truth `worker_endpoint.py` looks up) against the
    explicit table above — not enum ordering, not should_ack plus a guess."""
    from app.messaging.outcomes import OUTCOME_HTTP_STATUS

    assert OUTCOME_HTTP_STATUS[outcome] == _EXPECTED_HTTP_STATUS[outcome]


@pytest.mark.parametrize("outcome", list(ProcessingOutcome))
def test_endpoint_returns_exact_http_status_for_every_outcome(outcome: ProcessingOutcome) -> None:
    """End-to-end: POSTs a real envelope through /internal/pubsub/assurance
    with AssuranceWorker.process_job mocked to return each possible
    ProcessingResult outcome in turn, and asserts the exact HTTP status —
    not just "2xx" or ">=500"."""
    from app.messaging.outcomes import ProcessingResult

    job = AssuranceJob(job_id=f"JOB-MATRIX-{outcome.value}", run_id=f"MIS-MATRIX-{outcome.value}", job_type="ASSURANCE_MISSION_V2")
    b64_data = base64.b64encode(job.model_dump_json().encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": b64_data, "message_id": f"MSG-MATRIX-{outcome.value}"}}

    with patch("app.api.worker_endpoint.AssuranceWorker") as mock_worker_cls:
        mock_worker_cls.return_value.process_job.return_value = ProcessingResult(
            outcome=outcome, run_id=job.run_id, job_id=job.job_id,
        )
        res = client.post("/internal/pubsub/assurance", json=envelope)

    assert res.status_code == _EXPECTED_HTTP_STATUS[outcome]
    assert res.json()["status"] == outcome.value
