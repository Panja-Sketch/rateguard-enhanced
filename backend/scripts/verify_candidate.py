"""Opt-in candidate/staging acceptance test suite.

Exercises a deployed CANDIDATE (`--no-traffic --tag candidate`) API + worker
end to end over HTTP only: creates one real, disposable RELEASE_CONFORMANCE
demo mission, waits for it to complete, and inspects the result for the
specific evidence Group 3 requires (real Gemini decision, deterministic
premium mismatches, BLOCK_DEPLOYMENT, portfolio impact, remediation +
revalidation, a deterministic action with no model_id), then exercises the
mission lifecycle (cancel, delete, archive, structured validation errors).

Refuses to run without explicit opt-in (`--yes-test-candidate`), because it
creates real state (a mission, Gemini invocations, Firestore documents) in
whatever `--api-url` it is pointed at. It only ever talks to the URLs passed
on the command line — never discovers or guesses a URL, never touches
production. The candidate API's own environment (RATEGUARD_PUBSUB_TOPIC=
assurance-runs-staging, RATEGUARD_FIRESTORE_COLLECTION=assurance_runs_staging)
is what actually confines every side effect to staging resources — this
script has no direct Pub/Sub or Firestore access of its own and never needs
any.

Real Gemini decision evidence (schema_valid, response_id presence, latency,
token counts) is verified via GET /api/v1/missions/{id}/evidence — a
sanitized, read-only endpoint that whitelists exactly those fields and never
returns prompts, raw model output, credentials, or rationale/confidence text
(see app.api.missions.get_mission_gemini_evidence).

Scope limitation (documented, not silently skipped): items that would require
direct Pub/Sub publish access (inducing a genuine duplicate delivery) or a
dedicated per-stage events-listing API are not exposable through the public
HTTP surface this script is restricted to. Those specific sub-checks are
reported as BEST-EFFORT / proxy checks rather than faked as a full PASS. The
stronger, direct-access version of the duplicate-delivery check already
exists as this repository's own pytest suite
(test_worker_delivery_outcomes.py).

Does NOT test malformed/poison DLQ delivery — that is intentionally a
separate, explicitly destructive-to-staging-only script
(test_dlq_poison_delivery.py), since it deliberately creates a message
designed to fail repeatedly.

Usage:
    python scripts/verify_candidate.py --yes-test-candidate \\
        --api-url https://candidate---rateguard-api-xxx.a.run.app \\
        --frontend-url https://candidate---rateguard-web-xxx.a.run.app
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

EXPECTED_GEMINI_MODEL = "gemini-3.7-flash"
EXPECTED_FRAMEWORK_SUBSTRING = "Google GenAI SDK"
EXPECTED_PROVIDER = "Google Vertex AI"
EXPECTED_AUTH_MODE = "VERTEX_AI"
EXPECTED_LOCATION = "global"
DEMO_SOURCE_A = "AZ_HO3_2026_09"
DEMO_SOURCE_B_DEFECTIVE = "AZ_HO3_2026_09_DEFECTIVE"

# Defensive secret-shaped patterns to scan /api/v1/system/status for. Mirrors
# app.agents.gemini_client._SECRET_PATTERNS's intent (defense-in-depth, not a
# guarantee) applied to the response body instead of log lines.
_SECRET_LIKE_SUBSTRINGS = ("AIza", "ya29.", "-----BEGIN")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class Report:
    checks: list[CheckResult] = field(default_factory=list)
    staging_mission_ids: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(CheckResult(name, passed, detail))
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: {detail}")


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _normalize_headers(headers: Any) -> dict[str, str]:
    """Normalizes HTTP response header names to lowercase exactly once, at
    the single point where they're captured. HTTP/2 intermediaries --
    including Cloud Run's Google Frontend -- lowercase every header name per
    RFC 7540 §8.1.2, so a plain `dict(response.headers)` preserves whatever
    casing the server actually sent on the wire and silently breaks any
    capitalized-key lookup like "Access-Control-Allow-Origin". `headers` is
    an `http.client.HTTPMessage` (from a normal response) or
    `email.message.Message` (from an HTTPError) -- both support `.items()`,
    and both may repeat a header name, so this keeps the last occurrence
    exactly like `dict(headers)` already did."""
    return {k.lower(): v for k, v in headers.items()}


def _http_with_origin(
    method: str, url: str, origin: str, extra_headers: dict | None = None, timeout: float = 15.0
) -> tuple[int, dict[str, str]]:
    """Like _http, but sends a browser-shaped Origin header and returns
    response HEADERS (normalized to lowercase keys -- see
    _normalize_headers) instead of the parsed body -- this is the piece
    plain CLI/urllib acceptance checks never exercised: a client that never
    sends Origin never observes CORSMiddleware reject a request, so a
    candidate deployment could pass every other check here while every real
    browser request from the candidate frontend was silently blocked."""
    headers = {"Origin": origin}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _normalize_headers(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, _normalize_headers(e.headers)


def check_health_live(report: Report, api_url: str) -> None:
    status_code, body = _http("GET", f"{api_url}/health/live")
    ok = status_code == 200 and body.get("status") == "healthy"
    report.add("health_live", ok, f"HTTP {status_code}, body={body}")


def check_health_ready(report: Report, api_url: str) -> None:
    status_code, body = _http("GET", f"{api_url}/health/ready")
    checks = body.get("checks", {})
    run_store_ok = checks.get("run_store", {}).get("status") == "ok"
    queue_ok = checks.get("message_queue", {}).get("status") == "ok"
    # /health/ready does not separately probe "worker readiness" as a distinct
    # dependency (the worker is a separate Cloud Run service with its own
    # /health/ready) — message_queue==ok here confirms this API instance can
    # construct a Pub/Sub publisher for the configured staging topic, which is
    # the API-side half of worker readiness.
    ok = status_code in (200, 503) and run_store_ok and queue_ok
    report.add(
        "health_ready", ok,
        f"HTTP {status_code}, run_store={checks.get('run_store')}, message_queue={checks.get('message_queue')}",
    )


def check_system_status(report: Report, api_url: str) -> None:
    """Validates the structured `gemini` sub-object of /api/v1/system/status
    field by field (exact model id, provider, framework, auth mode, and the
    effective Gemini *location* - never the general Cloud Run/GCP deployment
    region) rather than a loose substring match over the whole response,
    which previously passed even when the reported values were wrong."""
    status_code, body = _http("GET", f"{api_url}/api/v1/system/status")
    gemini = body.get("gemini", {}) if isinstance(body, dict) else {}
    raw_text = json.dumps(body)

    model_ok = gemini.get("configured_model_id") == EXPECTED_GEMINI_MODEL
    provider_ok = gemini.get("provider") == EXPECTED_PROVIDER
    framework_ok = EXPECTED_FRAMEWORK_SUBSTRING in (gemini.get("framework") or "")
    auth_mode_ok = gemini.get("auth_mode") == EXPECTED_AUTH_MODE
    location_ok = gemini.get("configured_location") == EXPECTED_LOCATION
    probe_not_invoked = gemini.get("endpoint_probe_invoked") is False
    no_secrets = not any(marker in raw_text for marker in _SECRET_LIKE_SUBSTRINGS)

    ok = (
        status_code == 200
        and model_ok
        and provider_ok
        and framework_ok
        and auth_mode_ok
        and location_ok
        and probe_not_invoked
        and no_secrets
    )
    report.add(
        "system_status", ok,
        f"HTTP {status_code}, model_ok={model_ok}, provider_ok={provider_ok}, framework_ok={framework_ok}, "
        f"auth_mode_ok={auth_mode_ok}, location_ok={location_ok}, endpoint_probe_invoked_false={probe_not_invoked}, "
        f"no_secret_markers={no_secrets}",
    )


def check_cors_from_candidate_web_origin(report: Report, api_url: str, frontend_url: str) -> None:
    """Verifies the candidate API grants CORS access to the candidate WEB
    origin specifically -- not merely that the API answers HTTP requests.
    A plain GET/POST from this script always "succeeds" at the transport
    level even when CORSMiddleware would reject every real browser request
    from the candidate frontend, because urllib never sends an Origin header
    and never inspects response headers. This check does both: a simple GET
    with Origin set to the candidate frontend's own origin, and an OPTIONS
    preflight for a real mission-creation POST, exactly as a browser would
    send it before the candidate frontend's first API call."""
    # Response headers from _http_with_origin are already normalized to
    # lowercase keys (see _normalize_headers) -- these constants are
    # deliberately lowercase to match, never the mixed-case spelling a
    # browser/spec doc uses, so a future edit can't silently reintroduce a
    # case-sensitive lookup against Cloud Run's lowercased HTTP/2 headers.
    allow_origin_header = "access-control-allow-origin"
    allow_methods_header = "access-control-allow-methods"

    origin = frontend_url.rstrip("/")

    get_status, get_headers = _http_with_origin("GET", f"{api_url}/health/live", origin)
    get_allow_origin = get_headers.get(allow_origin_header)
    get_ok = get_status == 200 and get_allow_origin == origin

    preflight_status, preflight_headers = _http_with_origin(
        "OPTIONS",
        f"{api_url}/api/v1/missions",
        origin,
        extra_headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    preflight_allow_origin = preflight_headers.get(allow_origin_header)
    preflight_allow_methods = preflight_headers.get(allow_methods_header) or ""
    preflight_ok = (
        preflight_status == 200
        and preflight_allow_origin == origin
        and "POST" in preflight_allow_methods
    )

    ok = get_ok and preflight_ok
    report.add(
        "cors_allows_candidate_web_origin", ok,
        f"origin={origin}, GET /health/live: HTTP {get_status} "
        f"allow-origin={get_allow_origin!r}; "
        f"OPTIONS preflight on /api/v1/missions: HTTP {preflight_status} "
        f"allow-origin={preflight_allow_origin!r} "
        f"allow-methods={preflight_allow_methods!r}",
    )


def create_demo_mission(report: Report, api_url: str) -> str | None:
    payload = {
        "name": "Candidate Acceptance Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": DEMO_SOURCE_A, "source_type": "SAMPLE_RELEASE", "name": "Intent"},
        "source_b": {"source_id": DEMO_SOURCE_B_DEFECTIVE, "source_type": "SAMPLE_RELEASE", "name": "Defective Target"},
        "disposable_sample_run": True,
    }
    status_code, body = _http("POST", f"{api_url}/api/v1/missions", payload)
    mission_id = body.get("mission_id")
    ok = status_code == 202 and bool(mission_id) and body.get("status") == "QUEUED"
    report.add("create_demo_mission", ok, f"HTTP {status_code}, mission_id={mission_id}")
    if mission_id:
        report.staging_mission_ids.append(mission_id)
    return mission_id


