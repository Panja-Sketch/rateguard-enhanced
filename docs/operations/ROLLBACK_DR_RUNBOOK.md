# Rollback and disaster-recovery runbook

## Application rollback (non-destructive)

* Identify previous images: `gcloud run revisions describe <PREV_REVISION> --region us-central1 --format='value(spec.containers[0].image)'`
  (digest form). Previous production revisions before Prompt 8: worker `rateguard-worker-00003-96v`, api
  `rateguard-api-00004-hwh`, rating engine `rateguard-rating-engine-00003-bn5`, web `rateguard-web-00003-tbr`.
* Traffic rollback: `gcloud run services update-traffic <service> --region us-central1 --to-revisions <PREV>=100`
  (all four services; `infrastructure/rollback.sh --rollback ...`). Revisions are never deleted.
  `promote_candidate_to_production.sh --promote --auto-rollback-on-failure` prints and invokes this same
  `rollback.sh` command automatically if its own post-promotion web-bundle/Pub/Sub-routing check fails —
  `rollback.sh` still requires its own explicit `--rollback` confirmation and only prints the `gcloud`
  commands rather than executing them (see the note at the bottom of that script).
* **Data compatibility:** the new release only *adds* the `impact_jobs` collection. Old revisions ignore it. Old
  missions/evidence stay readable by new and old revisions (doubled `CONNECTOR_CONNECTOR_` codes are normalised on read).
* **Pub/Sub:** if the worker is rolled back while `impact-batches` messages are in flight, the old worker has no
  `/internal/pubsub/impact-batch` route → 404 → Pub/Sub retries with backoff and, after 5 attempts, dead-letters to
  `impact-batches-dead-letter`. Batch results are checkpointed and idempotent, so redelivery after restoring the new
  revision resumes the job without double counting (proven in `tests/impact`, and in the live drill recorded in STATUS.md).
* **Candidate verification left half-done (crash, lost terminal, abandoned test):** the candidate API/worker still
  carry the *temporary* mission/impact topic names and the temporary topics/subscriptions still exist. Production is
  unaffected (they only ever receive candidate traffic), but the candidate must not be promoted in that state.
  Check out the SHA that was prepared and run `infrastructure/deploy_candidate_enhanced.sh --abort-verification`:
  it restores the recorded original env values from `infrastructure/.candidate-verified/<sha>.pending.json`,
  deletes only the SHA-scoped `assurance-runs-candidate-verify-<sha12>` / `impact-batches-candidate-verify-<sha12>`
  topics and subscriptions, and confirms `assurance-runs-worker-sub` / `impact-batches-worker-sub` are unchanged
  (it exits non-zero with a "SUSPECT" warning if they are). It is safe to repeat. Temporary subscriptions also
  self-expire after 2 days. If the pending file is lost, restore by hand:
  `gcloud run services update rateguard-api --region us-central1 --no-traffic --tag candidate --update-env-vars RATEGUARD_PUBSUB_TOPIC=assurance-runs,RATEGUARD_IMPACT_TOPIC=impact-batches`,
  the same for `rateguard-worker` with `RATEGUARD_IMPACT_TOPIC=impact-batches`, then delete the four temporary
  resources by name. Never delete `assurance-runs`, `impact-batches` or their production subscriptions.
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
