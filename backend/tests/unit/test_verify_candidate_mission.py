"""Unit tests for backend/scripts/verify_candidate_mission.py (the operator-side
checks behind `--complete-verification`) and for the worker-recorded execution
provenance those checks rely on.

The evidence bundle is assembled by whichever API revision serves the download,
so `deployment.json` must report the WORKER that executed the mission (recorded
at lease time) -- not the environment of the process that built the zip. No GCP
service is contacted.
"""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_candidate_mission as vcm  # noqa: E402

from app.messaging.models import AssuranceJob  # noqa: E402
from app.services.evidence_bundle import _deployment_provenance  # noqa: E402
from app.services.mission_execution_service import MissionExecutionService  # noqa: E402
from app.storage import AssuranceRunRecord, AssuranceRunStatus, InMemoryRunStore  # noqa: E402

SHA = "1234567890abcdef1234567890abcdef12345678"
DIGEST = "sha256:" + "b" * 64
STATE = {
    "git_sha": SHA, "project_id": "rateguard-enhanced", "mission_topic": f"assurance-runs-candidate-verify-{SHA[:12]}",
    "candidate_worker_revision": "rateguard-worker-00007-cnd", "production_worker_revision": "rateguard-worker-00003-prd",
    "candidate_worker_image_digest": f"us-central1-docker.pkg.dev/p/r/rateguard-api@{DIGEST}",
}


def _dep(**over):
    base = {"git_sha": SHA, "image_digest": DIGEST, "cloud_run_service": "rateguard-worker",
            "cloud_run_revision": "rateguard-worker-00007-cnd"}
    return {**base, **over}


def _files(dep=None, inputs=None, connector=None):
    files = {
        "deployment.json": dep or _dep(),
        "inputs.json": inputs or {
            "source_a": {"source_type": "FILE", "hash_checksum": "abc123", "name": "controlled.xlsx"},
            "source_b": {"source_type": "API_CONNECTOR", "connector_id": "rating-engine", "engine_version": "1.2.0"},
        },
        "connector.json": connector or {
            "registry": {"connector_id": "rating-engine", "allowed_engine_versions": ["1.1.0", "1.2.0"]},
            "invocations": [{"evidence_id": "EV-1"}],
        },
        "decision.json": {"decision": "BLOCK_DEPLOYMENT"},
    }
    return {n: json.dumps(v, sort_keys=True).encode() for n, v in files.items()}


def _zip(files: dict[str, bytes], tamper: str | None = None, drop_sha_file: bool = False) -> bytes:
    manifest = {"files": [{"path": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in sorted(files.items())]}
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    out = dict(files)
    out["manifest.json"] = manifest_bytes
    if not drop_sha_file:
        out["manifest.sha256"] = (hashlib.sha256(manifest_bytes).hexdigest() + "  manifest.json\n").encode()
    if tamper:
        out[tamper] = out[tamper] + b" "
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in out.items():
            zf.writestr(n, b)
    return buf.getvalue()


def _bundle(**kw):
    return vcm.read_bundle(_zip(_files(**kw)))


def _record(**over):
    base = {"status": "COMPLETED", "decision": "BLOCK_DEPLOYMENT", "name": f"[CANDIDATE-VERIFY-{SHA[:12]}] wb vs rest"}
    return {**base, **over}


# --- mission record ---------------------------------------------------------


def test_completed_marked_mission_with_decision_passes() -> None:
    vcm.validate_mission_record(_record(), STATE)


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "PROCESSING", "WAITING_RETRY", "NEEDS_REVIEW", ""])
def test_non_terminal_mission_is_never_success(status: str) -> None:
    with pytest.raises(vcm.VerificationError, match="not terminal"):
        vcm.validate_mission_record(_record(status=status), STATE)


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "ARCHIVED"])
def test_terminal_but_not_completed_mission_is_rejected(status: str) -> None:
    with pytest.raises(vcm.VerificationError, match="not a completed decision"):
        vcm.validate_mission_record(_record(status=status), STATE)


def test_completed_mission_without_a_decision_is_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="no decision"):
        vcm.validate_mission_record(_record(decision=None), STATE)


@pytest.mark.parametrize("name", ["Pricing Release Assurance Mission", "[CANDIDATE-VERIFY-000000000000] x", None])
def test_mission_must_be_marked_as_synthetic_candidate_verification_for_this_sha(name) -> None:
    with pytest.raises(vcm.VerificationError, match="synthetic candidate verification"):
        vcm.validate_mission_record(_record(name=name), STATE)


# --- evidence bundle --------------------------------------------------------


def test_valid_bundle_passes_every_check() -> None:
    vcm.validate_bundle(_bundle(), STATE)


def test_bundle_from_the_production_worker_revision_is_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="!= candidate worker revision"):
        vcm.validate_bundle(_bundle(dep=_dep(cloud_run_revision="rateguard-worker-00003-prd")), STATE)


