"""Deployment verification gate for RateGuard.

Runs a set of pass/fail checks that a deployment must satisfy before it is
trusted. Two tiers:

  OFFLINE checks (default, always run): pure local code/env/package
  inspection. No network calls, no gcloud, no GCP credentials, no live
  Gemini call, no production Firestore/Pub/Sub/GCS/BigQuery access. Safe to
  run in any environment, including this repository's own CI.

  LIVE checks (`--live`, opt-in): read-only calls against a real deployed
  environment. The Firestore-readiness check calls the target's own
  `/health/ready` HTTP endpoint rather than touching Firestore directly from
  this script (minimizing billable calls and duplicated dependency logic,
  per the "use /health/ready for dependency checks" rule). The Pub/Sub and
  image-digest checks shell out to read-only `gcloud` describe commands —
  never create/update/delete.

Exit code is 0 only if every check that ran passed. `--live` checks that are
skipped (no target configured) are reported as SKIPPED, not PASS or FAIL.

Usage:
    python scripts/verify_deployment.py                # offline checks only
    python scripts/verify_deployment.py --live \\
        --api-url https://rateguard-api-xxx.a.run.app \\
        --worker-url https://rateguard-worker-xxx.a.run.app \\
        --project rateguard-ai --region us-central1
"""

import argparse
import importlib.metadata
import inspect
import json
import subprocess
import sys
from dataclasses import dataclass

KNOWN_BAD_GOOGLE_API_CORE_VERSION = "2.35.0"
EXPECTED_GEMINI_MODEL = "gemini-3.7-flash"
EXPECTED_PUSH_ENDPOINT_SUFFIX = "/internal/pubsub/assurance"


@dataclass
class CheckResult:
    name: str
    passed: bool | None  # True=pass, False=fail, None=skipped
    detail: str


