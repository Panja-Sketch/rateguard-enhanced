# Deployment runbook (Cloud Run, immutable digests)

Project `rateguard-enhanced`, region `us-central1`, Artifact Registry
`us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images`. The canonical
candidate → promotion flow is `infrastructure/deploy_candidate_enhanced.sh` (build +
deploy a no-traffic, `--tag candidate` revision and verify it) followed by
`infrastructure/promote_candidate_to_production.sh` (shift production traffic to the
tested candidate); commands below are what those scripts run.

1. **Preconditions** — clean worktree at the commit to release; tests green (`backend`: ruff, pytest with
   the Firestore emulator and `RATEGUARD_REQUIRE_EMULATOR=1`; `frontend`: typecheck, lint, jest, build,
   playwright; `python scripts/secret_scan.py`); Firestore rules tests (`infrastructure/firestore-rules-tests`).
2. **One-time resources** (idempotent): `infrastructure/setup_impact_pubsub.sh` (topic `impact-batches`,
   push subscription `impact-batches-worker-sub` → worker `/internal/pubsub/impact-batch` with OIDC as
   `rateguard-worker-sa`, DLQ `impact-batches-dead-letter`, 5 attempts, 10–600 s backoff);
   `gcloud firestore fields ttls update expires_at --collection-group=batches --enable-ttl`;
   `python infrastructure/monitoring/setup_monitoring.py`.
3. **Build + stage candidate** — `deploy_candidate_enhanced.sh --deploy-candidate`: builds the four images
   tagged with the **full git SHA** (never `latest`) and deploys them as `--no-traffic --tag candidate`
   Cloud Run revisions wired to the same production Firestore/GCS/BigQuery resources production already uses.
4. **Verify candidate** — `deploy_candidate_enhanced.sh --verify-candidate`: exercises the tagged URLs
   through a disposable, auto-restoring Pub/Sub subscription (never the real one); confirm private services
   reject unauthenticated calls (403) and the tagged rating engine answers `GET /capabilities` with an ID token.
5. **Promote** — `promote_candidate_to_production.sh --promote`: shifts production traffic to the
   already-tested candidate revision (byte-for-byte what was verified), rebuilds only the web image (its
   API URL is a build-time constant), and updates the API's CORS origin. Verify a live mission after promotion.
6. **Rollback** — `infrastructure/rollback.sh --rollback ...` (see `ROLLBACK_DR_RUNBOOK.md`).

Compatibility rule: Firestore/GCS changes in a release must be backward compatible with the previous
revision (additive collections/fields only). Prompt 8 adds `impact_jobs` (+`batches` TTL), never mutating
existing documents, so the previous revisions run unchanged.
