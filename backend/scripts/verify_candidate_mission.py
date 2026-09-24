"""Operator-side checks for `deploy_candidate_enhanced.sh --complete-verification`.

Runs with the OPERATOR's Application Default Credentials only (Firestore read,
GCS artifact store, Pub/Sub publish). It never accepts, reads, prints or stores
a password, Firebase ID token or bearer token: the evidence bundle is assembled
by calling the API's own `download_mission_evidence_zip` route function
in-process (same code path the candidate API serves), with a principal built
from the mission record's own tenant -- no HTTP, no token.

Subcommands (all take `--state-file` = the pending-state JSON written by
`--prepare-verification`, and `--mission-id`):

  check      mission reached a terminal decision; evidence bundle is intact;
             deployment.json proves the CANDIDATE worker revision/SHA/digest
             executed it; controlled-workbook + versioned REST connector
             provenance is present; prints a fingerprint of the result.
  duplicate  re-runs `check`, publishes the SAME job envelope twice to the
             isolated mission topic, waits for both redeliveries to be
             acknowledged as duplicates, then proves exactly one terminal
             decision and an unchanged evidence bundle.

Exit code 0 = every check passed; 1 = a check failed (details on stderr);
2 = usage/state error.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

VERIFY_NAME_MARKER = "CANDIDATE-VERIFY"
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "ARCHIVED"}
SUCCESS_STATUSES = {"COMPLETED"}
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
DUPLICATE_EVENT_PREFIX = "Idempotency check:"


class VerificationError(Exception):
    """A verification check failed."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_state(path: str) -> dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerificationError(f"cannot read pending-state file {path}: {exc}") from exc
    required = (
        "git_sha", "mission_topic", "candidate_worker_revision", "production_worker_revision",
        "candidate_worker_image_digest", "project_id",
    )
    missing = [k for k in required if not state.get(k)]
    if missing:
        raise VerificationError(f"pending-state file is missing: {', '.join(missing)}")
    return state


def assert_isolated_topic(state: dict[str, Any], topic: str) -> None:
    """The ONLY topic this tool may publish to is the SHA-scoped temporary mission topic."""
    sha12 = state["git_sha"][:12]
    expected = f"assurance-runs-candidate-verify-{sha12}"
    if topic != expected or state.get("mission_topic") != expected:
        raise VerificationError(f"refusing to publish to '{topic}': only '{expected}' is allowed")


def validate_mission_record(record: dict[str, Any], state: dict[str, Any]) -> None:
    status = str(record.get("status") or "")
    if status not in TERMINAL_STATUSES:
        raise VerificationError(f"mission is not terminal (status={status or '<none>'}); QUEUED/RUNNING is not success")
    if status not in SUCCESS_STATUSES:
        raise VerificationError(f"mission reached terminal status {status}, not a completed decision")
    if not record.get("decision"):
        raise VerificationError("mission is terminal but recorded no decision")
    name = str(record.get("name") or "")
    if VERIFY_NAME_MARKER not in name or state["git_sha"][:12] not in name:
        raise VerificationError(
            f"mission name must be marked '[{VERIFY_NAME_MARKER}-{state['git_sha'][:12]}]' "
            f"(synthetic candidate verification); got '{name}'"
        )


