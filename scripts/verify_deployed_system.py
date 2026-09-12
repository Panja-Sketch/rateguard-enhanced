#!/usr/bin/env python3
"""Non-destructive acceptance verification script for RateGuard AI deployments."""

import argparse
import sys
import time
import httpx


def poll_mission_until_terminal(
    base_url: str,
    mission_id: str,
    timeout_seconds: float = 300.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    """Polls GET /api/v1/missions/{mission_id} until terminal state or timeout with detailed diagnostic logging."""
    start_time = time.time()
    last_status = "QUEUED"
    last_stage = "QUEUED"
    last_http_status = None
    last_heartbeat = None
    consecutive_404s = 0
    poll_count = 0
    seen_stages = set()

    print(f"    [*] Polling mission '{mission_id}'...")

    while time.time() - start_time < timeout_seconds:
        poll_count += 1
        try:
            r = httpx.get(f"{base_url}/api/v1/missions/{mission_id}", timeout=10.0)
            last_http_status = r.status_code

            if r.status_code == 404:
                consecutive_404s += 1
                if consecutive_404s >= 5:
                    print(f"    [✗] MISSION_PERSISTENCE_FAILURE: Mission '{mission_id}' returned 404 five times after POST 202!")
                    return {
                        "status": "MISSION_PERSISTENCE_FAILURE",
                        "workflow_stage": "NOT_FOUND",
                        "decision": "PERSISTENCE_FAILURE",
                        "mission_id": mission_id,
                        "error": "Mission record was not persisted to durable storage before HTTP 202 response",
                    }
            elif r.status_code == 200:
                consecutive_404s = 0
                data = r.json()
                status_val = (data.get("status") or "QUEUED").upper()
                stage_val = data.get("workflow_stage") or "RUNNING"
                meta = data.get("metadata") or {}
                last_heartbeat = meta.get("last_heartbeat_at") or data.get("updated_at")

                stage_key = f"{status_val}:{stage_val}"
                if stage_key not in seen_stages:
                    seen_stages.add(stage_key)
                    print(f"    [→] Lifecycle Transition: Status={status_val} | Stage={stage_val} | Heartbeat={last_heartbeat}")

                last_status = status_val
                last_stage = stage_val

                if last_status in ("COMPLETED", "FAILED", "NEEDS_REVIEW", "CANCELLED", "ARCHIVED"):
                    decision = data.get("decision")
                    result_obj = data.get("result") or {}
                    if not decision and isinstance(result_obj, dict):
                        rel_dec = result_obj.get("release_decision") or {}
                        dec_data = rel_dec.get("data") or {}
                        decision = dec_data.get("status")

                    return {
                        "status": last_status,
                        "workflow_stage": last_stage,
                        "decision": decision or "UNKNOWN",
                        "mission_id": mission_id,
                        "data": data,
                    }
        except Exception as err:
            print(f"    [!] Polling network warning for {mission_id}: {err}")

        time.sleep(poll_interval)

    print(f"    [✗] TIMEOUT DIAGNOSTIC: mission_id={mission_id}, last_http={last_http_status}, last_status={last_status}, last_stage={last_stage}, heartbeat={last_heartbeat}, total_polls={poll_count}")

    return {
        "status": "TIMEOUT",
        "workflow_stage": last_stage,
        "decision": "TIMEOUT",
        "mission_id": mission_id,
        "error": f"Client timed out after {timeout_seconds}s (last status: {last_status}, stage: {last_stage}, heartbeat: {last_heartbeat})",
    }


def run_smoke_tests(base_url: str) -> bool:
    print(f"[*] Running smoke acceptance checks against {base_url}...")
    success = True

    # 1. Health Probe
    try:
        r = httpx.get(f"{base_url}/health", timeout=10.0)
        if r.status_code == 200:
            print("  [✓] /health returned 200 OK")
        else:
            print(f"  [✗] /health returned {r.status_code}")
            success = False
    except Exception as e:
        print(f"  [✗] /health failed: {e}")
        success = False

    # 2. System Info Probe
    try:
        r = httpx.get(f"{base_url}/api/v1/system/info", timeout=10.0)
        if r.status_code == 200:
            model_id = r.json().get("configured_model", "")
            print(f"  [✓] /api/v1/system/info returned 200 OK (Model: {model_id})")
            if model_id != "gemini-3.7-flash":
                print(f"  [✗] Configured model is '{model_id}', expected 'gemini-3.7-flash'")
                success = False
        else:
            print(f"  [✗] /api/v1/system/info returned {r.status_code}")
            success = False
    except Exception as e:
        print(f"  [✗] /api/v1/system/info failed: {e}")
        success = False

    # 3. List Missions
    try:
        r = httpx.get(f"{base_url}/api/v1/missions?limit=5", timeout=10.0)
        if r.status_code == 200:
            print("  [✓] /api/v1/missions returned 200 OK")
        else:
            print(f"  [✗] /api/v1/missions returned {r.status_code}")
            success = False
    except Exception as e:
        print(f"  [✗] List missions failed: {e}")
        success = False

    return success


def run_full_tests(base_url: str, timeout_seconds: float = 300.0, poll_interval: float = 2.0) -> bool:
    if not run_smoke_tests(base_url):
        return False

    print("\n[*] Running full end-to-end async mission verification tests...")
    success = True

    # Flow A: Clean PASS Mission
    try:
        clean_req = {
            "name": "Acceptance Test Clean Mission",
            "mode": "RELEASE_CONFORMANCE",
            "product": "AZ_HO3",
            "jurisdiction": "Arizona",
            "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"},
            "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Clean Target"},
            "disposable_sample_run": True,
        }
        r = httpx.post(f"{base_url}/api/v1/missions", json=clean_req, timeout=10.0)
        if r.status_code in (200, 202):
            m_id = r.json()["mission_id"]
            print(f"  [✓] Clean mission accepted HTTP 202 (Mission ID: {m_id}). Polling completion...")
            res = poll_mission_until_terminal(base_url, m_id, timeout_seconds, poll_interval)
            dec = res.get("decision")
            status_val = res.get("status")
            print(f"  [✓] Clean mission finished with status '{status_val}', decision '{dec}'")
            if dec != "PASS" and status_val != "COMPLETED":
                print(f"  [✗] Expected PASS/COMPLETED, got {dec}/{status_val}")
                success = False
        else:
            print(f"  [✗] Clean mission submission returned HTTP {r.status_code}: {r.text[:150]}")
            success = False
    except Exception as e:
        print(f"  [✗] Clean mission failed: {e}")
        success = False

    # Flow B: Defective BLOCK Mission
    try:
        block_req = {
            "name": "Acceptance Test Block Mission",
            "mode": "RELEASE_CONFORMANCE",
            "product": "AZ_HO3",
            "jurisdiction": "Arizona",
            "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"},
            "source_b": {"source_id": "AZ_HO3_2026_09_DEFECTIVE", "source_type": "SAMPLE_RELEASE", "name": "Defective Target"},
            "disposable_sample_run": True,
        }
        r = httpx.post(f"{base_url}/api/v1/missions", json=block_req, timeout=10.0)
        if r.status_code in (200, 202):
            m_id = r.json()["mission_id"]
            print(f"  [✓] Defective mission accepted HTTP 202 (Mission ID: {m_id}). Polling completion...")
            res = poll_mission_until_terminal(base_url, m_id, timeout_seconds, poll_interval)
            dec = res.get("decision")
            status_val = res.get("status")
            print(f"  [✓] Defective mission finished with status '{status_val}', decision '{dec}'")
            if dec != "BLOCK_DEPLOYMENT" and status_val != "NEEDS_REVIEW":
                print(f"  [✗] Expected BLOCK_DEPLOYMENT / NEEDS_REVIEW, got {dec}/{status_val}")
                success = False
        else:
            print(f"  [✗] Defective mission submission returned HTTP {r.status_code}: {r.text[:150]}")
            success = False
    except Exception as e:
        print(f"  [✗] Defective mission failed: {e}")
        success = False

    # Flow C: Symmetric Equivalence Mission (A->B and B->A must agree)
    try:
        equiv_req_ab = {
            "name": "Acceptance Test Equivalence Mission (A to B)",
            "mode": "EQUIVALENCE",
            "product": "AZ_HO3",
            "jurisdiction": "Arizona",
            "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Source A"},
            "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Source B"},
            "disposable_sample_run": True,
        }
        equiv_req_ba = {
            **equiv_req_ab,
            "name": "Acceptance Test Equivalence Mission (B to A)",
            "source_a": equiv_req_ab["source_b"],
            "source_b": equiv_req_ab["source_a"],
        }
        decisions: dict[str, str | None] = {}
        for label, req in (("A->B", equiv_req_ab), ("B->A", equiv_req_ba)):
            r = httpx.post(f"{base_url}/api/v1/missions", json=req, timeout=10.0)
            if r.status_code not in (200, 202):
                print(f"  [✗] Equivalence {label} submission returned HTTP {r.status_code}: {r.text[:150]}")
                success = False
                continue
            m_id = r.json()["mission_id"]
            print(f"  [✓] Equivalence {label} mission accepted HTTP 202 (Mission ID: {m_id}). Polling completion...")
            res = poll_mission_until_terminal(base_url, m_id, timeout_seconds, poll_interval)
            decisions[label] = res.get("decision")
            status_val = res.get("status")
            print(f"  [✓] Equivalence {label} mission finished with status '{status_val}', decision '{decisions[label]}'")
            if decisions[label] != "PASS" and status_val != "COMPLETED":
                print(f"  [✗] Expected PASS/COMPLETED, got {decisions[label]}/{status_val}")
                success = False

        if decisions.get("A->B") and decisions.get("B->A") and decisions["A->B"] != decisions["B->A"]:
            print(f"  [✗] Equivalence not symmetric: A->B={decisions['A->B']} but B->A={decisions['B->A']}")
            success = False
    except Exception as e:
        print(f"  [✗] Equivalence mission failed: {e}")
        success = False

    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="RateGuard AI Deployment Acceptance Harness")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of RateGuard API service")
    parser.add_argument("--smoke-only", action="store_true", help="Run only smoke probes")
    parser.add_argument("--mission-timeout-seconds", type=float, default=300.0, help="Timeout seconds per mission")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    if args.smoke_only:
        ok = run_smoke_tests(base_url)
    else:
        ok = run_full_tests(base_url, timeout_seconds=args.mission_timeout_seconds)

    if ok:
        print("\n========================================================")
        print("   ACCEPTANCE VERIFICATION PASSED")
        print("========================================================")
        sys.exit(0)
    else:
        print("\n========================================================")
        print("   ACCEPTANCE VERIFICATION FAILED")
        print("========================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()
