#!/usr/bin/env bash
# RateGuard AI -- Production-Configured Release Revisions (0% traffic)
#
# Creates NEW rateguard-api / rateguard-worker / rateguard-web revisions,
# tagged "release" at --no-traffic, using the ALREADY-VERIFIED application
# image digest(s) and infrastructure/runtime-env.yaml (the real production
# resource names) -- never `:latest`, never a rebuild, never the candidate
# env file. This is a deliberately separate script from deploy_candidate.sh
# (which builds+deploys against ISOLATED staging resources) and from
# deploy.sh (which builds `:latest` and cuts over 100% traffic immediately --
# unsafe for promoting an already-verified digest). This script never shifts
# traffic; see promote_candidate.sh-style traffic commands for that,
# generated separately once these 0%-traffic revisions pass inspection.
#
# WHY production ended up staging-configured: `deploy.sh` deploys
# `rateguard-api`/`rateguard-worker` at 100% traffic immediately on every
# run, with no digest pinning and no guard against the env file it's handed.
# This script exists specifically so a verified digest can be promoted to a
# 0%-traffic revision, inspected, and only THEN have traffic shifted --
# and it refuses to finish if the deployed revision ever turns out to
# contain a staging resource name (see check_production_config.sh).
#
# The web image is REBUILT here, not reused from any candidate build: Next.js
# bakes NEXT_PUBLIC_RATEGUARD_API_URL into the client JS bundle at `next
# build` time (frontend/Dockerfile ARG/ENV + `npm run build`), so a candidate
# web image built with the candidate-tagged API URL can never correctly serve
# production -- it would call the candidate API forever. This script always
# builds a fresh web image from the CURRENT git commit, substituting the
# production rateguard-api base URL.
#
# SAFETY: by default (no --deploy-release flag) this performs ZERO gcloud/
# network calls beyond the git SHA lookup. It only prints the plan.

set -euo pipefail

PROJECT_ID="rateguard-ai"
REGION="us-central1"
RUNTIME_SA="rateguard-runtime@rateguard-ai.iam.gserviceaccount.com"
RELEASE_TAG="release"
RUNTIME_ENV_FILE="infrastructure/runtime-env.yaml"

GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo 'UNKNOWN_SHA')"

API_WORKER_DIGEST=""
WEB_IMAGE_TAG="release-${GIT_SHA}"
DEPLOY_RELEASE=false
for arg in "$@"; do
  case "$arg" in
    --deploy-release) DEPLOY_RELEASE=true ;;
    --image-digest=*) API_WORKER_DIGEST="${arg#*=}" ;;
    --help|-h)
      echo "Usage: $0 --image-digest=sha256:... [--deploy-release]"
      echo "  (no --deploy-release)  Print the full release plan. No GCP calls."
      echo "  --deploy-release       Actually deploy the 0%-traffic release revisions."
      exit 0
      ;;
  esac
done

if [ -z "$API_WORKER_DIGEST" ]; then
  echo "Refusing to run: --image-digest=sha256:... is required (the already-verified" >&2
  echo "rateguard-api/rateguard-worker digest -- see backend/scripts/verify_candidate.py" >&2
  echo "output for the candidate that was actually exercised)." >&2
  exit 2
fi
case "$API_WORKER_DIGEST" in
  sha256:*) ;;
  *) echo "Refusing to run: --image-digest must be a sha256:... digest, not a tag." >&2; exit 2 ;;
esac

API_WORKER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-api@${API_WORKER_DIGEST}"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-web:${WEB_IMAGE_TAG}"

print_plan() {
  cat <<PLAN
========================================================
   RateGuard AI -- Production Release PLAN (0% traffic)
   (dry-run: no gcloud command below has been executed)
========================================================
Project:                       ${PROJECT_ID}
Region:                         ${REGION}
Verified API/worker digest:    ${API_WORKER_DIGEST}
API/worker image reference:    ${API_WORKER_IMAGE}
Web image (rebuilt, tagged):   ${WEB_IMAGE}
Cloud Run release tag:         ${RELEASE_TAG} (--no-traffic)
Env file:                      ${RUNTIME_ENV_FILE} (production names, unmodified)

NOT done by this script, ever:
  - No rebuild of the API/worker image (the verified digest is reused as-is).
  - No 'gcloud pubsub topics/subscriptions create' -- production Pub/Sub
    (assurance-runs / assurance-worker / assurance-runs-dlq) already exists.
  - No Firestore/BigQuery/GCS resource creation -- production resources
    (assurance_runs collection, rateguard dataset, rateguard-ai-artifacts
    bucket) already exist.
  - No traffic shift ('gcloud run services update-traffic') for any service.
  - No production promotion decision -- this only stages revisions at 0%.

Exact commands --deploy-release would run, in order:

  1) Deploy API release revision (--no-traffic --tag ${RELEASE_TAG}):
     gcloud run deploy rateguard-api \\
       --image ${API_WORKER_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${RELEASE_TAG} \\
       --allow-unauthenticated --service-account ${RUNTIME_SA} \\
       --memory=512Mi --env-vars-file=${RUNTIME_ENV_FILE}

  2) Deploy worker release revision (--no-traffic --tag ${RELEASE_TAG}):
     gcloud run deploy rateguard-worker \\
       --image ${API_WORKER_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${RELEASE_TAG} \\
       --no-allow-unauthenticated --service-account ${RUNTIME_SA} \\
       --memory=1Gi --env-vars-file=${RUNTIME_ENV_FILE}

  3) Guard both new revisions before continuing (aborts the script if either
     one somehow resolves to a staging-named value):
     infrastructure/check_production_config.sh rateguard-api --revision=<new-api-revision>
     infrastructure/check_production_config.sh rateguard-worker --revision=<new-worker-revision>

  4) Rebuild the frontend from the CURRENT commit (${GIT_SHA}), pointed at the
     production API base URL (never a candidate/tagged URL):
     gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${WEB_IMAGE_TAG},_NEXT_PUBLIC_RATEGUARD_API_URL=<production-api-base-url>

  5) Deploy web release revision (--no-traffic --tag ${RELEASE_TAG}):
     gcloud run deploy rateguard-web \\
       --image ${WEB_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${RELEASE_TAG} --allow-unauthenticated

Re-run this plan any time with no arguments (besides --image-digest). Pass
--deploy-release to actually execute it.
PLAN
}

