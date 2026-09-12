#!/usr/bin/env bash
# RateGuard AI -- Production Promotion Plan (prepared, NOT executed by default)
#
# Promotes an already-verified candidate image digest to production traffic
# gradually (10% -> 50% -> 100%), with an explicit health/log observation
# gate between each shift. This is a DELIBERATELY SEPARATE script/command
# from infrastructure/deploy_candidate.sh -- candidate deployment never
# promotes itself, and this script never builds or deploys a new candidate.
#
# SAFETY: by default (no --promote flag) this performs ZERO gcloud calls and
# only prints the plan for the digest you pass in. Passing --promote without
# a concrete --image-digest is refused outright -- there is no "promote
# whatever is latest" mode, specifically so a digest that was never run
# through verify_candidate.py can't be promoted by accident.

set -euo pipefail

PROJECT_ID="rateguard-ai"
REGION="us-central1"
CANDIDATE_TAG="candidate"

IMAGE_DIGEST=""
PROMOTE=false
for arg in "$@"; do
  case "$arg" in
    --promote) PROMOTE=true ;;
    --image-digest=*) IMAGE_DIGEST="${arg#*=}" ;;
    --help|-h)
      echo "Usage: $0 --image-digest=sha256:... [--promote]"
      echo "  (no --promote)  Print the full promotion plan for this digest. No GCP calls."
      echo "  --promote       Actually execute the gradual traffic shift."
      exit 0
      ;;
  esac
done

if [ -z "$IMAGE_DIGEST" ]; then
  echo "Refusing to run: --image-digest=sha256:... is required."
  echo "Use the exact digest reported by 'gcloud run services describe rateguard-api --format=value(status.traffic[?tag==\"candidate\"].imageDigest)'"
  echo "after it has been verified with backend/scripts/verify_candidate.py."
  exit 2
fi

cat <<PLAN
========================================================
   RateGuard AI -- Production Promotion PLAN
   (dry-run unless --promote is also passed)
========================================================
Project:              ${PROJECT_ID}
Region:                ${REGION}
Promoting image digest: ${IMAGE_DIGEST}
(This digest must be the exact one already deployed to the '${CANDIDATE_TAG}'
tag and already exercised successfully by
backend/scripts/verify_candidate.py --yes-test-candidate.)

Traffic shift plan (API, worker, frontend, each independently gated).

  Step 0: resolve concrete revision names (Cloud Run traffic splits are
  addressed by REVISION NAME, not by tag or digest -- each service has its
  own independent revision numbering, so this must be done per service):
    API_CANDIDATE_REV=\$(gcloud run services describe rateguard-api --region ${REGION} \\
      --format="value(status.traffic.filter(tag='${CANDIDATE_TAG}').revisionName)")
    API_PREVIOUS_REV=\$(gcloud run services describe rateguard-api --region ${REGION} \\
      --format="value(status.traffic.filter(percent=100).revisionName)")
    (repeat for rateguard-worker and rateguard-web)

  Step 1: 10% candidate / 90% previous
    gcloud run services update-traffic rateguard-api --region ${REGION} \\
      --to-revisions=\${API_CANDIDATE_REV}=10,\${API_PREVIOUS_REV}=90
    gcloud run services update-traffic rateguard-worker --region ${REGION} \\
      --to-revisions=\${WORKER_CANDIDATE_REV}=10,\${WORKER_PREVIOUS_REV}=90
    gcloud run services update-traffic rateguard-web --region ${REGION} \\
      --to-revisions=\${WEB_CANDIDATE_REV}=10,\${WEB_PREVIOUS_REV}=90
    >>> GATE: observe for at least 15 minutes:
        - gcloud logging read 'resource.labels.service_name="rateguard-worker"
          severity>=ERROR' --freshness=15m
        - gcloud run services describe rateguard-api --region ${REGION}
          --format='value(status.url)' then curl <url>/health/ready
        - Confirm no new "Firestore error" / "RETRYABLE_FAILURE" / "POISON_MESSAGE"
          log lines attributable to the promoted revision.
        - Confirm the Pub/Sub subscription's undelivered-message count is not growing.
    Proceed only if the gate is clean. If not: run rollback.sh immediately
    (pass the *_PREVIOUS_REV values resolved in Step 0 as its --api-revision/
    --worker-revision/--web-revision arguments).

  Step 2: 50% candidate / 50% previous
    gcloud run services update-traffic rateguard-api --region ${REGION} \\
      --to-revisions=\${API_CANDIDATE_REV}=50,\${API_PREVIOUS_REV}=50
    gcloud run services update-traffic rateguard-worker --region ${REGION} \\
      --to-revisions=\${WORKER_CANDIDATE_REV}=50,\${WORKER_PREVIOUS_REV}=50
    gcloud run services update-traffic rateguard-web --region ${REGION} \\
      --to-revisions=\${WEB_CANDIDATE_REV}=50,\${WEB_PREVIOUS_REV}=50
    >>> GATE: same observation as Step 1, minimum 15 minutes.

  Step 3: 100% candidate
    gcloud run services update-traffic rateguard-api --region ${REGION} \\
      --to-revisions=\${API_CANDIDATE_REV}=100
    gcloud run services update-traffic rateguard-worker --region ${REGION} \\
      --to-revisions=\${WORKER_CANDIDATE_REV}=100
    gcloud run services update-traffic rateguard-web --region ${REGION} \\
      --to-revisions=\${WEB_CANDIDATE_REV}=100
    >>> GATE: same observation, minimum 30 minutes before declaring done.

Mandatory verifications before AND after every step:
  - Production Pub/Sub subscription 'assurance-worker' push endpoint still
    points ONLY at the production rateguard-worker URL (never the
    candidate-tagged URL):
      gcloud pubsub subscriptions describe assurance-worker \\
        --format='value(pushConfig.pushEndpoint)'
  - Staging resources remain isolated and untouched by this promotion:
      gcloud pubsub subscriptions describe assurance-worker-staging \\
        --format='value(pushConfig.pushEndpoint)'
    (must still point at the candidate/staging worker URL, unaffected)
  - No command in this script ever modifies assurance-worker-staging,
    assurance-runs-staging, or the RATEGUARD_FIRESTORE_COLLECTION=
    assurance_runs_staging documents.

Post-promotion verification (after Step 3 reaches 100%):
  - Create one real asynchronous production mission end-to-end and confirm
    QUEUED -> RUNNING -> COMPLETED (equivalent to verify_candidate.py's main
    flow, run against the now-promoted PRODUCTION url).
  - Exercise cancel / delete / archive against that one verification mission
    exactly as verify_candidate.py does against staging.
  - Do NOT run the DLQ poison-message test against production (staging-only,
    by design -- see backend/scripts/test_dlq_poison_delivery.py).

Rollback: infrastructure/rollback.sh --to-revision=<previous-revision-name>
(prepared, separate script, template only -- never executed automatically by
this script even on a failed gate).

Explicitly NOT done by this script:
  - No candidate build or deployment (see deploy_candidate.sh).
  - No repair of existing stuck MIS-* missions -- that is a separate
    data-repair operation, deliberately out of scope here.
========================================================
PLAN

if [ "$PROMOTE" = true ]; then
  echo ""
  echo "!!! --promote was passed, but this reference implementation stops here."
  echo "!!! Each 'GATE' above requires a human decision informed by real"
  echo "!!! logs/metrics that cannot be safely automated away. Execute each"
  echo "!!! numbered gcloud block above manually, in order, only after its"
  echo "!!! preceding gate is confirmed clean."
  exit 3
fi
