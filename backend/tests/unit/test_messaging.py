from unittest.mock import MagicMock, patch

from app.messaging import (
    AssuranceJob,
    AssuranceWorker,
    InMemoryPublisher,
    PubSubPublisher,
    get_message_publisher,
)
from app.messaging.outcomes import ProcessingOutcome, ProcessingResult
from app.storage import AssuranceRunRecord, AssuranceRunStatus, InMemoryRunStore


def test_assurance_job_serialization():
    """Tests AssuranceJob serialization and deserialization."""
    job = AssuranceJob(
        job_id="JOB-12345",
        run_id="RUN-99999",
        left_package_id="AZ_HO3_2026_09",
        right_package_id="AZ_HO3_2026_09_DEFECTIVE",
    )

    json_data = job.model_dump_json()
    deserialized = AssuranceJob.model_validate_json(json_data)

    assert deserialized.job_id == "JOB-12345"
    assert deserialized.run_id == "RUN-99999"
    assert deserialized.left_package_id == "AZ_HO3_2026_09"


def test_in_memory_publisher():
    """Tests InMemoryPublisher publishing and tracking."""
    pub = InMemoryPublisher()
    job = AssuranceJob(job_id="JOB-01", run_id="RUN-01")

    msg_id = pub.publish_assurance_job(job)
    assert msg_id.startswith("MSG-MEM-")
    assert len(pub.published_jobs) == 1
    assert pub.published_jobs[0][1].job_id == "JOB-01"


def test_pubsub_publisher_mocked():
    """Tests PubSubPublisher using a mocked Pub/Sub client."""
    mock_client = MagicMock()
    mock_future = MagicMock()
    mock_future.result.return_value = "1234567890"
    mock_client.publish.return_value = mock_future

    pub = PubSubPublisher(publisher_client=mock_client)
    job = AssuranceJob(job_id="JOB-02", run_id="RUN-02")

    msg_id = pub.publish_assurance_job(job)
    assert msg_id == "1234567890"
    assert mock_client.publish.called


def test_worker_rejects_non_mission_v2_run_id():
    """The pre-V2 'RUN-*' agentic pipeline has been retired -- no code path
    can create a new 'RUN-*' job any more, so an unrecognized non-terminal
    'RUN-*' id is a poison message, not silently dropped or guessed at."""
    store = InMemoryRunStore()
    worker = AssuranceWorker(run_store=store)

    job = AssuranceJob(job_id="JOB-LEGACY-1", run_id="RUN-LEGACY-1")

    res = worker.process_job(job)

    assert res.outcome == ProcessingOutcome.TERMINAL_INVALID_MESSAGE
    assert res.should_ack is False


def test_worker_acks_stale_redelivery_of_completed_legacy_run():
    """A Pub/Sub redelivery of an already-terminal historical 'RUN-*' record
    (from before the legacy pipeline was retired) must be acked as a
    harmless duplicate, not treated as poison -- poison messages are never
    acked, which would leave a genuinely-finished old run retrying forever."""
    store = InMemoryRunStore()
    store.save_run(AssuranceRunRecord(run_id="RUN-OLD-COMPLETED", status=AssuranceRunStatus.COMPLETED))
    worker = AssuranceWorker(run_store=store)

    job = AssuranceJob(job_id="JOB-REDELIVERED", run_id="RUN-OLD-COMPLETED")

    res = worker.process_job(job)

    assert res.outcome == ProcessingOutcome.DUPLICATE_ALREADY_PROCESSED
    assert res.should_ack is True


def test_worker_rejects_mission_v2_id_with_mismatched_job_type():
    """A MIS-* run_id with a job_type other than 'ASSURANCE_MISSION_V2' is a genuine
    bug and must fail with a structured error — never silently dispatched to either
    pipeline."""
    store = InMemoryRunStore()
    store.save_run(AssuranceRunRecord(run_id="MIS-MISMATCH-1", status=AssuranceRunStatus.QUEUED))
    worker = AssuranceWorker(run_store=store)

    job = AssuranceJob(job_id="JOB-MISMATCH-1", run_id="MIS-MISMATCH-1", job_type="SOME_OTHER_TYPE")

    res = worker.process_job(job)

    assert res.outcome == ProcessingOutcome.TERMINAL_INVALID_MESSAGE
    assert res.should_ack is False
    assert "JOB_ROUTING_MISMATCH" in res.detail


def test_worker_dispatches_mission_v2_id_with_matching_job_type():
    store = InMemoryRunStore()
    store.save_run(AssuranceRunRecord(run_id="MIS-MATCH-1", status=AssuranceRunStatus.QUEUED))
    worker = AssuranceWorker(run_store=store)

    job = AssuranceJob(job_id="JOB-MATCH-1", run_id="MIS-MATCH-1", job_type="ASSURANCE_MISSION_V2")

    with patch("app.services.mission_execution_service.MissionExecutionService.execute_job") as mock_exec:
        mock_exec.return_value = ProcessingResult(
            outcome=ProcessingOutcome.SUCCEEDED, run_id="MIS-MATCH-1",
        )
        res = worker.process_job(job)

    mock_exec.assert_called_once_with(job)
    assert res.outcome == ProcessingOutcome.SUCCEEDED


def test_factory_returns_memory_publisher_when_local():
    """Verifies factory returns InMemoryPublisher when execution_mode is local or async is disabled."""
    with patch("app.messaging.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.execution_mode = "local"
        mock_settings.async_enabled = False
        mock_get_settings.return_value = mock_settings

        pub = get_message_publisher()
        assert isinstance(pub, InMemoryPublisher)
