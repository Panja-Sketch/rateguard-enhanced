"""Tenant isolation (locked doc 4.1.A, acceptance scenario A12).

Cross-tenant records are indistinguishable from missing ones (404), lists never
include another tenant's rows, and pre-tenant legacy records are hidden from
everyone unless the server explicitly assigns them to a tenant."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.explanations.draft import deterministic_fallback_draft
from app.explanations.models import build_explanation_facts
from app.storage import (
    AssuranceRunRecord,
    AssuranceRunStatus,
    EvidenceRecord,
    EvidenceType,
    get_run_store,
)
from tests.auth.conftest import TENANT_A, TENANT_B, bearer

pytestmark = pytest.mark.real_auth


def _facts_and_draft():
    facts = build_explanation_facts(
        case_id="case-1",
        prior_premium=Decimal("700.00"),
        new_premium=Decimal("655.00"),
        factor_label="roof age band",
        effective_date=date(2026, 10, 15),
    )
    draft = deterministic_fallback_draft(facts)
    return facts.model_dump(mode="json"), draft.model_dump(mode="json")


def _seed_mission(run_id: str, tenant: str | None, with_explanation: bool = False) -> None:
    store = get_run_store()
    report: dict = {}
    if with_explanation:
        facts, draft = _facts_and_draft()
        report = {"explanation_facts": {"data": facts}, "explanation_draft": {"data": draft}}
    store.create_run(
        AssuranceRunRecord(
            run_id=run_id,
            tenant_id=tenant,
            created_by="seed",
            status=AssuranceRunStatus.COMPLETED,
            decision="BLOCK_DEPLOYMENT",
            report=report,
            metadata={"record_type": "ASSURANCE_MISSION_V2", "name": run_id, "mode": "RELEASE_CONFORMANCE"},
        )
    )
    store.add_evidence(
        run_id,
        EvidenceRecord(
            evidence_id=f"EV-{run_id}",
            run_id=run_id,
            evidence_type=EvidenceType.ASSURANCE_DECISION,
            title="decision",
            description="d",
        ),
    )


@pytest.fixture
def seeded(api):
    _seed_mission("MIS-TENANTA1", TENANT_A, with_explanation=True)
    _seed_mission("MIS-TENANTB1", TENANT_B, with_explanation=True)
    _seed_mission("MIS-LEGACY01", None)
    return None


# ---- missions --------------------------------------------------------------


def test_same_tenant_can_read_its_mission(api, seeded):
    assert api.get("/api/v1/missions/MIS-TENANTA1", headers=bearer("viewer-token")).status_code == 200


def test_cross_tenant_mission_read_is_404(api, seeded):
    r = api.get("/api/v1/missions/MIS-TENANTB1", headers=bearer("viewer-token"))
    assert r.status_code == 404
    missing = api.get("/api/v1/missions/MIS-DOESNOTEXIST", headers=bearer("viewer-token"))
    # Indistinguishable from a record that does not exist.
    assert r.json()["detail"].replace("MIS-TENANTB1", "X") == missing.json()["detail"].replace("MIS-DOESNOTEXIST", "X")


def test_mission_list_never_enumerates_other_tenants(api, seeded):
    a = api.get("/api/v1/missions?include_legacy=true", headers=bearer("viewer-token")).json()
    b = api.get("/api/v1/missions?include_legacy=true", headers=bearer("b-admin-token")).json()
    a_ids = {m["mission_id"] for m in a["missions"]}
    b_ids = {m["mission_id"] for m in b["missions"]}
    assert "MIS-TENANTA1" in a_ids and "MIS-TENANTB1" not in a_ids
    assert "MIS-TENANTB1" in b_ids and "MIS-TENANTA1" not in b_ids


@pytest.mark.parametrize(
    ("method", "suffix", "token"),
    [
        ("POST", "/cancel", "b-owner-token"),
        ("POST", "/retry", "b-owner-token"),
        ("POST", "/archive", "b-owner-token"),
        ("DELETE", "", "b-admin-token"),
        ("POST", "/alignment-options", "b-owner-token"),
    ],
)
def test_cross_tenant_mutations_are_404_and_change_nothing(api, seeded, method, suffix, token):
    kwargs = {"json": {"reference": "A"}} if suffix == "/alignment-options" else {}
    r = api.request(method, "/api/v1/missions/MIS-TENANTA1" + suffix, headers=bearer(token), **kwargs)
    assert r.status_code == 404
    rec = get_run_store().get_run("MIS-TENANTA1")
    assert rec is not None and rec.status == AssuranceRunStatus.COMPLETED


def test_cross_tenant_run_events_and_evidence_are_404(api, seeded):
    for suffix in ("events", "evidence"):
        assert api.get(f"/api/v1/assurance/runs/MIS-TENANTA1/{suffix}", headers=bearer("viewer-token")).status_code == 200
        assert api.get(f"/api/v1/assurance/runs/MIS-TENANTA1/{suffix}", headers=bearer("b-admin-token")).status_code == 404


# ---- evidence --------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["/evidence", "/connector-evidence", "/evidence/download", "/explanations"])
def test_cross_tenant_evidence_access_is_404(api, seeded, suffix):
    ok = api.get("/api/v1/missions/MIS-TENANTA1" + suffix, headers=bearer("owner-token"))
    assert ok.status_code == 200, ok.text
    denied = api.get("/api/v1/missions/MIS-TENANTA1" + suffix, headers=bearer("b-owner-token"))
    assert denied.status_code == 404


def test_evidence_bundle_download_contents_and_headers(api, seeded):
    r = api.get("/api/v1/missions/MIS-TENANTA1/evidence/download", headers=bearer("reviewer-token"))
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["tenant_id"] == TENANT_A and body["exported_by"] == "uid-reviewer"
    assert len(body["bundle_sha256"]) == 64
    assert [e["evidence_id"] for e in body["evidence"]] == ["EV-MIS-TENANTA1"]


def test_viewer_cannot_download_evidence_bundle_but_can_read_summary(api, seeded):
    assert api.get("/api/v1/missions/MIS-TENANTA1/evidence/download", headers=bearer("viewer-token")).status_code == 403
    assert api.get("/api/v1/missions/MIS-TENANTA1/evidence", headers=bearer("viewer-token")).status_code == 200


# ---- explanations ----------------------------------------------------------


def _create_explanation(api, mission_id="MIS-TENANTA1", token="owner-token") -> str:
    r = api.post(f"/api/v1/missions/{mission_id}/explanations", headers=bearer(token))
    assert r.status_code == 201, r.text
    return r.json()["explanation_id"]


def test_explanation_lifecycle_approve_records_reviewer_identity(api, seeded):
    eid = _create_explanation(api)
    created = api.get("/api/v1/missions/MIS-TENANTA1/explanations", headers=bearer("viewer-token")).json()
    assert created["count"] == 1 and created["explanations"][0]["status"] == "DRAFT"
    assert created["explanations"][0]["label"].startswith("For authorized review")

    r = api.post(f"/api/v1/explanations/{eid}/approve", headers=bearer("reviewer-token"))
    assert r.status_code == 200
    assert r.json()["status"] == "APPROVED" and r.json()["reviewed_by"] == "uid-reviewer"
    events = get_run_store().get_events("MIS-TENANTA1")
    assert any(e.stage == "EXPLANATION_REVIEW" and e.details.get("reviewer_uid") == "uid-reviewer" for e in events)
    # Cannot be reviewed twice.
    assert api.post(f"/api/v1/explanations/{eid}/reject", json={"reason": "changed my mind"}, headers=bearer("reviewer-token")).status_code == 409


def test_explanation_creation_is_idempotent_per_facts(api, seeded):
    assert _create_explanation(api) == _create_explanation(api)


def test_cross_tenant_explanation_approval_and_rejection_are_404(api, seeded):
    eid = _create_explanation(api)
    approve = api.post(f"/api/v1/explanations/{eid}/approve", headers=bearer("b-reviewer-token"))
    reject = api.post(f"/api/v1/explanations/{eid}/reject", json={"reason": "not mine"}, headers=bearer("b-admin-token"))
    assert approve.status_code == 404 and reject.status_code == 404
    still = api.get("/api/v1/missions/MIS-TENANTA1/explanations", headers=bearer("viewer-token")).json()
    assert still["explanations"][0]["status"] == "DRAFT"


def test_cross_tenant_explanation_creation_is_404(api, seeded):
    r = api.post("/api/v1/missions/MIS-TENANTA1/explanations", headers=bearer("b-owner-token"))
    assert r.status_code == 404


def test_release_owner_and_viewer_cannot_approve(api, seeded):
    eid = _create_explanation(api)
    assert api.post(f"/api/v1/explanations/{eid}/approve", headers=bearer("owner-token")).status_code == 403
    assert api.post(f"/api/v1/explanations/{eid}/approve", headers=bearer("viewer-token")).status_code == 403


def test_explanation_without_mismatch_returns_409(api, seeded):
    r = api.post("/api/v1/missions/MIS-LEGACY01/explanations", headers=bearer("owner-token"))
    assert r.status_code == 404  # legacy is hidden by default (see below)
    _seed_mission("MIS-CLEAN001", TENANT_A)
    r = api.post("/api/v1/missions/MIS-CLEAN001/explanations", headers=bearer("owner-token"))
    assert r.status_code == 409


def test_client_cannot_supply_explanation_text(api, seeded):
    r = api.post(
        "/api/v1/missions/MIS-TENANTA1/explanations",
        json={"draft_text": "Your premium fell by $9,999", "tenant_id": TENANT_B},
        headers=bearer("owner-token"),
    )
    assert r.status_code == 201
    assert "9,999" not in r.text and r.json()["facts"]["new_premium"] == "655.00"


# ---- sources ---------------------------------------------------------------


def _upload(api, token: str) -> str:
    r = api.post("/api/v1/sources", files={"file": ("x.json", b"{}", "application/json")}, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()["source_id"]


def test_cross_tenant_source_access_is_404(api, seeded):
    sid = _upload(api, "owner-token")
    assert api.get(f"/api/v1/sources/{sid}", headers=bearer("viewer-token")).status_code == 200
    assert api.get(f"/api/v1/sources/{sid}", headers=bearer("b-admin-token")).status_code == 404
    assert api.post(f"/api/v1/sources/{sid}/compile", headers=bearer("b-owner-token")).status_code == 404
    assert api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer("b-admin-token")).status_code == 404


def test_source_download_is_limited_to_release_roles_within_tenant(api, seeded):
    sid = _upload(api, "owner-token")
    ok = api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer("owner-token"))
    assert ok.status_code == 200 and ok.content == b"{}"
    assert ok.headers["x-content-type-options"] == "nosniff"
    for token in ("viewer-token", "reviewer-token"):
        assert api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer(token)).status_code == 403
    assert api.get(f"/api/v1/sources/{sid}/artifacts/OTHER-ARTIFACT", headers=bearer("owner-token")).status_code == 404


def test_source_metadata_never_exposes_storage_uri(api, seeded):
    sid = _upload(api, "owner-token")
    body = api.get(f"/api/v1/sources/{sid}", headers=bearer("viewer-token")).json()
    assert "storage_uri" not in body and body["tenant_id"] == TENANT_A


def test_mission_cannot_reference_another_tenants_uploaded_source(api, seeded, monkeypatch):
    monkeypatch.setattr("app.api.missions.get_message_publisher", lambda: type("P", (), {"publish_assurance_job": lambda s, j: None})())
    sid = _upload(api, "owner-token")
    body = {
        "source_a": {"source_id": sid, "source_type": "FILE", "name": "a"},
        "source_b": {"source_id": "AZ_HO3_2026_09_DEFECTIVE", "source_type": "SAMPLE_RELEASE", "name": "b"},
    }
    denied = api.post("/api/v1/missions", json=body, headers=bearer("b-owner-token"))
    assert denied.status_code == 422
    assert denied.json()["detail"]["issues"][0]["code"] == "SOURCE_NOT_FOUND"
    assert api.post("/api/v1/missions", json=body, headers=bearer("owner-token")).status_code == 202


# ---- legacy (tenantless) records ------------------------------------------


def test_legacy_records_are_hidden_from_every_tenant_by_default(api, seeded):
    for token in ("admin-token", "viewer-token", "b-admin-token"):
        assert api.get("/api/v1/missions/MIS-LEGACY01", headers=bearer(token)).status_code == 404
        ids = {m["mission_id"] for m in api.get("/api/v1/missions?include_legacy=true", headers=bearer(token)).json()["missions"]}
        assert "MIS-LEGACY01" not in ids


def test_explicit_legacy_assignment_exposes_legacy_records_to_that_tenant_only(api, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "legacy_record_tenant_id", TENANT_A)
    assert api.get("/api/v1/missions/MIS-LEGACY01", headers=bearer("viewer-token")).status_code == 200
    assert api.get("/api/v1/missions/MIS-LEGACY01", headers=bearer("b-admin-token")).status_code == 404
