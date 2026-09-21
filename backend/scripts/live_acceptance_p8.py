#!/usr/bin/env python3
"""Live (deployed) acceptance harness for Prompt 8.

Creates TEMPORARY Firebase users through the Admin SDK (operator ADC), keeps
their credentials only in an ignored temp file (never printed, never committed),
signs in through the public Firebase REST endpoint and drives the deployed API.
`teardown` disables + deletes nothing else: it disables the temporary users and
removes the credentials file.

    python backend/scripts/live_acceptance_p8.py setup   --creds <file>
    python backend/scripts/live_acceptance_p8.py mission --creds <file> --engine defective-v1 --workbook canonical
    python backend/scripts/live_acceptance_p8.py teardown --creds <file>

Synthetic/de-identified data only. Prints ids, statuses and aggregate numbers - never tokens/passwords.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx

PROJECT = "rateguard-enhanced"
API = os.environ.get("RG_API_URL", "https://rateguard-api-nwhotixfva-uc.a.run.app")
WEB_ORIGIN = "https://rateguard-web-nwhotixfva-uc.a.run.app"
ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = ROOT / "data" / "samples" / "workbook_v1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _web_api_key() -> str:
    for line in (ROOT / "frontend" / ".env.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("NEXT_PUBLIC_FIREBASE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("public Firebase web key not found in frontend/.env.local")


def _load(creds: Path) -> dict:
    return json.loads(creds.read_text(encoding="utf-8"))


def cmd_setup(args) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import firebase_admin
    from firebase_admin import auth, credentials

    from app.auth.directory import FirestoreUserDirectory
    from app.auth.models import Role, UserRecord

    firebase_admin.initialize_app(credentials.ApplicationDefault(), options={"projectId": PROJECT})
    directory = FirestoreUserDirectory(PROJECT)
    tag = secrets.token_hex(3)
    users = {}
    for label, tenant, role in (("a", "rateguard-demo", Role.ADMIN), ("b", f"p8-tenant-b-{tag}", Role.ADMIN),
                                ("viewer", "rateguard-demo", Role.VIEWER)):
        email = f"rg-p8-{label}-{tag}@example.com"
        password = secrets.token_urlsafe(24)
        user = auth.create_user(email=email, password=password, email_verified=True)
        directory.put_user(UserRecord(uid=user.uid, tenant_id=tenant, role=role, disabled=False, email=email))
        users[label] = {"uid": user.uid, "email": email, "password": password, "tenant": tenant}
    args.creds.write_text(json.dumps(users), encoding="utf-8")
    print("temporary users created:", {k: v["tenant"] for k, v in users.items()}, "(credentials in the ignored file)")


def _sign_in(user: dict) -> str:
    r = httpx.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={_web_api_key()}",
        json={"email": user["email"], "password": user["password"], "returnSecureToken": True}, timeout=30,
    )
    r.raise_for_status()
    return r.json()["idToken"]


def cmd_teardown(args) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import firebase_admin
    from firebase_admin import auth, credentials

    from app.auth.directory import FirestoreUserDirectory
    from app.auth.models import UserRecord

    firebase_admin.initialize_app(credentials.ApplicationDefault(), options={"projectId": PROJECT})
    directory = FirestoreUserDirectory(PROJECT)
    for label, u in _load(args.creds).items():
        auth.update_user(u["uid"], disabled=True)
        rec = directory.get_user(u["uid"])
        if rec:
            directory.put_user(UserRecord(uid=rec.uid, tenant_id=rec.tenant_id, role=rec.role, disabled=True, email=rec.email))
        print("disabled:", label)
    args.creds.unlink()
    print("credentials file removed")


class Client:
    def __init__(self, user: dict) -> None:
        self.h = {"Authorization": f"Bearer {_sign_in(user)}", "Origin": WEB_ORIGIN}
        self.c = httpx.Client(base_url=API, timeout=120)

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path, **kw):
        return self.c.post(path, headers=self.h, **kw)


def upload_compile(cl: Client, kind: str) -> tuple[str, str]:
    path = WORKBOOK / kind / "AZ_HO3_GOLDEN_workbook.xlsx"
    up = cl.post("/api/v1/sources", files={"file": (path.name, io.BytesIO(path.read_bytes()), XLSX)})
    up.raise_for_status()
    sid = up.json()["source_id"]
    comp = cl.post(f"/api/v1/sources/{sid}/compile")
    comp.raise_for_status()
    return sid, comp.json()["ipir_package_id"]


def create_mission(cl: Client, sid: str, pid: str, engine: str, name: str) -> str:
    payload = {
        "name": name, "mode": "RELEASE_CONFORMANCE", "product": "az_ho3", "jurisdiction": "Arizona",
        "effective_period_start": "2026-10-01",
        "source_a": {"source_id": sid, "source_type": "FILE", "name": "Controlled Workbook", "compiled_package_id": pid},
        "source_b": {"source_id": "rating-engine-demo", "source_type": "API_CONNECTOR", "name": "Connector",
                     "connector_id": "rating-engine-demo", "engine_version": engine},
        "disposable_sample_run": True,
    }
    r = cl.post("/api/v1/missions", json=payload)
    r.raise_for_status()
    return r.json()["mission_id"]


def wait(cl: Client, mid: str, timeout: int = 1500) -> dict:
    start, last = time.time(), None
    while time.time() - start < timeout:
        r = cl.get(f"/api/v1/missions/{mid}")
        r.raise_for_status()
        d = r.json()
        imp = cl.get(f"/api/v1/missions/{mid}/impact").json()
        prog = imp.get("progress") or {}
        line = (d["status"], d.get("current_stage"), imp.get("status"), prog.get("batches_done"), prog.get("batches_total"))
        if line != last:
            print(f"  [{int(time.time() - start):>4}s] {line}")
            last = line
        if d["status"] in ("COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"):
            return d
        time.sleep(5)
    raise SystemExit("timeout waiting for mission")


def summarize(cl: Client, mid: str, d: dict) -> dict:
    res = d["result"]
    dec = (res.get("release_decision") or {}).get("data") or {}
    exp = ((res.get("experiments") or {}).get("data")) or {}
    imp = cl.get(f"/api/v1/missions/{mid}/impact").json()
    agg = imp.get("aggregate") or {}
    out = {
        "mission_id": mid, "status": d["status"], "decision": dec.get("status"),
        "blocking_reasons": [x[:200] for x in dec.get("blocking_reasons", [])],
        "probes": {k: exp.get(k) for k in ("total_executed", "match_count", "mismatch_count", "inconclusive_count")},
        "impact_status": imp.get("status"), "job_id": imp.get("job_id"),
        "impact": {k: agg.get(k) for k in (
            "status", "impact_decision", "completeness", "exposure_is_lower_bound", "eligible_policies",
            "out_of_scope_policies", "successful_comparisons", "mismatches", "inconclusive", "unprocessed_policies",
            "coverage_pct", "overcharge_count", "undercharge_count", "undercharge_total", "overcharge_total",
            "signed_net_delta", "absolute_exposure", "mean_abs_delta", "median_abs_delta", "min_delta", "max_delta",
            "batch_count", "batches_done", "retry_count", "request_count", "result_sha256", "error_classes",
            "budget_exhausted", "halt_reason")},
        "pipeline": (agg.get("pipeline_impact") or {}).get("buckets"),
        "cohorts": len(((agg.get("cohort_distribution") or {}).get("cohorts")) or []),
        "batch_wall_seconds": (agg.get("budget") or {}).get("wall_seconds"),
    }
    return out


def cmd_mission(args) -> None:
    users = _load(args.creds)
    cl = Client(users["a"])
    sid, pid = upload_compile(cl, args.workbook)
    started = time.time()
    mid = create_mission(cl, sid, pid, args.engine, args.name or f"P8 {args.engine}")
    print("mission:", mid, "source:", sid)
    d = wait(cl, mid)
    out = summarize(cl, mid, d)
    out["total_seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=2, default=str))
    if args.bundle:
        b = cl.get(f"/api/v1/missions/{mid}/evidence/bundle")
        Path(args.bundle).write_bytes(b.content)
        print("bundle:", b.status_code, len(b.content), "bytes; manifest sha256:", b.headers.get("x-manifest-sha256"),
              "bundle sha256:", b.headers.get("x-bundle-sha256"))


def cmd_security(args) -> None:
    users = _load(args.creds)
    a, b, v = Client(users["a"]), Client(users["b"]), Client(users["viewer"])
    mid = args.mission
    results = {
        "own tenant reads mission": a.get(f"/api/v1/missions/{mid}").status_code,
        "own tenant reads impact": a.get(f"/api/v1/missions/{mid}/impact").status_code,
        "cross-tenant mission (expect 404)": b.get(f"/api/v1/missions/{mid}").status_code,
        "cross-tenant impact (expect 404)": b.get(f"/api/v1/missions/{mid}/impact").status_code,
        "cross-tenant bundle (expect 404)": b.get(f"/api/v1/missions/{mid}/evidence/bundle").status_code,
        "viewer reads mission": v.get(f"/api/v1/missions/{mid}").status_code,
        "viewer bundle (expect 403)": v.get(f"/api/v1/missions/{mid}/evidence/bundle").status_code,
        "viewer create mission (expect 403)": v.post("/api/v1/missions", json={}).status_code,
        "no token (expect 401)": httpx.get(f"{API}/api/v1/missions/{mid}", timeout=30).status_code,
        "worker via public API url (expect 404)": httpx.post(f"{API}/internal/pubsub/impact-batch", json={}, timeout=30).status_code,
    }
    r = httpx.options(f"{API}/api/v1/me", headers={"Origin": WEB_ORIGIN, "Access-Control-Request-Method": "GET"}, timeout=30)
    results["CORS allowed origin"] = r.headers.get("access-control-allow-origin")
    r = httpx.options(f"{API}/api/v1/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}, timeout=30)
    results["CORS foreign origin (expect none)"] = r.headers.get("access-control-allow-origin")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "teardown", "mission", "security"):
        p = sub.add_parser(name)
        p.add_argument("--creds", type=Path, required=True)
        if name == "mission":
            p.add_argument("--engine", default="canonical-v1")
            p.add_argument("--workbook", default="canonical")
            p.add_argument("--name")
            p.add_argument("--bundle")
        if name == "security":
            p.add_argument("--mission", required=True)
    a = ap.parse_args()
    {"setup": cmd_setup, "teardown": cmd_teardown, "mission": cmd_mission, "security": cmd_security}[a.cmd](a)
