"""API-level proof of connector-backed impact through the real mission path,
the impact endpoint, and the evidence bundle (integrity + exclusions)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.services.evidence_bundle import (
    EvidenceBundleError,
    build_bundle,
    normalize_reason_codes,
    scan_for_forbidden,
)
from tests.integration.test_workbook_to_connector_mission_e2e import (
    _canonical_bytes,
    _run_mission,
    _upload_and_compile,
    client,
)


@pytest.fixture()
def clean_mission():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    return _run_mission(source_id, package_id, "canonical-v1")


@pytest.fixture()
def defective_mission():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    return _run_mission(source_id, package_id, "defective-v1")


def test_clean_mission_has_complete_impact_and_passes(clean_mission):
    result = clean_mission["result"]
    impact = result["connector_impact"]["data"]
    assert impact["status"] == "COMPLETE" and impact["impact_decision"] == "PASS_ELIGIBLE"
    assert impact["mismatches"] == 0 and impact["inconclusive"] == 0 and impact["coverage_pct"] == 100.0
    assert result["release_decision"]["data"]["status"] == "PASS"
    assert result["blast_radius"]["data"]["financially_affected_count"] == 0
    assert result["cohort_distribution"]["status"] == "SUCCEEDED"


def test_defective_mission_blocks_with_exposure_cohorts_and_pipeline(defective_mission):
    result = defective_mission["result"]
    impact = result["connector_impact"]["data"]
    assert result["release_decision"]["data"]["status"] == "BLOCK_DEPLOYMENT"
    assert impact["mismatches"] > 0 and impact["undercharge_count"] == impact["mismatches"]
    assert float(impact["absolute_exposure"]) > 0 and not impact["exposure_is_lower_bound"]
    assert impact["cohort_distribution"]["cohorts"] and impact["pipeline_impact"]["buckets"]
    assert result["blast_radius"]["data"]["absolute_financial_exposure"] == impact["absolute_exposure"]


def test_impact_endpoint_reports_final_aggregate_and_no_rows(defective_mission):
    body = client.get(f"/api/v1/missions/{defective_mission['mission_id']}/impact").json()
    assert body["status"] == "COMPLETE" and body["aggregate"]["mismatches"] > 0
    assert body["progress"]["batches_done"] == body["progress"]["batches_total"]
    text = json.dumps(body)
    assert "policy_id" not in text and "AZ-HO3-" not in text


def test_impact_endpoint_unknown_mission_is_404():
    assert client.get("/api/v1/missions/MIS-DOESNOTEXIST/impact").status_code == 404


def test_evidence_bundle_manifest_hashes_and_exclusions(defective_mission):
    mission_id = defective_mission["mission_id"]
    r = client.get(f"/api/v1/missions/{mission_id}/evidence/bundle")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "evidence-bundle-v1"
    for entry in manifest["files"]:
        assert hashlib.sha256(zf.read(entry["path"])).hexdigest() == entry["sha256"]
    assert hashlib.sha256(zf.read("manifest.json")).hexdigest() == r.headers["X-Manifest-SHA256"]
    assert {"impact.json", "decision.json", "inputs.json", "connector.json", "probes.json", "summary.md",
            "limitations.json", "model_invocations.json", "explanations.json"} <= set(zf.namelist())
    everything = b"".join(zf.read(n) for n in zf.namelist()).decode()
    for needle in ("Bearer ", "PRIVATE KEY", "base_url", "127.0.0.1", "policy_id", "@example.com"):
        assert needle not in everything
    impact = json.loads(zf.read("impact.json"))
    assert impact["status"] == "COMPLETE" and impact["mismatches"] > 0


def test_bundle_is_deterministic_apart_from_export_time(defective_mission):
    mission_id = defective_mission["mission_id"]
    a = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/missions/{mission_id}/evidence/bundle").content))
    b = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/missions/{mission_id}/evidence/bundle").content))
    for name in a.namelist():
        if name not in ("manifest.json", "manifest.sha256"):
            assert a.read(name) == b.read(name)


def test_bundle_export_fails_closed_on_secret_or_pii():
    for bad in (
        {"x.json": {"note": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"}},
        {"x.json": {"api_key": "v"}},
        {"x.json": {"contact": "someone@example.org"}},
        {"x.json": {"k": "-----BEGIN PRIVATE KEY-----"}},  # pragma: allowlist secret
    ):
        with pytest.raises(EvidenceBundleError):
            scan_for_forbidden("x", bad["x.json"])
    with pytest.raises(EvidenceBundleError):
        build_bundle({"mission_summary.json": {"a": {"password": "x"}}, "decision.json": {}, "impact.json": {},
                      "limitations.json": {"limitations": []}}, mission_id="M", tenant_id="t")


def test_doubled_connector_prefix_is_normalised_on_read_without_rewriting():
    stored = {"reasons": ["CONNECTOR_CONNECTOR_FAILURE", "CONNECTOR_PARTIAL_RESPONSE"]}
    assert normalize_reason_codes(stored) == {"reasons": ["CONNECTOR_FAILURE", "CONNECTOR_PARTIAL_RESPONSE"]}
    assert stored["reasons"][0] == "CONNECTOR_CONNECTOR_FAILURE"


def test_legacy_workbook_without_control_case_artifact_is_reupload_required():
    from app.services.source_control_cases import control_case_compatibility

    source_id, _package_id, _ = _upload_and_compile(_canonical_bytes())
    from app.api.sources import _registered_sources

    desc = _registered_sources[source_id]
    tenant = (desc.metadata or {}).get("tenant_id")
    assert control_case_compatibility(tenant, source_id)["state"] == "OK"
    from app.storage.artifacts import ArtifactKey, get_artifact_store

    store = get_artifact_store()
    assert store.delete_artifact(ArtifactKey(tenant, "sources", source_id, "compiled", f"CTRL-{source_id}")) is not False
    state = control_case_compatibility(tenant, source_id)
    assert state["state"] == "REUPLOAD_REQUIRED" and "Re-upload" in state["message"]
