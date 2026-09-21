#!/usr/bin/env python3
"""Idempotently provisions RateGuard monitoring: log-based metrics, an email
notification channel, alert policies, a dashboard and a $25 project budget.

Uses the operator's gcloud identity (`gcloud auth print-access-token`) against
the Monitoring / Logging / Billing Budgets REST APIs. No secrets are read or
printed. Metric labels are deliberately low-cardinality (decision, status, code,
decision_type) - never tenant, mission, user or policy identifiers.

    python infrastructure/monitoring/setup_monitoring.py [--email you@example.com]

Budget alerts do NOT cap spending; they only notify.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

PROJECT = "rateguard-enhanced"
PROJECT_NUMBER = "316435199506"
BILLING_ACCOUNT = "016A9D-BC0A62-2370A3"
WORKER = "rateguard-worker"
API = "rateguard-api"


def _token() -> str:
    return subprocess.run("gcloud auth print-access-token", shell=True, capture_output=True, text=True, check=True).stdout.strip()


def call(method: str, url: str, body: dict | None = None, ok_missing: bool = False):
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json",
                 "x-goog-user-project": PROJECT},
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return {"_exists": True}
        if ok_missing and e.code == 404:
            return None
        print(f"HTTP {e.code} for {method} {url}: {e.read().decode()[:400]}", file=sys.stderr)
        raise


# --- log-based metrics ---------------------------------------------------------------------------

WORKER_LOGS = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{WORKER}"'
API_LOGS = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{API}"'


def _counter(name, desc, flt, label=None, regex=None, label_desc=None):
    m = {"name": name, "description": desc, "filter": flt,
         "metricDescriptor": {"metricKind": "DELTA", "valueType": "INT64", "unit": "1", "labels": []}}
    if label:
        m["metricDescriptor"]["labels"] = [{"key": label, "valueType": "STRING", "description": label_desc or label}]
        m["labelExtractors"] = {label: f'REGEXP_EXTRACT(textPayload, "{regex}")'}
    return m


def _distribution(name, desc, flt, regex, unit, bounds):
    return {
        "name": name, "description": desc, "filter": flt,
        "metricDescriptor": {"metricKind": "DELTA", "valueType": "DISTRIBUTION", "unit": unit},
        "valueExtractor": f'REGEXP_EXTRACT(textPayload, "{regex}")',
        "bucketOptions": {"explicitBuckets": {"bounds": bounds}},
    }


METRICS = [
    _counter("rg_mission_decision", "Missions completed, by release decision.",
             f'{WORKER_LOGS} AND textPayload:"MISSION_DECISION decision="', "decision", r"decision=(\\w+)"),
    _counter("rg_mission_failed", "Mission executions that raised an unhandled failure.",
             f'{WORKER_LOGS} AND textPayload:"SUPERVISOR_FAILED"'),
    _counter("rg_connector_request_failed", "Failed connector requests, by stable error code.",
             f'{WORKER_LOGS} AND textPayload:"connector_request_failed"', "code", r"code=(\\w+)"),
    _counter("rg_impact_finished", "Connector impact scans finished, by status.",
             f'{WORKER_LOGS} AND textPayload:"IMPACT_FINISHED status="', "status", r"status=(\\w+)"),
    _distribution("rg_impact_coverage_pct", "Portfolio coverage of finished impact scans (%).",
                  f'{WORKER_LOGS} AND textPayload:"IMPACT_FINISHED status="', r"coverage_pct=([0-9.]+)", "%",
                  [50, 75, 90, 99, 99.99, 100]),
    _distribution("rg_impact_batch_duration_ms", "Impact batch execution time (ms).",
                  f'{WORKER_LOGS} AND textPayload:"IMPACT_BATCH_DONE"', r"duration_ms=([0-9]+)", "ms",
                  [250, 500, 1000, 2500, 5000, 15000, 60000, 240000]),
    _counter("rg_impact_retry_exhausted", "Impact batches that exhausted their attempts.",
             f'{WORKER_LOGS} AND textPayload:"IMPACT_RETRY_EXHAUSTED"'),
    _counter("rg_impact_stalled", "Impact scans with no batch progress.",
             f'{WORKER_LOGS} AND textPayload:"IMPACT_STALLED"'),
    _counter("rg_gemini_fallback", "Deterministic fallbacks used instead of a Gemini decision.",
             f'{WORKER_LOGS} AND textPayload:"GEMINI_FALLBACK decision_type="', "decision_type", r"decision_type=(\\w+)"),
    _counter("rg_ratelimit_storage_failure", "Rate-limit backend (Firestore) unavailable.",
             f'({API_LOGS}) AND (textPayload:"RATE_LIMIT_UNAVAILABLE" OR textPayload:"event=storage_unavailable")'),
    _counter("rg_unauthorized_internal_requests", "Rejected (401/403) requests to the private worker.",
             f'resource.type="cloud_run_revision" AND resource.labels.service_name="{WORKER}" '
             'AND logName:"run.googleapis.com%2Frequests" AND httpRequest.status>=401 AND httpRequest.status<=403'),
    _counter("rg_evidence_bundle_failed", "Evidence bundle generation failures.",
             f'{API_LOGS} AND textPayload:"EVIDENCE_BUNDLE_FAILED"'),
]


def ensure_metrics() -> None:
    base = f"https://logging.googleapis.com/v2/projects/{PROJECT}/metrics"
    for m in METRICS:
        existing = call("GET", f"{base}/{m['name']}", ok_missing=True)
        if existing is None:
            call("POST", base, m)
            print("metric created:", m["name"])
        else:
            call("PUT", f"{base}/{m['name']}", m)
            print("metric updated:", m["name"])


# --- notification channel ------------------------------------------------------------------------

def ensure_channel(email: str) -> str:
    base = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/notificationChannels"
    for ch in call("GET", base).get("notificationChannels", []):
        if ch.get("labels", {}).get("email_address") == email:
            print("channel exists:", ch["name"], ch.get("verificationStatus"))
            return ch["name"]
    ch = call("POST", base, {"type": "email", "displayName": "RateGuard operator email",
                             "labels": {"email_address": email}, "enabled": True})
    print("channel created:", ch["name"], ch.get("verificationStatus"))
    return ch["name"]


# --- alert policies --------------------------------------------------------------------------------

def _cond_logs(name, metric, threshold, window, comparison="COMPARISON_GT", extra=""):
    return {
        "displayName": name,
        "conditionThreshold": {
            "filter": f'metric.type="logging.googleapis.com/user/{metric}" AND resource.type="cloud_run_revision"{extra}',
            "comparison": comparison, "thresholdValue": threshold, "duration": "0s",
            "aggregations": [{"alignmentPeriod": window, "perSeriesAligner": "ALIGN_SUM",
                              "crossSeriesReducer": "REDUCE_SUM"}],
            "trigger": {"count": 1},
        },
    }


def policies(channel: str) -> list[dict]:
    def pol(name, cond, doc):
        return {"displayName": name, "combiner": "OR", "conditions": [cond], "enabled": True,
                "notificationChannels": [channel],
                "documentation": {"content": doc, "mimeType": "text/markdown"}}

    return [
        pol("RateGuard: repeated mission failures", _cond_logs("mission failures >= 3 / 15m", "rg_mission_failed", 2, "900s"),
            "Three or more mission executions raised unhandled failures in 15 minutes. See docs/operations/RUNBOOK_MONITORING.md."),
        pol("RateGuard: connector error rate", _cond_logs("connector failures > 25 / 10m", "rg_connector_request_failed", 25, "600s"),
            "Connector requests are failing at an elevated rate. Check rating-engine health and IAM."),
        pol("RateGuard: DLQ message present", {
            "displayName": "DLQ backlog > 0",
            "conditionThreshold": {
                "filter": 'metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages" AND resource.type="pubsub_subscription" '
                          'AND (resource.labels.subscription_id="impact-batches-dead-letter-sub" OR resource.labels.subscription_id="assurance-runs-dead-letter-sub")',
                "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "60s",
                "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_MAX"}],
            }}, "A message reached a dead-letter queue. Inspect the DLQ subscription; batch handling is idempotent."),
        pol("RateGuard: worker 5xx", {
            "displayName": "worker 5xx > 5 / 5m",
            "conditionThreshold": {
                "filter": f'metric.type="run.googleapis.com/request_count" AND resource.type="cloud_run_revision" '
                          f'AND resource.labels.service_name="{WORKER}" AND metric.labels.response_code_class="5xx"',
                "comparison": "COMPARISON_GT", "thresholdValue": 5, "duration": "0s",
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM",
                                  "crossSeriesReducer": "REDUCE_SUM"}],
            }}, "The worker is returning 5xx (Pub/Sub will redeliver). Check Firestore/connector health."),
        pol("RateGuard: impact job stalled", _cond_logs("impact stalled", "rg_impact_stalled", 0, "300s"),
            "An impact scan made no batch progress. Check worker capacity and the impact-batches subscription."),
        pol("RateGuard: rate-limit backend unavailable", _cond_logs("rate-limit storage failures", "rg_ratelimit_storage_failure", 0, "300s"),
            "Rate limiting fails closed when Firestore counters are unavailable; users see 503s."),
        pol("RateGuard: abnormal API latency", {
            "displayName": "API p95 latency > 5s",
            "conditionThreshold": {
                "filter": f'metric.type="run.googleapis.com/request_latencies" AND resource.type="cloud_run_revision" '
                          f'AND resource.labels.service_name="{API}"',
                "comparison": "COMPARISON_GT", "thresholdValue": 5000, "duration": "300s",
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_PERCENTILE_95"}],
            }}, "The public API's p95 latency is above 5 seconds for 5 minutes."),
    ]


def ensure_policies(channel: str) -> None:
    base = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/alertPolicies"
    existing = {p["displayName"]: p["name"] for p in call("GET", base).get("alertPolicies", [])}
    for p in policies(channel):
        if p["displayName"] in existing:
            call("PATCH", f"https://monitoring.googleapis.com/v3/{existing[p['displayName']]}", p)
            print("policy updated:", p["displayName"])
        else:
            call("POST", base, p)
            print("policy created:", p["displayName"])


# --- dashboard --------------------------------------------------------------------------------------

def _xy(title, metric_filter, aligner="ALIGN_RATE", reducer="REDUCE_SUM", group=None, unit=None):
    agg = {"alignmentPeriod": "300s", "perSeriesAligner": aligner, "crossSeriesReducer": reducer}
    if group:
        agg["groupByFields"] = [group]
    return {"title": title, "xyChart": {"dataSets": [{
        "timeSeriesQuery": {"timeSeriesFilter": {"filter": metric_filter, "aggregation": agg}},
        "plotType": "LINE"}], "yAxis": {"scale": "LINEAR"}}}


def dashboard() -> dict:
    u = "logging.googleapis.com/user"
    tiles = [
        _xy("Missions by decision", f'metric.type="{u}/rg_mission_decision"', "ALIGN_SUM", "REDUCE_SUM", "metric.label.decision"),
        _xy("Mission failures", f'metric.type="{u}/rg_mission_failed"', "ALIGN_SUM"),
        _xy("Connector failures by code", f'metric.type="{u}/rg_connector_request_failed"', "ALIGN_SUM", "REDUCE_SUM", "metric.label.code"),
        _xy("Impact scans by status", f'metric.type="{u}/rg_impact_finished"', "ALIGN_SUM", "REDUCE_SUM", "metric.label.status"),
        _xy("Impact batch duration p95 (ms)", f'metric.type="{u}/rg_impact_batch_duration_ms"', "ALIGN_PERCENTILE_95", "REDUCE_MAX"),
        _xy("Portfolio coverage p50 (%)", f'metric.type="{u}/rg_impact_coverage_pct"', "ALIGN_PERCENTILE_50", "REDUCE_MIN"),
        _xy("Impact retry exhaustion / stalls", f'metric.type="{u}/rg_impact_retry_exhausted"', "ALIGN_SUM"),
        _xy("Gemini fallbacks", f'metric.type="{u}/rg_gemini_fallback"', "ALIGN_SUM", "REDUCE_SUM", "metric.label.decision_type"),
        _xy("Rate-limit storage failures", f'metric.type="{u}/rg_ratelimit_storage_failure"', "ALIGN_SUM"),
        _xy("Rejected internal requests", f'metric.type="{u}/rg_unauthorized_internal_requests"', "ALIGN_SUM"),
        _xy("Worker request latency p95", f'metric.type="run.googleapis.com/request_latencies" AND resource.labels.service_name="{WORKER}"',
            "ALIGN_PERCENTILE_95", "REDUCE_MAX"),
        _xy("Worker 5xx", f'metric.type="run.googleapis.com/request_count" AND resource.labels.service_name="{WORKER}" AND metric.labels.response_code_class="5xx"',
            "ALIGN_SUM"),
        _xy("DLQ backlog", 'metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages" AND '
            '(resource.labels.subscription_id=monitoring.regex.full_match(".*dead-letter-sub"))', "ALIGN_MAX", "REDUCE_MAX"),
        _xy("Evidence bundle failures", f'metric.type="{u}/rg_evidence_bundle_failed"', "ALIGN_SUM"),
    ]
    grid = [{"xPos": (i % 3) * 16, "yPos": (i // 3) * 12, "width": 16, "height": 12, "widget": t} for i, t in enumerate(tiles)]
    return {"displayName": "RateGuard operations", "mosaicLayout": {"columns": 48, "tiles": grid}}


def ensure_dashboard() -> None:
    base = f"https://monitoring.googleapis.com/v1/projects/{PROJECT}/dashboards"
    for d in call("GET", base).get("dashboards", []):
        if d.get("displayName") == "RateGuard operations":
            call("DELETE", f"https://monitoring.googleapis.com/v1/{d['name']}")
    created = call("POST", base, dashboard())
    print("dashboard:", created.get("name"))


# --- budget ---------------------------------------------------------------------------------------------

def ensure_budget(channel: str | None) -> None:
    base = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT}/budgets"
    name = "rateguard-enhanced $25 monthly"
    for b in call("GET", base).get("budgets", []):
        if b.get("displayName") == name:
            print("budget exists:", b["name"])
            return
    body = {
        "displayName": name,
        "budgetFilter": {"projects": [f"projects/{PROJECT_NUMBER}"], "calendarPeriod": "MONTH",
                         "creditTypesTreatment": "INCLUDE_ALL_CREDITS"},
        "amount": {"specifiedAmount": {"currencyCode": "USD", "units": "25"}},
        "thresholdRules": [
            {"thresholdPercent": 0.5, "spendBasis": "CURRENT_SPEND"},
            {"thresholdPercent": 0.8, "spendBasis": "CURRENT_SPEND"},
            {"thresholdPercent": 1.0, "spendBasis": "CURRENT_SPEND"},
            {"thresholdPercent": 1.0, "spendBasis": "FORECASTED_SPEND"},
        ],
        "notificationsRule": {"disableDefaultIamRecipients": False,
                              **({"monitoringNotificationChannels": [channel]} if channel else {})},
    }
    print("budget created:", call("POST", base, body).get("name"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="panjacharans@gmail.com")
    ap.add_argument("--skip-budget", action="store_true")
    args = ap.parse_args()
    ensure_metrics()
    channel = ensure_channel(args.email)
    ensure_policies(channel)
    ensure_dashboard()
    if not args.skip_budget:
        ensure_budget(channel)
