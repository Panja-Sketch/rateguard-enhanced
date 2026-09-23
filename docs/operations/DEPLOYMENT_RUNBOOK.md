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
4. **Verify candidate** — `deploy_candidate_enhanced.sh --verify-candidate`: creates a SHA-scoped, fully
   isolated ephemeral verification topic+subscription (never a subscription on the shared production topic,
   which would fan out to the real production worker too), retargets the candidate API's
   `RATEGUARD_PUBSUB_TOPIC` at it for the duration of the run, exercises the candidate end to end with a
   synthetic tenant, and proves the *candidate* worker (not production) processed it via the evidence
   bundle's `deployment.json.cloud_run_revision`/`git_sha`. The Pub/Sub push OIDC audience is always the
   worker's **stable, untagged** URL — the proven-working form (see `infrastructure/setup_impact_pubsub.sh`),
   never a `--tag` URL. Cleanup (ephemeral topic/subscription deletion + restoring the candidate API's
   production topic) always runs, success or failure, and never deletes the acceptance evidence. Once the
   manual/CI checks pass, run `deploy_candidate_enhanced.sh --record-verified` to resolve and pin the
   verified image digests (`infrastructure/.candidate-verified/<sha>.evidence`).
5. **Promote** — `promote_candidate_to_production.sh --promote`: refuses to proceed unless the
   candidate-tagged revisions still match the pinned verified digests, reuses those exact digests
   unchanged (no rebuild, including web), repoints worker/api at the production Pub/Sub topic, the
   **stable** production rating-engine URL, and the production CORS origin, and deploys the web image with
   `RATEGUARD_API_URL` set as **deployment-time runtime config** (a plain Cloud Run env var, never baked
   into the image — see `frontend/src/lib/runtimeConfig.ts`) rather than rebuilding it. Verifies Pub/Sub
   routing (endpoint + OIDC audience both stable, non-tag) after promotion. Optional
   `--auto-rollback-on-failure` invokes `rollback.sh` on a failed post-promotion check (which still requires
   its own explicit `--rollback` confirmation). Verify a live mission after promotion.
6. **Rollback** — `infrastructure/rollback.sh --rollback ...` (see `ROLLBACK_DR_RUNBOOK.md`).

Compatibility rule: Firestore/GCS changes in a release must be backward compatible with the previous
revision (additive collections/fields only). Prompt 8 adds `impact_jobs` (+`batches` TTL), never mutating
existing documents, so the previous revisions run unchanged.
