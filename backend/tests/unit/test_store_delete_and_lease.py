"""Tests for BaseRunStore.delete_run and BaseRunStore.acquire_lease across both the
InMemoryRunStore and FirestoreRunStore adapters: real deletion confirmation, and
duplicate-Pub/Sub-delivery / concurrent-worker lease protection."""

from unittest.mock import MagicMock, patch

from app.storage.firestore_store import FirestoreRunStore
from app.storage.interfaces import LeaseOutcome
from app.storage.memory_store import InMemoryRunStore
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus

# ---------------------------------------------------------------------------
# InMemoryRunStore.delete_run
# ---------------------------------------------------------------------------


def test_memory_store_delete_run_removes_record_and_confirms():
    store = InMemoryRunStore()
    store.create_run(AssuranceRunRecord(run_id="MIS-DEL-01", status=AssuranceRunStatus.DRAFT))

    deleted = store.delete_run("MIS-DEL-01")

    assert deleted is True
    assert store.get_run("MIS-DEL-01") is None


def test_memory_store_delete_run_returns_false_when_missing():
    store = InMemoryRunStore()
    assert store.delete_run("MIS-DOES-NOT-EXIST") is False


def test_memory_store_delete_run_also_removes_events_and_evidence():
    from app.storage.models import EvidenceRecord, EvidenceType, RunEvent

    store = InMemoryRunStore()
    store.create_run(AssuranceRunRecord(run_id="MIS-DEL-02", status=AssuranceRunStatus.CANCELLED))
    store.add_event(
        "MIS-DEL-02",
        RunEvent(event_id="EVT-1", run_id="MIS-DEL-02", stage="QUEUED", action="test"),
    )
    store.add_evidence(
        "MIS-DEL-02",
        EvidenceRecord(
            evidence_id="EV-1",
            run_id="MIS-DEL-02",
            evidence_type=EvidenceType.SOURCE,
            title="t",
            description="d",
        ),
    )

    assert store.delete_run("MIS-DEL-02") is True
    assert store.get_events("MIS-DEL-02") == []
    assert store.get_evidence("MIS-DEL-02") == []


# ---------------------------------------------------------------------------
# InMemoryRunStore.acquire_lease (duplicate delivery / concurrent worker race)
# ---------------------------------------------------------------------------


def test_memory_store_lease_acquired_then_duplicate_delivery_is_blocked():
    store = InMemoryRunStore()
    store.create_run(AssuranceRunRecord(run_id="MIS-LEASE-01", status=AssuranceRunStatus.QUEUED))

    outcome1, rec1 = store.acquire_lease("MIS-LEASE-01", "JOB-A")
    assert outcome1 == LeaseOutcome.ACQUIRED
    assert rec1.status == AssuranceRunStatus.RUNNING

    # Simulated duplicate Pub/Sub delivery / second worker racing on the same mission.
    outcome2, rec2 = store.acquire_lease("MIS-LEASE-01", "JOB-B")
    assert outcome2 == LeaseOutcome.ALREADY_LEASED
    # Lease owner must remain the first job, not overwritten by the duplicate.
    assert rec2.metadata["lease_owner"] == "JOB-A"


def test_memory_store_lease_not_found():
    store = InMemoryRunStore()
    outcome, rec = store.acquire_lease("MIS-NOPE", "JOB-A")
    assert outcome == LeaseOutcome.NOT_FOUND
    assert rec is None


def test_memory_store_lease_already_terminal():
    store = InMemoryRunStore()
    store.create_run(AssuranceRunRecord(run_id="MIS-LEASE-02", status=AssuranceRunStatus.COMPLETED))
    outcome, rec = store.acquire_lease("MIS-LEASE-02", "JOB-A")
    assert outcome == LeaseOutcome.ALREADY_TERMINAL
    assert rec.status == AssuranceRunStatus.COMPLETED


def test_memory_store_lease_expired_heartbeat_can_be_reacquired():
    from datetime import UTC, datetime, timedelta

    store = InMemoryRunStore()
    stale_heartbeat = (datetime.now(UTC) - timedelta(seconds=999)).isoformat()
    store.create_run(
        AssuranceRunRecord(
            run_id="MIS-LEASE-03",
            status=AssuranceRunStatus.RUNNING,
            metadata={"last_heartbeat_at": stale_heartbeat, "lease_owner": "JOB-STALE"},
        )
    )

    outcome, rec = store.acquire_lease("MIS-LEASE-03", "JOB-NEW", lease_seconds=120)
    assert outcome == LeaseOutcome.ACQUIRED
    assert rec.metadata["lease_owner"] == "JOB-NEW"


# ---------------------------------------------------------------------------
# FirestoreRunStore.delete_run (mocked Firestore client)
# ---------------------------------------------------------------------------