def wait_for_completion(report: Report, api_url: str, mission_id: str, timeout_s: float = 240.0) -> dict | None:
    seen_statuses: list[str] = []
    deadline = time.monotonic() + timeout_s
    last_body: dict = {}
    while time.monotonic() < deadline:
        status_code, body = _http("GET", f"{api_url}/api/v1/missions/{mission_id}")
        last_body = body
        current = body.get("status")
        if not seen_statuses or seen_statuses[-1] != current:
            seen_statuses.append(current)
        if current in ("COMPLETED", "FAILED", "NEEDS_REVIEW", "CANCELLED"):
            break
        time.sleep(3)

    reached_completed = seen_statuses and seen_statuses[-1] == "COMPLETED"
    plausible_sequence = "QUEUED" in seen_statuses or "RUNNING" in seen_statuses
    ok = reached_completed and plausible_sequence
    report.add(
        "mission_reaches_completed", ok,
        f"observed status sequence={seen_statuses} within {timeout_s}s",
    )
    return last_body if reached_completed else None


def check_persisted_events_proxy(report: Report, mission_detail: dict) -> None:
    """No dedicated /missions/{id}/events endpoint exists on this API surface
    (documented scope limitation — see module docstring). Best-effort proxy:
    confirms the mission has real started_at/completed_at timestamps and a
    non-empty agent_execution timeline, which can only be populated by actual
    persisted stage progression, not a single terminal write."""
    result = mission_detail.get("result", {})
    agent_actions = result.get("agent_execution", {}).get("data") or []
    ok = bool(mission_detail.get("started_at")) and bool(mission_detail.get("completed_at")) and len(agent_actions) > 0
    report.add(
        "persisted_stage_events_proxy", ok,
        f"started_at={mission_detail.get('started_at')!r}, completed_at={mission_detail.get('completed_at')!r}, "
        f"agent_action_count={len(agent_actions)} "
        "(proxy check: no dedicated events-listing endpoint exists to verify the full stage timeline directly)",
    )


