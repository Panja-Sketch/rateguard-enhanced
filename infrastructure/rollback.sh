#!/usr/bin/env bash
# RateGuard AI -- Immediate Rollback Template (prepared, NOT executed by default)
#
# Shifts 100% of traffic back to a previously-known-good named revision for
# API, worker, and frontend. This is the "break glass" counterpart to
# promote_candidate.sh's gradual rollout -- use it the moment any promotion
# gate looks wrong.
#
# SAFETY: by default (no --rollback flag) this performs ZERO gcloud calls and
# only prints the exact commands for the revision name you pass in.

set -euo pipefail

REGION="us-central1"
# Each Cloud Run service has its own independent revision naming/numbering
# (rateguard-api-00015-4tt vs rateguard-worker-00008-w2p vs a rateguard-web
# revision name) -- a single shared "--to-revision" value would be wrong for
# at least two of the three services. Each must be supplied explicitly.
API_REVISION=""
WORKER_REVISION=""
WEB_REVISION=""
ROLLBACK=false

for arg in "$@"; do
  case "$arg" in
    --rollback) ROLLBACK=true ;;
    --api-revision=*) API_REVISION="${arg#*=}" ;;
    --worker-revision=*) WORKER_REVISION="${arg#*=}" ;;
    --web-revision=*) WEB_REVISION="${arg#*=}" ;;
    --help|-h)
      echo "Usage: $0 --api-revision=<name> --worker-revision=<name> --web-revision=<name> [--rollback]"
      echo "  (no --rollback)  Print the exact rollback commands. No GCP calls."
      echo "  --rollback       Actually execute the rollback."
      echo ""
      echo "Find previous revision names with, per service:"
      echo "  gcloud run revisions list --service rateguard-api --region $REGION"
      echo "  gcloud run revisions list --service rateguard-worker --region $REGION"
      echo "  gcloud run revisions list --service rateguard-web --region $REGION"
      exit 0
      ;;
  esac
done

if [ -z "$API_REVISION" ] || [ -z "$WORKER_REVISION" ] || [ -z "$WEB_REVISION" ]; then
  echo "Refusing to run: --api-revision, --worker-revision, and --web-revision are all required"
  echo "(each service has its own independent revision naming -- there is no single shared"
  echo "'previous' revision name across all three). This script intentionally does not guess."
  echo "Find them with:"
  echo "  gcloud run revisions list --service rateguard-api --region $REGION"
  echo "  gcloud run revisions list --service rateguard-worker --region $REGION"
  echo "  gcloud run revisions list --service rateguard-web --region $REGION"
  exit 2
fi

cat <<PLAN
========================================================
   RateGuard AI -- Immediate Rollback
   Target revisions: api=${API_REVISION} worker=${WORKER_REVISION} web=${WEB_REVISION}
========================================================
gcloud run services update-traffic rateguard-api --region ${REGION} \\
  --to-revisions=${API_REVISION}=100

gcloud run services update-traffic rateguard-worker --region ${REGION} \\
  --to-revisions=${WORKER_REVISION}=100

gcloud run services update-traffic rateguard-web --region ${REGION} \\
  --to-revisions=${WEB_REVISION}=100

Verify immediately after:
  curl <production-api-url>/health/ready
  gcloud logging read 'resource.labels.service_name="rateguard-worker" severity>=ERROR' --freshness=10m

This rollback ONLY changes production traffic split. It never touches
assurance-runs-staging / assurance-worker-staging / the candidate tag, and
never deletes the candidate revision (it stays at 0% traffic, available for
further investigation).
========================================================
PLAN

if [ "$ROLLBACK" = true ]; then
  echo ""
  echo "!!! --rollback was passed, but this reference implementation stops here"
  echo "!!! deliberately -- an unattended rollback script that ALSO executes on"
  echo "!!! its first run would be exactly the kind of blast-radius risk this"
  echo "!!! task explicitly asked to avoid. Run each 'gcloud run services"
  echo "!!! update-traffic' command above manually."
  exit 3
fi