def _firestore_store_with_mocked_client() -> tuple[FirestoreRunStore, MagicMock]:
    """Never constructs a real google.cloud.firestore.Client — defense in depth
    against accidentally touching live GCP resources on a machine with real
    Application Default Credentials configured."""
    with patch("google.cloud.firestore.Client", side_effect=RuntimeError("no credentials in unit test")):
        store = FirestoreRunStore(project_id="test-project", fallback_on_error=True)
    mock_db = MagicMock()
    store._db = mock_db
    return store, mock_db


def test_firestore_store_delete_run_confirms_deletion():
    store, mock_db = _firestore_store_with_mocked_client()

    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.collection.return_value.stream.return_value = []

    # First .get() (existence check) -> exists; second .get() (post-delete confirm) -> gone.
    existing_snapshot = MagicMock(exists=True)
    deleted_snapshot = MagicMock(exists=False)
    mock_doc_ref.get.side_effect = [existing_snapshot, deleted_snapshot]

    result = store.delete_run("MIS-FS-DEL-01")

    assert result is True
    mock_doc_ref.delete.assert_called_once()


def test_firestore_store_delete_run_returns_false_when_not_found():
    store, mock_db = _firestore_store_with_mocked_client()
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.get.return_value = MagicMock(exists=False)

    assert store.delete_run("MIS-FS-DEL-MISSING") is False
    mock_doc_ref.delete.assert_not_called()


def test_firestore_store_delete_run_never_reports_success_without_confirmation():
    """If Firestore's post-delete read still shows the document exists (eventual
    consistency / delete failure), delete_run must report False, not True."""
    store, mock_db = _firestore_store_with_mocked_client()
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.collection.return_value.stream.return_value = []

    still_exists_snapshot = MagicMock(exists=True)
    mock_doc_ref.get.side_effect = [still_exists_snapshot, still_exists_snapshot]

    result = store.delete_run("MIS-FS-DEL-02")

    assert result is False


def test_firestore_store_delete_run_falls_back_when_db_unavailable():
    """Forces the offline/no-Firestore-client path deterministically — a dev machine
    with real GCP Application Default Credentials configured (e.g. for the actual
    'rateguard-ai' project) would otherwise construct a genuine client here and risk
    a real network call, rather than exercising the fallback path this test targets."""
    with patch("google.cloud.firestore.Client", side_effect=RuntimeError("no credentials in unit test")):
        store = FirestoreRunStore(project_id="test-project", fallback_on_error=True)
    assert store._db is None
    store._fallback_store.create_run(AssuranceRunRecord(run_id="MIS-FS-FALLBACK", status=AssuranceRunStatus.DRAFT))

    assert store.delete_run("MIS-FS-FALLBACK") is True
    assert store._fallback_store.get_run("MIS-FS-FALLBACK") is None


# ---------------------------------------------------------------------------
# FirestoreRunStore.acquire_lease (mocked transactional path)
# ---------------------------------------------------------------------------


def test_firestore_store_acquire_lease_transactional_flow():
    """Exercises our business logic inside the @firestore.transactional-wrapped
    function (status/heartbeat checks, record mutation) with the SDK's retry
    machinery replaced by a pass-through, since testing the SDK's own commit/retry
    protocol is out of scope here."""
    store, mock_db = _firestore_store_with_mocked_client()

    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    record = AssuranceRunRecord(run_id="MIS-FS-LEASE-01", status=AssuranceRunStatus.QUEUED)
    mock_doc_ref.get.return_value = MagicMock(exists=True, to_dict=lambda: record.model_dump(mode="json"))

    mock_transaction = MagicMock()
    mock_db.transaction.return_value = mock_transaction

    with patch("google.cloud.firestore.transactional", lambda fn: fn):
        outcome, rec = store.acquire_lease("MIS-FS-LEASE-01", "JOB-FS-A")

    assert outcome == LeaseOutcome.ACQUIRED
    assert rec.status == AssuranceRunStatus.RUNNING
    mock_transaction.set.assert_called_once()


def test_firestore_store_acquire_lease_falls_back_when_db_unavailable():
    """See test_firestore_store_delete_run_falls_back_when_db_unavailable: forces the
    offline path deterministically instead of depending on absent ambient credentials."""
    with patch("google.cloud.firestore.Client", side_effect=RuntimeError("no credentials in unit test")):
        store = FirestoreRunStore(project_id="test-project", fallback_on_error=True)
    assert store._db is None
    store._fallback_store.create_run(AssuranceRunRecord(run_id="MIS-FS-LEASE-FB", status=AssuranceRunStatus.QUEUED))

    outcome, rec = store.acquire_lease("MIS-FS-LEASE-FB", "JOB-FB")

    assert outcome == LeaseOutcome.ACQUIRED
    assert rec.status == AssuranceRunStatus.RUNNING