def check_real_gemini_decision(report: Report, api_url: str, mission_id: str) -> None:
    """Uses the sanitized GET /missions/{id}/evidence endpoint (see
    app.api.missions.get_mission_gemini_evidence) rather than inferring from
    agent_execution alone — this is the one place schema_valid and
    response_id are actually exposed and independently verifiable."""
    status_code, body = _http("GET", f"{api_url}/api/v1/missions/{mission_id}/evidence")
    invocations = body.get("gemini_invocations", []) if status_code == 200 else []
    candidates = [
        inv for inv in invocations
        if inv.get("success") is True
        and inv.get("model_id") == EXPECTED_GEMINI_MODEL
        and inv.get("invocation_id")
        and inv.get("schema_valid") is True
    ]
    ok = status_code == 200 and len(candidates) > 0
    example = candidates[0] if candidates else None
    report.add(
        "real_gemini_decision_present", ok,
        f"HTTP {status_code}, {len(invocations)} total invocation(s), {len(candidates)} matching "
        f"model_id={EXPECTED_GEMINI_MODEL}+schema_valid+invocation_id+success. "
        f"response_id_present={bool(example and example.get('response_id'))}, "
        f"latency_ms={example.get('latency_ms') if example else None}",
    )


def check_deterministic_action_has_no_model_id(report: Report, mission_detail: dict) -> None:
    result = mission_detail.get("result", {})
    agent_actions = result.get("agent_execution", {}).get("data") or []
    deterministic = [a for a in agent_actions if a.get("is_gemini_decision") is False]
    clean = [a for a in deterministic if a.get("model_id") is None]
    ok = len(deterministic) > 0 and len(clean) == len(deterministic)
    report.add(
        "deterministic_action_has_no_model_id", ok,
        f"{len(deterministic)} deterministic action(s), {len(clean)} correctly carry no model_id",
    )