def test_bundle_from_an_api_revision_is_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="cloud_run_revision"):
        vcm.validate_bundle(_bundle(dep=_dep(cloud_run_revision="rateguard-api-00009-cnd", cloud_run_service="rateguard-api")), STATE)


def test_production_revision_recorded_as_candidate_is_still_rejected() -> None:
    state = {**STATE, "candidate_worker_revision": "rateguard-worker-00003-prd"}
    with pytest.raises(vcm.VerificationError, match="PRODUCTION worker"):
        vcm.validate_bundle(_bundle(dep=_dep(cloud_run_revision="rateguard-worker-00003-prd")), state)


def test_wrong_git_sha_is_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="git_sha"):
        vcm.validate_bundle(_bundle(dep=_dep(git_sha="f" * 40)), STATE)
    with pytest.raises(vcm.VerificationError, match="git_sha"):
        vcm.validate_bundle(_bundle(dep=_dep(git_sha=SHA[:12])), STATE)  # a short SHA is not the full SHA


def test_wrong_image_digest_is_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="image_digest"):
        vcm.validate_bundle(_bundle(dep=_dep(image_digest="sha256:" + "c" * 64)), STATE)


@pytest.mark.parametrize("source_a", [
    {"source_type": "SAMPLE_RELEASE", "hash_checksum": "x"},
    {"source_type": "FILE", "hash_checksum": ""},
    {},
])
def test_source_a_must_be_a_controlled_workbook_with_a_hash(source_a: dict) -> None:
    inputs = {"source_a": source_a, "source_b": {"source_type": "API_CONNECTOR", "connector_id": "rating-engine", "engine_version": "1.2.0"}}
    with pytest.raises(vcm.VerificationError, match="Source A"):
        vcm.validate_bundle(_bundle(inputs=inputs), STATE)


@pytest.mark.parametrize("source_b", [
    {"source_type": "FILE", "connector_id": "x", "engine_version": "1"},
    {"source_type": "API_CONNECTOR", "connector_id": "rating-engine", "engine_version": ""},
    {"source_type": "API_CONNECTOR", "connector_id": "", "engine_version": "1.2.0"},
])
def test_source_b_must_be_a_versioned_rest_connector(source_b: dict) -> None:
    inputs = {"source_a": {"source_type": "FILE", "hash_checksum": "abc"}, "source_b": source_b}
    with pytest.raises(vcm.VerificationError, match="Source B"):
        vcm.validate_bundle(_bundle(inputs=inputs), STATE)


def test_connector_version_must_be_registered_and_actually_invoked() -> None:
    with pytest.raises(vcm.VerificationError, match="not an allowed version"):
        vcm.validate_bundle(_bundle(connector={"registry": {"connector_id": "rating-engine", "allowed_engine_versions": ["0.9"]},
                                               "invocations": [{"x": 1}]}), STATE)
    with pytest.raises(vcm.VerificationError, match="registry entry"):
        vcm.validate_bundle(_bundle(connector={"registry": None, "invocations": [{"x": 1}]}), STATE)
    with pytest.raises(vcm.VerificationError, match="never actually called"):
        vcm.validate_bundle(_bundle(connector={"registry": {"connector_id": "rating-engine", "allowed_engine_versions": ["1.2.0"]},
                                               "invocations": []}), STATE)


def test_tampered_or_incomplete_bundles_are_rejected() -> None:
    with pytest.raises(vcm.VerificationError, match="sha256 mismatch"):
        vcm.read_bundle(_zip(_files(), tamper="decision.json"))
    with pytest.raises(vcm.VerificationError, match="manifest.sha256"):
        vcm.read_bundle(_zip(_files(), drop_sha_file=True))
    with pytest.raises(vcm.VerificationError, match="not a valid zip"):
        vcm.read_bundle(b"not a zip")


def test_fingerprint_is_stable_and_detects_changes() -> None:
    rec, b = _record(completed_at="t", attempt_number=1), _bundle()
    assert vcm.fingerprint(b, rec) == vcm.fingerprint(_bundle(), rec)
    assert vcm.fingerprint(b, rec) != vcm.fingerprint(b, {**rec, "attempt_number": 2})
    changed = _bundle(dep=_dep(cloud_run_revision="other"))
    assert vcm.fingerprint(changed, rec) != vcm.fingerprint(b, rec)


# --- duplicate delivery bookkeeping ----------------------------------------


def test_terminal_and_duplicate_events_are_counted_separately() -> None:
    events = [
        {"stage": "QUEUED", "action": "Mission accepted"},
        {"stage": "COMPLETED", "action": "Mission completed"},
        {"stage": "COMPLETED", "action": "Idempotency check: Job 'JOB-1' received for terminal mission status [COMPLETED]."},
        {"stage": "COMPLETED", "action": "Idempotency check: Job 'JOB-1' received for terminal mission status [COMPLETED]."},
    ]
    assert vcm.terminal_decision_events(events) == 1
    assert vcm.duplicate_events(events) == 2


