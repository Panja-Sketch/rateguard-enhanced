# Rollback and disaster-recovery runbook

## Application rollback (non-destructive)

* Identify previous images: `gcloud run revisions describe <PREV_REVISION> --region us-central1 --format='value(spec.containers[0].image)'`
  (digest form). Previous production revisions before Prompt 8: worker `rateguard-worker-00003-96v`, api
  `rateguard-api-00004-hwh`, rating engine `rateguard-rating-engine-00003-bn5`, web `rateguard-web-00003-tbr`.
* Traffic rollback: `gcloud run services update-traffic <service> --region us-central1 --to-revisions <PREV>=100`
  (all four services; `infrastructure/rollback.sh --rollback ...`). Revisions are never deleted.
* **Data compatibility:** the new release only *adds* the `impact_jobs` collection. Old revisions ignore it. Old
  missions/evidence stay readable by new and old revisions (doubled `CONNECTOR_CONNECTOR_` codes are normalised on read).
* **Pub/Sub:** if the worker is rolled back while `impact-batches` messages are in flight, the old worker has no
  `/internal/pubsub/impact-batch` route → 404 → Pub/Sub retries with backoff and, after 5 attempts, dead-letters to
  `impact-batches-dead-letter`. Batch results are checkpointed and idempotent, so redelivery after restoring the new
  revision resumes the job without double counting (proven in `tests/impact`, and in the live drill recorded in STATUS.md).
* **IAM rollback** commands are in `docs/security/IAM_INVENTORY.md` (per role). They are independent of the app rollback.
* **Credential revocation is NOT reversible and NOT part of application rollback.** The deleted Firebase Admin
  key and disabled secret versions were never used by any revision; the app authenticates with ADC only.

## Disaster recovery

| Asset | Recovery |
|---|---|
| Cloud Run services | redeploy by digest from Artifact Registry (`deploy_candidate_enhanced.sh` + `promote_candidate_to_production.sh`), images are immutable |
| Firestore (runs, evidence, `impact_jobs`) | managed export/PITR (enable point-in-time recovery for production); `impact_jobs` batches are reconstructible by re-running the mission |
| GCS artifacts (sources, evidence bundles) | bucket versioning + `gsutil`/`gcloud storage` restore of tenant-prefixed objects |
| Pub/Sub | recreate with `infrastructure/setup_impact_pubsub.sh`; DLQ subscription retains undelivered messages |
| Monitoring | `python infrastructure/monitoring/setup_monitoring.py` (idempotent) |

DR drill (non-disruptive): stage the previous images with a `dr` tag (`--no-traffic`), confirm health and a
mission through the tagged URLs, then remove the tag — production traffic is never moved. The Prompt 8 drill
result is recorded in `docs/implementation/STATUS.md`.