def read_bundle(zip_bytes: bytes) -> dict[str, Any]:
    """Returns {"files": {path: bytes}, "manifest": {...}} after integrity checks."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise VerificationError(f"evidence bundle is not a valid zip: {exc}") from exc
    files = {n: zf.read(n) for n in zf.namelist()}
    if "manifest.json" not in files:
        raise VerificationError("evidence bundle has no manifest.json")
    manifest = json.loads(files["manifest.json"])
    listed = {f["path"]: f for f in manifest.get("files", [])}
    for path, entry in listed.items():
        if path not in files:
            raise VerificationError(f"manifest lists {path} but the bundle does not contain it")
        if _sha(files[path]) != entry.get("sha256"):
            raise VerificationError(f"sha256 mismatch for {path}: bundle is corrupt or altered")
    unlisted = set(files) - set(listed) - {"manifest.json", "manifest.sha256"}
    if unlisted:
        raise VerificationError(f"bundle contains files absent from the manifest: {sorted(unlisted)}")
    declared = files.get("manifest.sha256", b"").decode("utf-8").split()[:1]
    if declared != [_sha(files["manifest.json"])]:
        raise VerificationError("manifest.sha256 does not match manifest.json")
    return {"files": files, "manifest": manifest}


def _json(bundle: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return json.loads(bundle["files"][name])
    except KeyError as exc:
        raise VerificationError(f"evidence bundle has no {name}") from exc


def validate_bundle(bundle: dict[str, Any], state: dict[str, Any]) -> None:
    dep = _json(bundle, "deployment.json")
    cand_rev = state["candidate_worker_revision"]
    prod_rev = state["production_worker_revision"]
    if dep.get("cloud_run_revision") != cand_rev:
        raise VerificationError(
            f"deployment.json.cloud_run_revision={dep.get('cloud_run_revision')!r} != candidate worker revision {cand_rev!r}"
        )
    if dep.get("cloud_run_revision") == prod_rev:
        raise VerificationError("mission ran on the PRODUCTION worker revision")
    if dep.get("git_sha") != state["git_sha"]:
        raise VerificationError(f"deployment.json.git_sha={dep.get('git_sha')!r} != candidate SHA {state['git_sha']!r}")
    if dep.get("cloud_run_service") != "rateguard-worker":
        raise VerificationError(f"deployment.json.cloud_run_service={dep.get('cloud_run_service')!r} is not rateguard-worker")
    want = SHA256_RE.search(state["candidate_worker_image_digest"])
    if not want or want.group(0) != dep.get("image_digest"):
        raise VerificationError("deployment.json.image_digest does not match the candidate worker image digest")

    inputs = _json(bundle, "inputs.json")
    src_a, src_b = inputs.get("source_a") or {}, inputs.get("source_b") or {}
    if src_a.get("source_type") != "FILE" or not src_a.get("hash_checksum"):
        raise VerificationError("Source A is not a controlled workbook upload with a content hash")
    if src_b.get("source_type") != "API_CONNECTOR" or not src_b.get("connector_id") or not src_b.get("engine_version"):
        raise VerificationError("Source B is not a versioned REST connector (connector_id + engine_version)")
    connector = _json(bundle, "connector.json")
    registry = connector.get("registry") or {}
    if registry.get("connector_id") != src_b["connector_id"]:
        raise VerificationError("connector registry entry does not match Source B")
    if src_b["engine_version"] not in (registry.get("allowed_engine_versions") or []):
        raise VerificationError("Source B engine_version is not an allowed version of the registered connector")
    if not connector.get("invocations"):
        raise VerificationError("no connector invocation evidence: the connector was never actually called")


def fingerprint(bundle: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": record.get("status"),
        "decision": record.get("decision"),
        "completed_at": record.get("completed_at"),
        "attempt_number": record.get("attempt_number"),
        "files": {f["path"]: f["sha256"] for f in bundle["manifest"]["files"]},
    }


def terminal_decision_events(events: list[dict[str, str]]) -> int:
    return sum(
        1 for e in events
        if e["stage"] in TERMINAL_STATUSES and not e["action"].startswith(DUPLICATE_EVENT_PREFIX)
    )


def duplicate_events(events: list[dict[str, str]]) -> int:
    return sum(1 for e in events if e["action"].startswith(DUPLICATE_EVENT_PREFIX))


# --- ADC-backed operations (imported lazily so the pure checks above are unit-testable) ---


def _configure_environment(state: dict[str, Any]) -> None:
    import os

    for k, v in {
        "RATEGUARD_RUN_STORE": "firestore", "RATEGUARD_ARTIFACT_STORE": "gcs",
        "RATEGUARD_FIRESTORE_DATABASE": "(default)",
        "RATEGUARD_FIRESTORE_COLLECTION": state.get("firestore_collection", "assurance_runs"),
        "RATEGUARD_GCS_BUCKET": state.get("gcs_bucket", "rateguard-enhanced-artifacts"),
        "RATEGUARD_GOOGLE_CLOUD_PROJECT": state["project_id"], "GOOGLE_CLOUD_PROJECT": state["project_id"],
    }.items():
        os.environ[k] = v


def _bundle_for(mission_id: str, record: dict[str, Any]) -> dict[str, Any]:
    from app.api.missions import download_mission_evidence_zip
    from app.auth.models import AuthenticatedUser, Role

    user = AuthenticatedUser(uid="candidate-verification-operator", tenant_id=str(record["tenant_id"]), role=Role.ADMIN)
    response = download_mission_evidence_zip(mission_id, user=user, _quota=None)
    return read_bundle(bytes(response.body))


def _events(store: Any, mission_id: str) -> list[dict[str, str]]:
    return [{"stage": str(e.stage), "action": str(e.action)} for e in store.get_events(mission_id)]


def run_check(state: dict[str, Any], mission_id: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    from app.storage import get_run_store

    _configure_environment(state)
    store = get_run_store(strict=True)
    record = _load_record(store, mission_id)
    validate_mission_record(record, state)
    bundle = _bundle_for(mission_id, record)
    validate_bundle(bundle, state)
    return store, record, bundle


def _load_record(store: Any, mission_id: str) -> dict[str, Any]:
    rec = store.get_run(mission_id)
    if rec is None:
        raise VerificationError(f"mission {mission_id} not found")
    meta = rec.metadata if isinstance(rec.metadata, dict) else {}
    mission_obj = meta.get("mission_object") if isinstance(meta.get("mission_object"), dict) else {}
    return {
        "status": getattr(rec.status, "value", str(rec.status)), "decision": rec.decision,
        "name": mission_obj.get("name") or meta.get("name"), "tenant_id": rec.tenant_id,
        "completed_at": str(rec.completed_at or ""), "attempt_number": rec.attempt_number,
        "correlation_id": meta.get("correlation_id"), "left": rec.left_package_id, "right": rec.right_package_id,
    }


def cmd_check(state: dict[str, Any], mission_id: str) -> dict[str, Any]:
    _, record, bundle = run_check(state, mission_id)
    return fingerprint(bundle, record)


def cmd_duplicate(state: dict[str, Any], mission_id: str, wait_seconds: int) -> dict[str, Any]:
    from google.cloud import pubsub_v1

    store, record, bundle = run_check(state, mission_id)
    before = fingerprint(bundle, record)
    events_before = _events(store, mission_id)
    terminal_before, dup_before = terminal_decision_events(events_before), duplicate_events(events_before)

    topic = state["mission_topic"]
    assert_isolated_topic(state, topic)
    envelope = {
        "job_id": f"JOB-{mission_id}", "run_id": mission_id, "job_type": "ASSURANCE_MISSION_V2", "schema_version": 2,
        "correlation_id": record.get("correlation_id"), "tenant_id": record["tenant_id"],
        "left_source_id": record.get("left"), "right_source_id": record.get("right"),
        "left_package_id": record.get("left"), "right_package_id": record.get("right"),
        "include_portfolio_analysis": True,
    }
    data = json.dumps(envelope).encode("utf-8")
    publisher = pubsub_v1.PublisherClient()
    path = publisher.topic_path(state["project_id"], topic)
    for _ in range(2):
        publisher.publish(path, data, run_id=mission_id, job_id=envelope["job_id"]).result(timeout=30)

    deadline = time.time() + wait_seconds
    events_after = events_before
    while time.time() < deadline:
        events_after = _events(store, mission_id)
        if duplicate_events(events_after) - dup_before >= 2:
            break
        time.sleep(5)
    if duplicate_events(events_after) - dup_before < 2:
        raise VerificationError("the candidate worker did not acknowledge both duplicate deliveries in time")
    if terminal_decision_events(events_after) != terminal_before:
        raise VerificationError("a duplicate delivery produced an additional terminal decision")

    record_after = _load_record(store, mission_id)
    bundle_after = _bundle_for(mission_id, record_after)
    after = fingerprint(bundle_after, record_after)
    if after != before:
        raise VerificationError("evidence/result changed after duplicate delivery")
    return {"before": before, "after": after, "duplicate_events_added": duplicate_events(events_after) - dup_before}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "duplicate"))
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        state = load_state(args.state_file)
        if args.command == "check":
            result = cmd_check(state, args.mission_id)
        else:
            result = cmd_duplicate(state, args.mission_id, args.wait_seconds)
    except VerificationError as exc:
        print(f"VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