def _result(name: str, passed: bool | None, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


# --- OFFLINE checks ---------------------------------------------------------


def check_google_api_core_version() -> CheckResult:
    try:
        version = importlib.metadata.version("google-api-core")
    except importlib.metadata.PackageNotFoundError:
        return _result("google_api_core_version", False, "google-api-core is not installed.")

    if version == KNOWN_BAD_GOOGLE_API_CORE_VERSION:
        return _result(
            "google_api_core_version", False,
            f"Resolved google-api-core=={version}, the known-bad version with the "
            "'(default)' database-id percent-encoding regression. Pin google-api-core"
            "==2.34.0 (or a verified fixed release) in backend/pyproject.toml.",
        )
    return _result("google_api_core_version", True, f"Resolved google-api-core=={version}.")


def check_worker_dispatches_via_mission_execution_service() -> CheckResult:
    try:
        from app.messaging.worker import AssuranceWorker

        source = inspect.getsource(AssuranceWorker.process_job)
    except Exception as e:
        return _result(
            "worker_mission_v2_dispatch", False, f"Could not inspect AssuranceWorker.process_job: {e}",
        )

    if "MissionExecutionService" not in source:
        return _result(
            "worker_mission_v2_dispatch", False,
            "AssuranceWorker.process_job does not reference MissionExecutionService — "
            "this looks like the pre-refactor worker that constructs AssuranceMission "
            "inline and lacks atomic lease/idempotency protection.",
        )
    return _result("worker_mission_v2_dispatch", True, "MIS-* dispatch routes through MissionExecutionService.")


def check_gemini_auth_mode() -> CheckResult:
    try:
        from app.agents.config import get_agent_config
        from app.agents.gemini_client import AUTH_MODE_NONE, GeminiDecisionClient

        config = get_agent_config()
        if not config.agent_enabled:
            return _result("gemini_auth_mode", True, "Gemini disabled (RATEGUARD_AGENT_ENABLED=false); auth mode not applicable.")

        client = GeminiDecisionClient(config)
        auth_mode, _ = client._resolve_auth_mode()
        if auth_mode == AUTH_MODE_NONE:
            return _result(
                "gemini_auth_mode", False,
                "RATEGUARD_AGENT_ENABLED=true but no auth mode resolves (no "
                "GOOGLE_GENAI_USE_VERTEXAI + ADC, and no GOOGLE_API_KEY/GEMINI_API_KEY). "
                "Every Gemini decision point will silently fall back to deterministic "
                "behavior.",
            )
        return _result("gemini_auth_mode", True, f"Gemini enabled, auth_mode={auth_mode}.")
    except Exception as e:
        return _result("gemini_auth_mode", False, f"Could not resolve Gemini auth mode: {e}")


def check_gemini_model_id() -> CheckResult:
    try:
        from app.agents.config import get_agent_config

        model = get_agent_config().gemini_model
    except Exception as e:
        return _result("gemini_model_id", False, f"Could not read configured Gemini model: {e}")

    if model != EXPECTED_GEMINI_MODEL:
        return _result(
            "gemini_model_id", False,
            f"Configured Gemini model is '{model}', expected '{EXPECTED_GEMINI_MODEL}'.",
        )
    return _result("gemini_model_id", True, f"Gemini model is '{model}'.")


def check_worker_endpoint_never_acks_retryable_failure() -> CheckResult:
    """In-process (no network): simulates a RETRYABLE_FAILURE ProcessingResult
    and confirms the HTTP layer answers non-2xx, not 200."""
    try:
        import base64
        import json as _json
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app.main import app
        from app.messaging.models import AssuranceJob
        from app.messaging.outcomes import ProcessingOutcome, ProcessingResult

        client = TestClient(app)
        job = AssuranceJob(job_id="JOB-VERIFY", run_id="MIS-VERIFY", job_type="ASSURANCE_MISSION_V2")
        b64_data = base64.b64encode(job.model_dump_json().encode("utf-8")).decode("utf-8")
        envelope = {"message": {"data": b64_data, "message_id": "MSG-VERIFY"}}

        with patch("app.api.worker_endpoint.AssuranceWorker") as mock_worker_cls:
            mock_worker_cls.return_value.process_job.return_value = ProcessingResult(
                outcome=ProcessingOutcome.RETRYABLE_FAILURE, run_id="MIS-VERIFY", job_id="JOB-VERIFY",
                status_write_ok=False,
            )
            res = client.post("/internal/pubsub/assurance", json=envelope)

        from app.messaging.outcomes import OUTCOME_HTTP_STATUS

        expected_status = OUTCOME_HTTP_STATUS[ProcessingOutcome.RETRYABLE_FAILURE]
        if res.status_code != expected_status:
            return _result(
                "worker_endpoint_ack_semantics", False,
                f"A RETRYABLE_FAILURE ProcessingResult produced HTTP {res.status_code}, "
                f"expected exactly {expected_status} — the endpoint would either "
                "acknowledge a failed delivery as if it succeeded, or return the wrong "
                "non-2xx code.",
            )
        body = res.json()
        if body.get("status") != ProcessingOutcome.RETRYABLE_FAILURE.value:
            return _result(
                "worker_endpoint_ack_semantics", False,
                f"Unexpected response body for a retryable failure: {_json.dumps(body)[:200]}",
            )
        return _result(
            "worker_endpoint_ack_semantics", True,
            f"RETRYABLE_FAILURE correctly produced HTTP {res.status_code}.",
        )
    except Exception as e:
        return _result("worker_endpoint_ack_semantics", False, f"Check itself failed: {e}")


def run_offline_checks() -> list[CheckResult]:
    return [
        check_google_api_core_version(),
        check_worker_dispatches_via_mission_execution_service(),
        check_gemini_auth_mode(),
        check_gemini_model_id(),
        check_worker_endpoint_never_acks_retryable_failure(),
    ]


# --- LIVE checks (opt-in, read-only) ----------------------------------------


def check_firestore_readiness_via_health_endpoint(api_url: str | None) -> CheckResult:
    if not api_url:
        return _result("firestore_readiness", None, "Skipped: no --api-url provided.")
    try:
        import urllib.request

        with urllib.request.urlopen(f"{api_url.rstrip('/')}/health/ready", timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return _result("firestore_readiness", False, f"/health/ready request failed: {e}")

    run_store_check = body.get("checks", {}).get("run_store", {})
    if run_store_check.get("status") != "ok":
        return _result(
            "firestore_readiness", False,
            f"/health/ready reports run_store degraded: {run_store_check.get('detail')}",
        )
    return _result("firestore_readiness", True, "/health/ready reports run_store ok.")


def _gcloud_json(args: list[str]) -> tuple[dict | None, str | None]:
    try:
        proc = subprocess.run(
            ["gcloud", *args, "--format=json"], capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            return None, proc.stderr.strip()[:300]
        return json.loads(proc.stdout), None
    except FileNotFoundError:
        return None, "gcloud CLI not found on PATH."
    except Exception as e:
        return None, str(e)


def check_pubsub_push_endpoint(project: str | None, subscription: str = "assurance-worker") -> CheckResult:
    if not project:
        return _result("pubsub_push_endpoint", None, "Skipped: no --project provided.")
    data, err = _gcloud_json(["pubsub", "subscriptions", "describe", subscription, "--project", project])
    if err:
        return _result("pubsub_push_endpoint", False, f"gcloud pubsub subscriptions describe failed: {err}")
    push_endpoint = (data or {}).get("pushConfig", {}).get("pushEndpoint", "")
    if not push_endpoint.endswith(EXPECTED_PUSH_ENDPOINT_SUFFIX):
        return _result(
            "pubsub_push_endpoint", False,
            f"Push endpoint '{push_endpoint}' does not end with '{EXPECTED_PUSH_ENDPOINT_SUFFIX}'.",
        )
    return _result("pubsub_push_endpoint", True, f"Push endpoint correctly targets {EXPECTED_PUSH_ENDPOINT_SUFFIX}.")


def check_api_worker_image_parity(project: str | None, region: str | None) -> CheckResult:
    if not project or not region:
        return _result("image_digest_parity", None, "Skipped: --project/--region not provided.")

    digests: dict[str, str] = {}
    for service in ("rateguard-api", "rateguard-worker"):
        data, err = _gcloud_json([
            "run", "services", "describe", service, "--project", project, "--region", region,
        ])
        if err:
            return _result("image_digest_parity", False, f"Could not describe {service}: {err}")
        traffic = (data or {}).get("status", {}).get("traffic", [])
        digest = next((t.get("imageDigest") for t in traffic if t.get("imageDigest")), None)
        if not digest:
            image = (
                (data or {}).get("spec", {}).get("template", {}).get("spec", {})
                .get("containers", [{}])[0].get("image", "unresolved")
            )
            digest = f"UNRESOLVED_DIGEST({image})"
        digests[service] = digest

    if digests.get("rateguard-api") != digests.get("rateguard-worker"):
        return _result(
            "image_digest_parity", False,
            f"rateguard-api and rateguard-worker are running different image digests: {digests}",
        )
    return _result("image_digest_parity", True, f"Both services share image digest {digests.get('rateguard-api')}.")


def run_live_checks(api_url: str | None, project: str | None, region: str | None) -> list[CheckResult]:
    return [
        check_firestore_readiness_via_health_endpoint(api_url),
        check_pubsub_push_endpoint(project),
        check_api_worker_image_parity(project, region),
    ]


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Also run read-only checks against a real deployment.")
    parser.add_argument("--api-url", default=None, help="Deployed rateguard-api base URL (for /health/ready).")
    parser.add_argument("--worker-url", default=None, help="Deployed rateguard-worker base URL (currently informational only).")
    parser.add_argument("--project", default=None, help="GCP project id for gcloud describe checks.")
    parser.add_argument("--region", default=None, help="GCP region for gcloud describe checks.")
    args = parser.parse_args(argv)

    results = run_offline_checks()
    if args.live:
        results += run_live_checks(args.api_url, args.project, args.region)

    failed = [r for r in results if r.passed is False]
    skipped = [r for r in results if r.passed is None]

    print("--- Deployment Verification ---")
    for r in results:
        marker = "PASS" if r.passed else ("SKIP" if r.passed is None else "FAIL")
        print(f"[{marker}] {r.name}: {r.detail}")

    print(f"\n{len(results) - len(failed) - len(skipped)}/{len(results)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
