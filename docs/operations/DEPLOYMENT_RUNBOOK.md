# Deployment runbook (Cloud Run, immutable digests)

Project `rateguard-enhanced`, region `us-central1`, Artifact Registry
`us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images`. `infrastructure/deploy_release.sh`
implements every step; commands below are what it runs.

1. **Preconditions** — clean worktree at the commit to release; tests green (`backend`: ruff, pytest with
   the Firestore emulator and `RATEGUARD_REQUIRE_EMULATOR=1`; `frontend`: typecheck, lint, jest, build,
   playwright; `python scripts/secret_scan.py`); Firestore rules tests (`infrastructure/firestore-rules-tests`).
2. **One-time resources** (idempotent): `infrastructure/setup_impact_pubsub.sh` (topic `impact-batches`,
   push subscription `impact-batches-worker-sub` → worker `/internal/pubsub/impact-batch` with OIDC as
   `rateguard-worker-sa`, DLQ `impact-batches-dead-letter`, 5 attempts, 10–600 s backoff);
   `gcloud firestore fields ttls update expires_at --collection-group=batches --enable-ttl`;
   `python infrastructure/monitoring/setup_monitoring.py`.
3. **Build** — `deploy_release.sh build`: three Cloud Builds (`backend/cloudbuild.yaml` → api+worker image,
   `backend/rating_engine/cloudbuild.yaml`, `frontend/cloudbuild.yaml`), each tagged with the **full git SHA**
   (never `latest`); digests recorded in `infrastructure/.release-digests.env`.
4. **Stage** — `deploy_release.sh stage`: deploys **by digest** with `--no-traffic --tag p8rc`, records the
   previous revision names in `.previous-revisions.env`, sets `RATEGUARD_GIT_SHA` / `RATEGUARD_IMAGE_DIGEST`
   (surfaced in evidence bundles). Health-check each tagged URL; confirm private services reject
   unauthenticated calls (403) and that the tagged rating engine answers `GET /capabilities` with an ID token.
5. **Shift** — `deploy_release.sh shift 25|50|100` (rating engine first is implied by the order: engine,
   worker, api, web). Verify a live mission at each step; 100% only after the gates pass.
6. **Rollback** — `deploy_release.sh rollback` (see `ROLLBACK_DR_RUNBOOK.md`).

Compatibility rule: Firestore/GCS changes in a release must be backward compatible with the previous
revision (additive collections/fields only). Prompt 8 adds `impact_jobs` (+`batches` TTL), never mutating
existing documents, so the previous revisions run unchanged.
