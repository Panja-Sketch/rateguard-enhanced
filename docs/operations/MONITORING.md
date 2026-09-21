# Monitoring, alerting and cost controls

Provisioned by `infrastructure/monitoring/setup_monitoring.py` (idempotent; operator `gcloud` identity).

**Log-based metrics** (low cardinality only — labels `decision`, `status`, `code`, `decision_type`; never tenant,
mission, user or policy identifiers): `rg_mission_decision`, `rg_mission_failed`, `rg_connector_request_failed`,
`rg_impact_finished`, `rg_impact_coverage_pct` (distribution), `rg_impact_batch_duration_ms` (distribution),
`rg_impact_retry_exhausted`, `rg_impact_stalled`, `rg_gemini_fallback`, `rg_ratelimit_storage_failure`,
`rg_unauthorized_internal_requests`, `rg_evidence_bundle_failed`. Worker duration/5xx and DLQ backlog use native
Cloud Run / Pub/Sub metrics.

**Dashboard:** "RateGuard operations" (14 tiles). **Alert policies:** repeated mission failures, connector error
rate, DLQ message present, worker 5xx, impact job stalled, rate-limit backend unavailable, abnormal API latency —
all notify channel "RateGuard operator email".

**Budget:** `rateguard-enhanced $25 monthly` (project-scoped, alerts at 50 %, 80 %, 100 % current spend and
100 % forecasted). *Budget alerts do not cap spending.* The pre-existing account-wide "$10 Monthly Budget Alert"
was left untouched (it is not project-scoped).

**Manual step:** an email notification channel created through the API may need the recipient to click the
verification link Google sends before notifications are delivered.

Alert response: mission failures → check worker logs (`SUPERVISOR_FAILED`), Firestore/Vertex health; connector
errors → rating engine health + IAM invoker bindings; DLQ → inspect `*-dead-letter-sub`, batches are idempotent
so republishing the message is safe; stalled impact → worker capacity (`--max-instances`, concurrency) and the
`impact-batches-worker-sub` backlog.
