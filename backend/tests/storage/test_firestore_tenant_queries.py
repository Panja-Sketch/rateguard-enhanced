"""Database-level tenant isolation against the real Firestore emulator.

Every assertion here is about what Firestore itself returns/changes: another
tenant's documents are never read into the process, counted, updated or deleted."""

import uuid

import pytest

import app.storage as storage_module
from app.storage.firestore_store import FirestoreRunStore
from app.storage.interfaces import MAX_TENANT_LIST_RECORDS
from app.storage.models import AssuranceRunRecord, AssuranceRunStatus, EvidenceRecord, EvidenceType
from tests.auth.conftest import TENANT_A, TENANT_B, bearer

pytestmark = pytest.mark.usefixtures("firestore_emulator_db")


@pytest.fixture
def store(firestore_emulator_db) -> FirestoreRunStore:
    return FirestoreRunStore(
        project_id="demo-rateguard-tests",
        fallback_on_error=False,  # any Firestore failure must surface, never silently fall back
        collection_name=f"runs_{uuid.uuid4().hex[:10]}",
    )


def _run(rid: str, tenant: str | None, **kw) -> AssuranceRunRecord:
    return AssuranceRunRecord(run_id=rid, tenant_id=tenant, status=AssuranceRunStatus.COMPLETED, **kw)


def _seed(store):
    for i in range(3):
        store.create_run(_run(f"MIS-A{i}", TENANT_A))
    for i in range(4):
        store.create_run(_run(f"MIS-B{i}", TENANT_B))
    store.create_run(_run("MIS-LEG-NULL", None))  # explicit null tenant (legacy)
    # A legacy document that predates the field entirely.
    doc = store._db.collection(store.collection_name).document("MIS-LEG-MISSING")
    payload = _run("MIS-LEG-MISSING", None).model_dump(mode="json")
    payload.pop("tenant_id")
    doc.set(payload)


def test_list_returns_only_the_callers_tenant_and_never_parses_other_tenants_documents(store, monkeypatch):
    _seed(store)
    parsed_tenants: list = []
    original = AssuranceRunRecord.model_validate.__func__

    def spy(cls, data, *a, **k):
        if isinstance(data, dict) and "run_id" in data:
            parsed_tenants.append(data.get("tenant_id"))
        return original(cls, data, *a, **k)

    monkeypatch.setattr(AssuranceRunRecord, "model_validate", classmethod(spy))
    rows = store.list_runs_for_tenant(TENANT_A, limit=50)
    assert sorted(r.run_id for r in rows) == ["MIS-A0", "MIS-A1", "MIS-A2"]
    # The query itself excluded everyone else: no other tenant's (or legacy) document was even fetched.
    assert set(parsed_tenants) == {TENANT_A}
    assert len(rows) == 3  # counts never include other tenants


def test_list_is_newest_first_and_paginated(store):
    _seed(store)
    rows = store.list_runs_for_tenant(TENANT_B, limit=2)
    assert len(rows) == 2
    assert rows[0].created_at >= rows[1].created_at
    assert len(store.list_runs_for_tenant(TENANT_B, limit=100)) == 4


def test_list_limit_is_clamped_so_a_client_cannot_request_an_unbounded_scan(store):
    _seed(store)
    assert len(store.list_runs_for_tenant(TENANT_A, limit=10**9)) == 3
    assert MAX_TENANT_LIST_RECORDS <= 1000
    assert len(store.list_runs_for_tenant(TENANT_A, limit=0)) == 1  # floor of 1


def test_unknown_tenant_gets_nothing(store):
    _seed(store)
    assert store.list_runs_for_tenant("nobody", limit=50) == []
    assert store.list_runs_for_tenant("", limit=50) == []


def test_read_by_id_is_tenant_checked(store):
    _seed(store)
    assert store.get_run_for_tenant("MIS-A0", TENANT_A).run_id == "MIS-A0"
    assert store.get_run_for_tenant("MIS-A0", TENANT_B) is None
    assert store.get_run_for_tenant("MIS-NOPE", TENANT_B) is None  # indistinguishable from missing


def test_cross_tenant_update_is_rejected_and_changes_nothing(store):
    _seed(store)
    hostile = store.get_run("MIS-A0")
    hostile.summary = "TAMPERED"
    hostile.status = AssuranceRunStatus.FAILED
    assert store.update_run_for_tenant(hostile, TENANT_B) is None
    stored = store.get_run("MIS-A0")
    assert stored.summary is None and stored.status == AssuranceRunStatus.COMPLETED


def test_owner_update_succeeds_and_tenant_is_immutable(store):
    _seed(store)
    rec = store.get_run("MIS-A0")
    rec.summary = "legit"
    rec.tenant_id = TENANT_B  # attempt to move the record to another tenant
    saved = store.update_run_for_tenant(rec, TENANT_A)
    assert saved is not None
    stored = store.get_run("MIS-A0")
    assert stored.summary == "legit" and stored.tenant_id == TENANT_A
    assert store.get_run_for_tenant("MIS-A0", TENANT_B) is None


