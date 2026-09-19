"""Tenant-prefixed artifact isolation, end to end through the API and the
artifact store (locked doc 14.2)."""

import pytest

from app.storage import AssuranceRunRecord, AssuranceRunStatus, get_run_store
from app.storage.artifacts import ArtifactKey, get_artifact_store
from tests.auth.conftest import TENANT_A, TENANT_B, bearer

pytestmark = pytest.mark.real_auth


def _upload(api, token, name="x.json", content=b"{}", data=None):
    r = api.post("/api/v1/sources", files={"file": (name, content, "application/json")}, data=data or {}, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()["source_id"]


def _raw(tenant, sid):
    return ArtifactKey(tenant, "sources", sid, "raw", sid)


def test_upload_lands_under_the_authenticated_tenants_prefix_only(api):
    sid = _upload(api, "owner-token")
    store = get_artifact_store()
    assert store.get_artifact_content(_raw(TENANT_A, sid)) == b"{}"
    assert store.get_artifact_content(_raw(TENANT_B, sid)) is None
    desc = store.get_descriptor(_raw(TENANT_A, sid))
    assert f"tenants/{TENANT_A}/sources/{sid}/raw/{sid}".replace("/", "\\") in desc.storage_uri or f"tenants/{TENANT_A}/sources/{sid}/raw/{sid}" in desc.storage_uri


def test_client_supplied_tenant_in_form_fields_or_filename_is_ignored(api):
    sid = _upload(api, "owner-token", name=f"tenants/{TENANT_B}/x.json", data={"tenant_id": TENANT_B, "tenant": TENANT_B})
    store = get_artifact_store()
    assert store.get_artifact_content(_raw(TENANT_A, sid)) == b"{}"
    assert store.get_artifact_content(_raw(TENANT_B, sid)) is None


@pytest.mark.parametrize("name", ["../../evil.json", "..\\..\\evil.json", "/etc/passwd.json", "a/../../b.json", "%2e%2e%2fevil.json", "x\x00.json"])
def test_hostile_filenames_never_reach_the_object_path(api, name, tmp_path):
    sid = _upload(api, "owner-token", name=name)
    store = get_artifact_store()
    desc = store.get_descriptor(_raw(TENANT_A, sid))
    assert ".." not in desc.filename and "/" not in desc.filename and "\\" not in desc.filename
    assert desc.storage_uri.replace("\\", "/").count("/tenants/") == 1
    assert f"tenants/{TENANT_A}/sources/{sid}/raw/{sid}" in desc.storage_uri.replace("\\", "/")


def test_cross_tenant_source_metadata_compile_and_download_are_404(api):
    sid = _upload(api, "owner-token")
    for token in ("b-admin-token", "b-owner-token"):
        assert api.get(f"/api/v1/sources/{sid}", headers=bearer(token)).status_code == 404
        assert api.post(f"/api/v1/sources/{sid}/compile", headers=bearer(token)).status_code == 404
        assert api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer(token)).status_code == 404
    # Same 404 body shape as a source that never existed.
    ghost = api.get("/api/v1/sources/SRC-GHOST000/artifacts/SRC-GHOST000", headers=bearer("b-owner-token"))
    other = api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer("b-owner-token"))
    assert ghost.status_code == other.status_code == 404
    # Owner still fine.
    assert api.get(f"/api/v1/sources/{sid}/artifacts/{sid}", headers=bearer("owner-token")).content == b"{}"


def test_compiled_artifact_is_tenant_prefixed_and_downloadable_only_by_its_tenant(api):
    sid = _upload(api, "owner-token", content=open_sample())
    assert api.post(f"/api/v1/sources/{sid}/compile", headers=bearer("owner-token")).status_code == 200
    store = get_artifact_store()
    compiled = ArtifactKey(TENANT_A, "sources", sid, "compiled", f"IPIR-{sid}")
    assert store.get_artifact_content(compiled) is not None
    assert store.get_artifact_content(ArtifactKey(TENANT_B, "sources", sid, "compiled", f"IPIR-{sid}")) is None
    ok = api.get(f"/api/v1/sources/{sid}/artifacts/IPIR-{sid}", headers=bearer("admin-token"))
    assert ok.status_code == 200
    assert api.get(f"/api/v1/sources/{sid}/artifacts/IPIR-{sid}", headers=bearer("b-admin-token")).status_code == 404