deploy_release() {
  echo "========================================================"
  echo "   RateGuard AI -- Deploying Release Revisions (--deploy-release)"
  echo "   API/worker digest: ${API_WORKER_DIGEST}"
  echo "========================================================"

  gcloud config set project "$PROJECT_ID"

  local py=python3
  if ! "$py" -c "" >/dev/null 2>&1; then
    py=python
  fi

  echo "1. Deploying API release revision (--no-traffic --tag ${RELEASE_TAG})..."
  gcloud run deploy rateguard-api \
    --image "$API_WORKER_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$RELEASE_TAG" \
    --allow-unauthenticated --service-account "$RUNTIME_SA" \
    --memory=512Mi --env-vars-file="$RUNTIME_ENV_FILE"

  local api_traffic_json api_revision
  api_traffic_json=$(gcloud run services describe rateguard-api --region "$REGION" --format="json(status.traffic)")
  api_revision=$(echo "$api_traffic_json" | "$py" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('status', {}).get('traffic', []) or []:
    if t.get('tag') == '${RELEASE_TAG}':
        print(t.get('revisionName', ''))
        break
")
  if [ -z "$api_revision" ]; then
    echo "Error: could not discover the release-tagged revision for rateguard-api. Stopping." >&2
    exit 1
  fi
  echo "   API release revision: ${api_revision}"

  echo "2. Deploying worker release revision (--no-traffic --tag ${RELEASE_TAG})..."
  gcloud run deploy rateguard-worker \
    --image "$API_WORKER_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$RELEASE_TAG" \
    --no-allow-unauthenticated --service-account "$RUNTIME_SA" \
    --memory=1Gi --env-vars-file="$RUNTIME_ENV_FILE"

  local worker_traffic_json worker_revision
  worker_traffic_json=$(gcloud run services describe rateguard-worker --region "$REGION" --format="json(status.traffic)")
  worker_revision=$(echo "$worker_traffic_json" | "$py" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('status', {}).get('traffic', []) or []:
    if t.get('tag') == '${RELEASE_TAG}':
        print(t.get('revisionName', ''))
        break
")
  if [ -z "$worker_revision" ]; then
    echo "Error: could not discover the release-tagged revision for rateguard-worker. Stopping." >&2
    exit 1
  fi
  echo "   Worker release revision: ${worker_revision}"

  echo "3. Guarding both new revisions against staging-named configuration..."
  if ! bash infrastructure/check_production_config.sh rateguard-api --revision="$api_revision"; then
    echo "Error: API release revision '${api_revision}' failed the production config guard. Stopping before frontend build." >&2
    exit 1
  fi
  if ! bash infrastructure/check_production_config.sh rateguard-worker --revision="$worker_revision"; then
    echo "Error: worker release revision '${worker_revision}' failed the production config guard. Stopping before frontend build." >&2
    exit 1
  fi

  PROD_API_URL=$(gcloud run services describe rateguard-api --region "$REGION" --format="value(status.url)")
  echo "   Production API base URL (baked into the web build): ${PROD_API_URL}"

  echo "4. Building release frontend image from current commit (${GIT_SHA})..."
  gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$WEB_IMAGE_TAG",_NEXT_PUBLIC_RATEGUARD_API_URL="$PROD_API_URL"

  echo "5. Deploying web release revision (--no-traffic --tag ${RELEASE_TAG})..."
  gcloud run deploy rateguard-web \
    --image "$WEB_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$RELEASE_TAG" --allow-unauthenticated

  local web_traffic_json web_revision
  web_traffic_json=$(gcloud run services describe rateguard-web --region "$REGION" --format="json(status.traffic)")
  web_revision=$(echo "$web_traffic_json" | "$py" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('status', {}).get('traffic', []) or []:
    if t.get('tag') == '${RELEASE_TAG}':
        print(t.get('revisionName', ''))
        break
")

  echo "========================================================"
  echo "RELEASE REVISIONS DEPLOYED (0% production traffic)"
  echo "API release revision:      ${api_revision}"
  echo "Worker release revision:   ${worker_revision}"
  echo "Web release revision:      ${web_revision}"
  echo "API/worker image digest:   ${API_WORKER_DIGEST}"
  echo "Web image:                 ${WEB_IMAGE}"
  echo ""
  echo "No traffic has been shifted. Review, then use targeted"
  echo "'gcloud run services update-traffic' commands to promote deliberately."
  echo "========================================================"
}

if [ "$DEPLOY_RELEASE" = true ]; then
  deploy_release
else
  print_plan
fi