def test_cross_tenant_delete_is_rejected_and_leaves_document_and_subcollections_intact(store):
    _seed(store)
    store.log_event("MIS-A0", "X", "event")
    store.add_evidence(
        "MIS-A0",
        EvidenceRecord(evidence_id="EV-1", run_id="MIS-A0", evidence_type=EvidenceType.SOURCE, title="t", description="d"),
    )
    assert store.delete_run_for_tenant("MIS-A0", TENANT_B) is False
    assert store.get_run("MIS-A0") is not None
    assert len(store.get_events("MIS-A0")) == 1
    assert len(store.get_evidence("MIS-A0")) == 1


def test_owner_delete_removes_document_and_subcollections(store):
    _seed(store)
    store.log_event("MIS-A1", "X", "event")
    store.save_explanation("MIS-A1", {"explanation_id": "EXP-1", "tenant_id": TENANT_A})
    assert store.delete_run_for_tenant("MIS-A1", TENANT_A) is True
    assert store.get_run("MIS-A1") is None
    doc = store._db.collection(store.collection_name).document("MIS-A1")
    for sub in ("events", "evidence", "explanations"):
        assert list(doc.collection(sub).stream()) == []
    assert store.delete_run_for_tenant("MIS-A1", TENANT_A) is False  # already gone
    assert len(store.list_runs_for_tenant(TENANT_B)) == 4  # other tenant untouched


def test_legacy_null_tenant_records_hidden_unless_explicitly_included(store):
    _seed(store)
    assert "MIS-LEG-NULL" not in {r.run_id for r in store.list_runs_for_tenant(TENANT_A)}
    with_legacy = {r.run_id for r in store.list_runs_for_tenant(TENANT_A, include_legacy=True)}
    assert {"MIS-LEG-NULL", "MIS-A0"} <= with_legacy
    assert not any(rid.startswith("MIS-B") for rid in with_legacy)
    # Direct read: hidden by default, readable only under the explicit assignment.
    assert store.get_run_for_tenant("MIS-LEG-NULL", TENANT_B) is None
    assert store.get_run_for_tenant("MIS-LEG-NULL", TENANT_A) is None
    assert store.get_run_for_tenant("MIS-LEG-NULL", TENANT_A, include_legacy=True) is not None


def test_documents_missing_the_tenant_field_are_never_listed_but_are_readable_by_id_under_legacy_assignment(store):
    _seed(store)
    assert "MIS-LEG-MISSING" not in {r.run_id for r in store.list_runs_for_tenant(TENANT_A, include_legacy=True)}
    assert store.get_run_for_tenant("MIS-LEG-MISSING", TENANT_A) is None
    assert store.get_run_for_tenant("MIS-LEG-MISSING", TENANT_A, include_legacy=True) is not None


def test_explanation_queries_are_tenant_filtered_in_the_database(store):
    store.save_explanation("MIS-X", {"explanation_id": "EXP-1", "tenant_id": TENANT_A})
    store.save_explanation("MIS-X", {"explanation_id": "EXP-2", "tenant_id": TENANT_B})
    assert [e["explanation_id"] for e in store.list_explanations("MIS-X", TENANT_A)] == ["EXP-1"]
    assert [e["explanation_id"] for e in store.list_explanations("MIS-X", TENANT_B)] == ["EXP-2"]
    assert store.list_explanations("MIS-X", "nobody") == []


def test_transactional_update_rejects_a_record_that_changed_owner_between_read_and_write(store):
    """The ownership check runs inside the transaction against the *stored*
    document, not against whatever the caller's in-memory copy claims."""
    _seed(store)
    rec = store.get_run("MIS-A2")
    # Caller B forges a copy that claims to be tenant B's record.
    forged = rec.model_copy(update={"tenant_id": TENANT_B, "summary": "forged"})
    assert store.update_run_for_tenant(forged, TENANT_B) is None
    assert store.get_run("MIS-A2").summary is None


# ---- through the HTTP API, backed by the emulator ---------------------------


@pytest.fixture
def api_on_firestore(store, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(storage_module, "_global_run_store", store)
    with TestClient(app) as c:
        yield c


@pytest.mark.real_auth
@pytest.mark.usefixtures("auth_env")
def test_api_list_get_cancel_and_delete_are_tenant_scoped_on_firestore(api_on_firestore, store):
    api = api_on_firestore
    store.create_run(_run("MIS-APIA1", TENANT_A, metadata={"record_type": "ASSURANCE_MISSION_V2", "name": "a"}))
    store.create_run(_run("MIS-APIB1", TENANT_B, metadata={"record_type": "ASSURANCE_MISSION_V2", "name": "b"}))
    store.create_run(_run("MIS-APIDRAFT", TENANT_A, metadata={"record_type": "ASSURANCE_MISSION_V2"}))

    a_list = api.get("/api/v1/missions", headers=bearer("viewer-token")).json()
    assert {m["mission_id"] for m in a_list["missions"]} == {"MIS-APIA1", "MIS-APIDRAFT"}
    assert a_list["total_count"] == 2  # never counts tenant B's record
    assert api.get("/api/v1/missions/MIS-APIB1", headers=bearer("viewer-token")).status_code == 404
    assert api.post("/api/v1/missions/MIS-APIA1/cancel", headers=bearer("b-owner-token")).status_code == 404
    assert api.delete("/api/v1/missions/MIS-APIA1", headers=bearer("b-admin-token")).status_code == 404
    assert store.get_run("MIS-APIA1") is not None
    assert store.get_run("MIS-APIA1").status == AssuranceRunStatus.COMPLETED