def test_only_the_sha_scoped_isolated_topic_may_be_published_to() -> None:
    vcm.assert_isolated_topic(STATE, STATE["mission_topic"])
    for topic in ("assurance-runs", "impact-batches", f"impact-batches-candidate-verify-{SHA[:12]}", "assurance-runs-candidate-verify-000000000000"):
        with pytest.raises(vcm.VerificationError, match="refusing to publish"):
            vcm.assert_isolated_topic(STATE, topic)
    with pytest.raises(vcm.VerificationError, match="refusing to publish"):
        vcm.assert_isolated_topic({**STATE, "mission_topic": "assurance-runs"}, STATE["mission_topic"])


def test_load_state_requires_the_recorded_fields(tmp_path: Path) -> None:
    good = tmp_path / "s.json"
    good.write_text(json.dumps(STATE), encoding="utf-8")
    assert vcm.load_state(str(good))["git_sha"] == SHA
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"git_sha": SHA}), encoding="utf-8")
    with pytest.raises(vcm.VerificationError, match="missing"):
        vcm.load_state(str(bad))
    with pytest.raises(vcm.VerificationError, match="cannot read"):
        vcm.load_state(str(tmp_path / "absent.json"))


def test_main_reports_failure_without_a_traceback_and_never_reads_tokens(tmp_path: Path, capsys) -> None:
    rc = vcm.main(["check", "--state-file", str(tmp_path / "absent.json"), "--mission-id", "MIS-1A2B3C4D"])
    assert rc == 1
    assert "VERIFICATION FAILED" in capsys.readouterr().err
    import ast

    tree = ast.parse((SCRIPTS_DIR / "verify_candidate_mission.py").read_text(encoding="utf-8"))
    tree.body = tree.body[1:]  # drop the module docstring, which discusses tokens in prose
    text = ast.unparse(tree).lower()
    assert "authorization" not in text and "bearer" not in text and "id_token" not in text and "getpass" not in text


# --- worker-recorded execution provenance ------------------------------------


def test_deployment_json_reports_the_recorded_worker_not_the_serving_process(monkeypatch) -> None:
    monkeypatch.setenv("K_REVISION", "rateguard-api-00009-serving-api")
    monkeypatch.setenv("K_SERVICE", "rateguard-api")
    monkeypatch.setenv("RATEGUARD_GIT_SHA", "api-sha")
    recorded = {"git_sha": SHA, "image_digest": DIGEST, "cloud_run_service": "rateguard-worker",
                "cloud_run_revision": "rateguard-worker-00007-cnd", "extra": "dropped"}
    assert _deployment_provenance({"execution_provenance": recorded}) == {
        "git_sha": SHA, "image_digest": DIGEST, "cloud_run_service": "rateguard-worker",
        "cloud_run_revision": "rateguard-worker-00007-cnd",
    }
    # Missions that predate the record fall back to the serving process.
    assert _deployment_provenance({})["cloud_run_revision"] == "rateguard-api-00009-serving-api"


def test_worker_records_its_own_provenance_once_and_duplicates_never_overwrite_it(monkeypatch) -> None:
    from app.models.mission import MissionStatus  # noqa: F401

    monkeypatch.setenv("K_REVISION", "rateguard-worker-00007-cnd")
    monkeypatch.setenv("K_SERVICE", "rateguard-worker")
    monkeypatch.setenv("RATEGUARD_GIT_SHA", SHA)
    monkeypatch.setenv("RATEGUARD_IMAGE_DIGEST", DIGEST)
    from tests.agents.test_worker_delivery_outcomes import _seed_queued_mission

    mission_id = "MIS-PROVENANCE"
    store = InMemoryRunStore()
    _seed_queued_mission(store, mission_id)
    job = AssuranceJob(job_id=f"JOB-{mission_id}", run_id=mission_id, job_type="ASSURANCE_MISSION_V2")

    def _finish(mission, *a, **k):
        store.update_run_status(run_id=mission_id, status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED")
        return MagicMock()

    with (
        patch("app.services.mission_execution_service.get_run_store", return_value=store),
        patch("app.services.mission_execution_service.AssuranceSupervisor") as sup,
        patch("app.services.mission_execution_service.resolve_demo_package", return_value=MagicMock()),
    ):
        sup.return_value.run_mission.side_effect = _finish
        MissionExecutionService.execute_job(job)
        first = dict(store.get_run(mission_id).metadata["execution_provenance"])
        monkeypatch.setenv("K_REVISION", "rateguard-worker-00099-someone-else")
        MissionExecutionService.execute_job(job)  # duplicate delivery
        second = store.get_run(mission_id).metadata["execution_provenance"]
    assert first == {"git_sha": SHA, "image_digest": DIGEST, "cloud_run_service": "rateguard-worker",
                     "cloud_run_revision": "rateguard-worker-00007-cnd"}
    assert second == first
    assert isinstance(store.get_run(mission_id), AssuranceRunRecord)