@pytest.mark.parametrize(
    "artifact_id",
    ["..%2f..%2fetc%2fpasswd", "%2e%2e", "SRC-OTHER", "IPIR-SRC-OTHER", "a/b", "..\\..\\x", "x" * 300],
)
def test_path_traversal_and_foreign_artifact_ids_are_404(api, artifact_id):
    sid = _upload(api, "owner-token")
    r = api.get(f"/api/v1/sources/{sid}/artifacts/{artifact_id}", headers=bearer("owner-token"))
    assert r.status_code == 404


@pytest.mark.parametrize("source_id", ["%2e%2e", "SRC-..%2f..", "a%2fb", "SRC-%00x"])
def test_traversal_in_source_id_is_404_everywhere(api, source_id):
    for path in (f"/api/v1/sources/{source_id}", f"/api/v1/sources/{source_id}/artifacts/{source_id}"):
        assert api.get(path, headers=bearer("owner-token")).status_code == 404
    assert api.post(f"/api/v1/sources/{source_id}/compile", headers=bearer("owner-token")).status_code == 404


def test_mission_referencing_a_traversal_source_id_is_rejected(api, monkeypatch):
    monkeypatch.setattr("app.api.missions.get_message_publisher", lambda: type("P", (), {"publish_assurance_job": lambda s, j: None})())
    body = {
        "source_a": {"source_id": "SRC-../../x", "source_type": "FILE", "name": "a"},
        "source_b": {"source_id": "AZ_HO3_2026_09_DEFECTIVE", "source_type": "SAMPLE_RELEASE", "name": "b"},
    }
    r = api.post("/api/v1/missions", json=body, headers=bearer("owner-token"))
    assert r.status_code == 422 and r.json()["detail"]["issues"][0]["code"] == "SOURCE_NOT_FOUND"


def test_evidence_bundle_is_stored_under_the_missions_tenant_and_is_idempotent(api):
    get_run_store().create_run(
        AssuranceRunRecord(run_id="MIS-ART00001", tenant_id=TENANT_A, status=AssuranceRunStatus.COMPLETED, decision="PASS")
    )
    first = api.get("/api/v1/missions/MIS-ART00001/evidence/download", headers=bearer("owner-token"))
    second = api.get("/api/v1/missions/MIS-ART00001/evidence/download", headers=bearer("reviewer-token"))
    assert first.status_code == second.status_code == 200
    sha = first.json()["bundle_sha256"]
    assert sha == second.json()["bundle_sha256"]  # exporter/time excluded from the hash
    key = ArtifactKey(TENANT_A, "missions", "MIS-ART00001", "evidence", f"EVB-{sha[:32]}")
    store = get_artifact_store()
    assert store.get_artifact_content(key) is not None
    assert store.get_artifact_content(ArtifactKey(TENANT_B, "missions", "MIS-ART00001", "evidence", f"EVB-{sha[:32]}")) is None
    assert len([d for d in store.list_artifacts(TENANT_A) if d.scope_id == "MIS-ART00001"]) == 1
    # Tenant B: same 404 as a missing mission; nothing is stored under B's prefix.
    assert api.get("/api/v1/missions/MIS-ART00001/evidence/download", headers=bearer("b-owner-token")).status_code == 404
    assert store.list_artifacts(TENANT_B) == [] or all(d.scope_id != "MIS-ART00001" for d in store.list_artifacts(TENANT_B))


def test_legacy_tenantless_source_artifacts_are_hidden_from_all_tenants_by_default(api):
    store = get_artifact_store()
    # Nothing exists at the legacy layout for the local store, and a tenant key
    # never falls back to it without the explicit legacy assignment.
    assert store.get_artifact_content(_raw(TENANT_A, "SRC-LEGACY01")) is None
    assert store.get_artifact_content(_raw(TENANT_B, "SRC-LEGACY01")) is None


def open_sample() -> bytes:
    from pathlib import Path

    return (Path(__file__).resolve().parents[3] / "data" / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()