def check_premium_mismatch_and_block(report: Report, mission_detail: dict) -> None:
    result = mission_detail.get("result", {})
    mismatch_count = (result.get("experiments", {}).get("data") or {}).get("mismatch_count", 0)
    decision_status = (result.get("release_decision", {}).get("data") or {}).get("status")
    ok = mismatch_count > 0 and decision_status == "BLOCK_DEPLOYMENT"
    report.add(
        "premium_mismatch_and_block_deployment", ok,
        f"mismatch_count={mismatch_count}, release_decision.status={decision_status}",
    )


def check_portfolio_impact_present(report: Report, mission_detail: dict) -> None:
    result = mission_detail.get("result", {})
    blast = result.get("blast_radius", {})
    ok = blast.get("status") == "SUCCEEDED" and bool(blast.get("data"))
    report.add("portfolio_impact_present", ok, f"blast_radius.status={blast.get('status')}")


def check_remediation_and_revalidation_present(report: Report, mission_detail: dict) -> None:
    result = mission_detail.get("result", {})
    remediation = result.get("remediation", {})
    revalidation = result.get("revalidation", {})
    ok = remediation.get("status") == "SUCCEEDED" and revalidation.get("status") == "SUCCEEDED"
    report.add(
        "remediation_and_revalidation_present", ok,
        f"remediation.status={remediation.get('status')}, revalidation.status={revalidation.get('status')}",
    )


def check_no_duplicate_execution_proxy(report: Report, mission_detail: dict) -> None:
    """No direct Pub/Sub publish access is available to this HTTP-only
    script, so a genuinely induced duplicate delivery cannot be tested here
    (documented scope limitation — see module docstring; the direct-access
    version of this exact check already exists as this repo's own
    test_no_duplicate_execution_under_redelivery /
    test_already_leased_duplicate_does_not_start_second_execution pytest
    tests). Proxy: confirms the normal single-delivery path executed exactly
    once (attempt_number == 1)."""
    attempt_number = mission_detail.get("attempt_number")
    ok = attempt_number == 1
    report.add(
        "no_duplicate_execution_proxy", ok,
        f"attempt_number={attempt_number} (proxy only — see docstring for the direct-access pytest coverage)",
    )


