from unittest.mock import patch

from app.storage import (
    AssuranceRunRecord,
    EvidenceRecord,
    EvidenceType,
    FirestoreRunStore,
    InMemoryRunStore,
    RunEvent,
)


def test_01_in_memory_run_store_lifecycle() -> None:
    store = InMemoryRunStore()

    run = AssuranceRunRecord(
        run_id="RUN-TEST-001",
        left_package_id="AZ_HO3_2026_09",
        right_package_id="AZ_HO3_2026_09_DEFECTIVE",
    )
    store.create_run(run)

    fetched = store.get_run("RUN-TEST-001")
    assert fetched is not None
    assert fetched.left_package_id == "AZ_HO3_2026_09"

    event = RunEvent(
        event_id="EVT-01",
        run_id="RUN-TEST-001",
        stage="TESTING",
        agent_name="TestAgent",
        action="TEST_ACTION",
    )
    store.add_event("RUN-TEST-001", event)

    events = store.get_events("RUN-TEST-001")
    assert len(events) == 1
    assert events[0].action == "TEST_ACTION"

    evidence = EvidenceRecord(
        evidence_id="EV-01",
        run_id="RUN-TEST-001",
        evidence_type=EvidenceType.SEMANTIC_DIFF,
        title="Test Evidence",
        description="Test description",
    )
    store.add_evidence("RUN-TEST-001", evidence)

    ev_list = store.get_evidence("RUN-TEST-001")
    assert len(ev_list) == 1
    assert ev_list[0].title == "Test Evidence"


def test_02_firestore_run_store_fallback_in_unit_test() -> None:
    """FirestoreRunStore falls back cleanly to InMemoryRunStore when Firestore is
    unavailable. This is forced deterministically (client construction mocked to
    fail) rather than relying on the test environment having no ambient GCP
    credentials — a dev machine with `gcloud auth application-default login`
    configured for the real 'rateguard-ai' project would otherwise cause this test
    to silently read/write against LIVE production Firestore."""
    with patch("google.cloud.firestore.Client", side_effect=RuntimeError("no credentials in unit test")):
        store = FirestoreRunStore(project_id="unit-test-fake-project", fallback_on_error=True)

    assert store._db is None

    run = AssuranceRunRecord(
        run_id="RUN-FS-001",
        left_package_id="AZ_HO3_2026_09",
        right_package_id="AZ_HO3_2026_09_DEFECTIVE",
    )
    store.create_run(run)

    fetched = store.get_run("RUN-FS-001")
    assert fetched is not None
    assert fetched.run_id == "RUN-FS-001"


def test_03_sanitize_for_firestore() -> None:
    from decimal import Decimal

    from app.ipir.enums import TransactionType
    from app.storage.firestore_store import sanitize_for_firestore

    nested_payload = {
        "decimal_val": Decimal("123.4567"),
        "enum_val": TransactionType.NEW_BUSINESS,
        "nested_dict": {
            "inner_decimal": Decimal("99.99"),
            "inner_enum": EvidenceType.PORTFOLIO_EXPOSURE,
        },
        "nested_list": [Decimal("1.00"), TransactionType.RENEWAL],
    }

    sanitized = sanitize_for_firestore(nested_payload)
    assert sanitized["decimal_val"] == "123.4567"
    assert sanitized["enum_val"] == "NEW_BUSINESS"
    assert sanitized["nested_dict"]["inner_decimal"] == "99.99"
    assert sanitized["nested_dict"]["inner_enum"] == "PORTFOLIO_EXPOSURE"
    assert sanitized["nested_list"] == ["1.00", "RENEWAL"]


def test_04_list_runs_sorting_and_limit() -> None:
    from datetime import UTC, datetime, timedelta

    store = InMemoryRunStore()

    now = datetime.now(UTC)
    run1 = AssuranceRunRecord(run_id="RUN-LIST-01", created_at=now - timedelta(seconds=10))
    run2 = AssuranceRunRecord(run_id="RUN-LIST-02", created_at=now - timedelta(seconds=5))
    run3 = AssuranceRunRecord(run_id="RUN-LIST-03", created_at=now)

    store.create_run(run1)
    store.create_run(run2)
    store.create_run(run3)

    listed = store.list_runs(limit=2)
    assert len(listed) == 2
    assert listed[0].run_id == "RUN-LIST-03"
    assert listed[1].run_id == "RUN-LIST-02"
