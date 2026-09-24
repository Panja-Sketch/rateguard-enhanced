"""Uploaded sources carry their real content hash into the mission record (and so
into the evidence bundle's inputs.json). The value is derived server-side from the
caller's own tenant artifact; whatever the browser sends is ignored."""

import hashlib

from app.storage import get_run_store
from tests.integration.test_workbook_to_connector_mission_e2e import (
    _canonical_bytes,
    _upload_and_compile,
    client,
)


def _create(source_a: dict) -> str:
    res = client.post(
        "/api/v1/missions",
        json={
            "name": "hash test", "mode": "RELEASE_CONFORMANCE", "product": "az_ho3", "jurisdiction": "Arizona",
            "effective_period_start": "2026-10-01", "disposable_sample_run": True,
            "source_a": source_a,
            "source_b": {"source_id": "rating-engine-demo", "source_type": "API_CONNECTOR", "name": "c",
                         "connector_id": "rating-engine-demo", "engine_version": "canonical-v1"},
        },
    )
    assert res.status_code == 202, res.text
    return res.json()["mission_id"]


def _stored_source_a(mission_id: str) -> dict:
    return get_run_store().get_run(mission_id).metadata["mission_object"]["source_a"]


def test_uploaded_workbook_hash_is_derived_server_side_and_a_forged_value_is_ignored():
    source_id, package_id, _ = _upload_and_compile(_canonical_bytes())
    ref = {"source_id": source_id, "source_type": "FILE", "name": "AZ_HO3_GOLDEN_workbook.xlsx",
           "compiled_package_id": package_id}
    expected = hashlib.sha256(_canonical_bytes()).hexdigest()
    assert _stored_source_a(_create(ref))["hash_checksum"] == expected
    forged = _stored_source_a(_create({**ref, "hash_checksum": "0" * 64}))
    assert forged["hash_checksum"] == expected


def test_a_bundled_sample_source_gets_no_hash():
    ref = {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"}
    assert _stored_source_a(_create({**ref, "hash_checksum": "f" * 64}))["hash_checksum"] is None