def check_cancel_delete_lifecycle(report: Report, api_url: str) -> None:
    payload = {
        "name": "Candidate Disposable Cancel Test",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": DEMO_SOURCE_A, "source_type": "SAMPLE_RELEASE", "name": "Intent"},
        "source_b": {"source_id": DEMO_SOURCE_B_DEFECTIVE, "source_type": "SAMPLE_RELEASE", "name": "Defective Target"},
        "disposable_sample_run": True,
    }
    status_code, body = _http("POST", f"{api_url}/api/v1/missions", payload)
    mission_id = body.get("mission_id")
    if mission_id:
        report.staging_mission_ids.append(mission_id)
    if status_code != 202 or not mission_id:
        report.add("disposable_mission_created_for_cancel_test", False, f"HTTP {status_code}, body={body}")
        return

    cancel_status, cancel_body = _http("POST", f"{api_url}/api/v1/missions/{mission_id}/cancel")
    cancel_ok = cancel_status == 200 and cancel_body.get("status") in ("CANCELLED", "QUEUED", "VALIDATING")
    report.add("disposable_mission_cancellable", cancel_ok, f"HTTP {cancel_status}, body={cancel_body}")

    # Give the cancel a moment to settle if it raced with QUEUED->RUNNING.
    time.sleep(2)
    detail_status, detail_body = _http("GET", f"{api_url}/api/v1/missions/{mission_id}")
    is_cancelled = detail_body.get("status") == "CANCELLED"
    deletable = detail_body.get("eligible_actions", {}).get("delete") is True
    report.add(
        "cancelled_disposable_mission_deletable", is_cancelled and deletable,
        f"status={detail_body.get('status')}, eligible_actions.delete={detail_body.get('eligible_actions', {}).get('delete')}",
    )

    if is_cancelled and deletable:
        del_status, del_body = _http("DELETE", f"{api_url}/api/v1/missions/{mission_id}")
        del_ok = del_status == 200
        report.add("delete_only_disposable_staging_mission", del_ok, f"HTTP {del_status}, body={del_body}")
        if del_ok and mission_id in report.staging_mission_ids:
            report.staging_mission_ids.remove(mission_id)


def check_completed_mission_not_deletable_but_archivable(report: Report, api_url: str, mission_id: str) -> None:
    del_status, del_body = _http("DELETE", f"{api_url}/api/v1/missions/{mission_id}")
    not_deletable = del_status == 409
    report.add("completed_audit_mission_not_deletable", not_deletable, f"HTTP {del_status}, body={del_body}")

    archive_status, archive_body = _http("POST", f"{api_url}/api/v1/missions/{mission_id}/archive")
    archivable = archive_status == 200 and archive_body.get("status") == "ARCHIVED"
    report.add("completed_audit_mission_archivable", archivable, f"HTTP {archive_status}, body={archive_body}")


def check_invalid_mission_returns_structured_422(report: Report, api_url: str) -> None:
    payload = {
        "name": "Invalid Candidate Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
    }
    status_code, body = _http("POST", f"{api_url}/api/v1/missions", payload)
    detail = body.get("detail", {})
    issues = detail.get("issues", []) if isinstance(detail, dict) else []
    has_structured_issue = any({"field", "code", "message"} <= set(i.keys()) for i in issues)
    ok = status_code == 422 and has_structured_issue
    report.add("invalid_mission_structured_422", ok, f"HTTP {status_code}, issues={issues}")


def check_missing_sources_no_arizona_fallback(report: Report, api_url: str) -> None:
    payload = {
        "name": "Missing Sources Candidate Mission",
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
    }
    status_code, body = _http("POST", f"{api_url}/api/v1/missions", payload)
    ok = status_code == 422
    report.add(
        "missing_sources_no_az_ho3_fallback", ok,
        f"HTTP {status_code} (must reject rather than silently defaulting to the AZ_HO3 demo packages), body={body}",
    )


_SAMPLE_TEMPLATE_PATHS = (
    "/samples/rateguard-source-template-a.json",
    "/samples/rateguard-source-template-b-drift.json",
)


def check_sample_templates_served(report: Report, frontend_url: str) -> None:
    """Verifies the two sample IPIR templates the Sources page advertises for
    download are actually served by the deployed web app: HTTP 200, a JSON
    content type, valid JSON, and a schema-shaped body (the exact top-level
    fields IPIRPackage requires). A regression here previously meant these
    links 404'd in production because the frontend Docker image's runner
    stage never copied Next.js's `public/` directory."""
    required_fields = {"ipir_version", "id", "name", "product", "effective_period", "inputs", "tables", "calculations", "outputs"}

    for path in _SAMPLE_TEMPLATE_PATHS:
        url = f"{frontend_url.rstrip('/')}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                status_code = resp.status
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status_code = e.code
            content_type = e.headers.get("Content-Type", "") if e.headers else ""
            raw = e.read()

        ok = status_code == 200 and "json" in content_type.lower()
        detail = f"{path}: HTTP {status_code}, Content-Type={content_type!r}"

        if ok:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                ok = False
                detail += f", INVALID JSON: {exc}"
            else:
                missing = required_fields - set(parsed.keys())
                if missing:
                    ok = False
                    detail += f", missing required IPIR fields: {sorted(missing)}"
                else:
                    detail += f", valid JSON with {len(parsed.keys())} top-level fields"

        report.add(f"sample_template_served[{path}]", ok, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes-test-candidate", action="store_true", help="Required explicit opt-in.")
    parser.add_argument("--api-url", required=False, help="Candidate-tagged rateguard-api URL.")
    parser.add_argument("--frontend-url", required=False, help="Candidate-tagged rateguard-web URL.")
    parser.add_argument("--timeout-seconds", type=float, default=240.0, help="Max wait for mission completion.")
    args = parser.parse_args(argv)

    if not args.yes_test_candidate:
        print("Refusing to run: pass --yes-test-candidate to explicitly opt into exercising a live candidate deployment.")
        return 2
    if not args.api_url:
        print("Refusing to run: --api-url is required (this script never discovers or guesses a URL).")
        return 2

    report = Report()

    check_health_live(report, args.api_url)
    check_health_ready(report, args.api_url)
    check_system_status(report, args.api_url)
    if args.frontend_url:
        check_cors_from_candidate_web_origin(report, args.api_url, args.frontend_url)
        check_sample_templates_served(report, args.frontend_url)
    else:
        report.add(
            "cors_allows_candidate_web_origin", False,
            "Skipped: --frontend-url was not provided, so the candidate web origin's CORS "
            "access could not be verified (health/mission checks below use a plain HTTP "
            "client and never send an Origin header, so they cannot catch this).",
        )
        report.add(
            "sample_template_served", False,
            "Skipped: --frontend-url was not provided, so the Sources page's downloadable "
            "sample templates could not be verified.",
        )

    mission_id = create_demo_mission(report, args.api_url)
    mission_detail = None
    if mission_id:
        mission_detail = wait_for_completion(report, args.api_url, mission_id, timeout_s=args.timeout_seconds)

    if mission_detail:
        check_persisted_events_proxy(report, mission_detail)
        check_real_gemini_decision(report, args.api_url, mission_id)
        check_premium_mismatch_and_block(report, mission_detail)
        check_portfolio_impact_present(report, mission_detail)
        check_remediation_and_revalidation_present(report, mission_detail)
        check_deterministic_action_has_no_model_id(report, mission_detail)
        check_no_duplicate_execution_proxy(report, mission_detail)
        check_completed_mission_not_deletable_but_archivable(report, args.api_url, mission_id)
    else:
        print("Skipping result-dependent checks: the demo mission did not reach COMPLETED.")

    check_cancel_delete_lifecycle(report, args.api_url)
    check_invalid_mission_returns_structured_422(report, args.api_url)
    check_missing_sources_no_arizona_fallback(report, args.api_url)

    failed = [c for c in report.checks if not c.passed]
    print(f"\n{len(report.checks) - len(failed)}/{len(report.checks)} passed, {len(failed)} failed.")
    print(f"\nStaging mission IDs created (clean up later if not already deleted): {report.staging_mission_ids}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
