#!/usr/bin/env bash
# RateGuard Enhanced -- Promote an already-deployed, already-tested candidate
# revision to production traffic (project: rateguard-enhanced).
#
# deploy_candidate_enhanced.sh deliberately builds every service wired to
# ISOLATED STAGING resources (Firestore collection, GCS bucket, BigQuery
# dataset, Pub/Sub topic) so candidate testing can never touch production
# data. That means a plain `gcloud run services update-traffic` promotion is
# NOT enough on its own -- shifting traffic alone leaves the now-100%-traffic
# revision still pointed at staging Firestore/GCS/BigQuery/Pub/Sub, and
# leaves rateguard-web's Next.js bundle still calling the candidate-tagged
# API URL (a build-time constant, not a runtime env var, so it can't be
# fixed with --update-env-vars). This script closes both gaps, plus the API
# CORS origin (also candidate-scoped by deploy_candidate_enhanced.sh).
#
# Prerequisite: a candidate has already been built and deployed via
# `deploy_candidate_enhanced.sh --deploy-candidate` and tested at its
# --tag candidate URLs. This script never builds rating-engine/worker/api
# images itself -- it reuses whatever image is already running on the
# service's `candidate`-tagged revision, so what gets promoted is byte-for-
# byte what was tested. It DOES rebuild the web image, because the
# production API URL must be baked in at build time.
#
# SAFETY: by default (no --promote flag) this performs ZERO gcloud/network
# calls -- it only inspects current revisions (via `gcloud run services
# describe`, read-only) and prints the full plan.

set -euo pipefail

PROJECT_ID="rateguard-enhanced"
REGION="us-central1"

PROD_TOPIC="assurance-runs"
PROD_SUBSCRIPTION="assurance-runs-worker-sub"
PROD_FIRESTORE_COLLECTION="assurance_runs"
PROD_BIGQUERY_DATASET="rateguard_portfolio"
PROD_BIGQUERY_PORTFOLIO_TABLE="synthetic_policies"
PROD_BIGQUERY_RESULTS_TABLE="portfolio_exposure_results"
PROD_GCS_BUCKET="rateguard-enhanced-artifacts"
PROD_WEB_URL="https://rateguard-web-nwhotixfva-uc.a.run.app"
PROD_API_URL="https://rateguard-api-nwhotixfva-uc.a.run.app"

FIREBASE_WEB_KEYS="API_KEY AUTH_DOMAIN PROJECT_ID STORAGE_BUCKET MESSAGING_SENDER_ID APP_ID MEASUREMENT_ID"
firebase_web_value() {
  local key="NEXT_PUBLIC_FIREBASE_$1" val
  val="${!key:-}"
  if [ -z "$val" ] && [ -f frontend/.env.local ]; then
    val="$(grep -E "^${key}=" frontend/.env.local | head -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
  fi
  printf '%s' "$val"
}

# Dependency-free lookup: parse `status.traffic` text output for the
# revision name currently under the `candidate` tag.
candidate_revision() {
  gcloud run services describe "$1" --region "$REGION" --project "$PROJECT_ID" \
    --format="value(status.traffic)" 2>/dev/null \
    | tr ';' '\n' | grep "'tag': 'candidate'" \
    | sed -E "s/.*'revisionName': '([^']+)'.*/\1/"
}

RATING_ENGINE_REV="$(candidate_revision rateguard-rating-engine)"
WORKER_REV="$(candidate_revision rateguard-worker)"
API_REV="$(candidate_revision rateguard-api)"
WEB_REV="$(candidate_revision rateguard-web)"

GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo 'UNKNOWN_SHA')"
WEB_IMAGE_TAG="prod-${GIT_SHA}"

PROMOTE=false
for arg in "$@"; do
  case "$arg" in
    --promote) PROMOTE=true ;;
    --help|-h)
      echo "Usage: $0 [--promote]"
      echo "  (no flag)   Print the promotion plan. No gcloud/network calls."
      echo "  --promote   Actually apply it."
      exit 0
      ;;
  esac
done

print_plan() {
  cat <<PLAN
========================================================
   RateGuard Enhanced -- Promote Candidate to Production PLAN
========================================================
Currently candidate-tagged revisions (what would be promoted):
  rating-engine:  ${RATING_ENGINE_REV:-<none found>}
  worker:         ${WORKER_REV:-<none found>}
  api:            ${API_REV:-<none found>}
  web:            ${WEB_REV:-<none found>} (rebuilt with prod API URL, not reused as-is)

Production data-plane resources these will be repointed at (all pre-existing,
never provisioned by this script):
  Firestore collection: ${PROD_FIRESTORE_COLLECTION}
  GCS bucket:            ${PROD_GCS_BUCKET}
  BigQuery dataset:      ${PROD_BIGQUERY_DATASET} (${PROD_BIGQUERY_PORTFOLIO_TABLE} / ${PROD_BIGQUERY_RESULTS_TABLE})
  Pub/Sub topic:         ${PROD_TOPIC}
  Pub/Sub subscription:  ${PROD_SUBSCRIPTION} (push endpoint is the stable
                          service base URL, already correct across revisions
                          -- never a --tag URL, so it is not touched here)
  Web origin (CORS):     ${PROD_WEB_URL}
  New web image tag:     ${WEB_IMAGE_TAG}

Steps --promote would run, in order:
  1) rateguard-rating-engine: update-traffic to 100% on ${RATING_ENGINE_REV:-<candidate revision>}.
  2) rateguard-worker: --update-env-vars (Firestore/GCS/BigQuery/Pub/Sub ->
     production names above), then update-traffic to 100% on the resulting
     revision.
  3) rateguard-api: same --update-env-vars, plus RATEGUARD_CORS_ORIGINS ->
     ["${PROD_WEB_URL}"], then update-traffic to 100%.
  4) Build frontend/cloudbuild.yaml with
     _NEXT_PUBLIC_RATEGUARD_API_URL=${PROD_API_URL} (the base production URL,
     never a --tag URL) and the real Firebase web config, tag ${WEB_IMAGE_TAG}.
  5) Deploy that web image to a --no-traffic --tag verify revision first,
     fetch its served bundle, and grep-confirm it contains
     "${PROD_API_URL}" and does NOT contain "candidate---" or
     "localhost:8000" -- only then update-traffic to 100%.
  6) Print a verification checklist (does NOT execute the checks): create one
     mission through ${PROD_API_URL}, then read the resulting document
     directly from Firestore (${PROD_FIRESTORE_COLLECTION}) and the uploaded
     artifact directly from gs://${PROD_GCS_BUCKET} -- never trust the app's
     own API response alone for this confirmation.
PLAN
}

if [ "$PROMOTE" = false ]; then
  print_plan
  exit 0
fi

if [ -z "$RATING_ENGINE_REV" ] || [ -z "$WORKER_REV" ] || [ -z "$API_REV" ]; then
  echo "Error: could not find a candidate-tagged revision for one or more of" >&2
  echo "rating-engine/worker/api. Run deploy_candidate_enhanced.sh --deploy-candidate" >&2
  echo "first, or check --tag candidate manually with 'gcloud run services describe'." >&2
  exit 1
fi

echo "1. Promoting rating-engine (${RATING_ENGINE_REV})..."
gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT_ID" \
  --to-revisions="${RATING_ENGINE_REV}=100"

echo "2. Repointing worker (${WORKER_REV}) at production data-plane resources..."
gcloud run services update rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION}"
WORKER_NEW_REV="$(gcloud run services describe rateguard-worker --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
echo "   Promoting traffic to ${WORKER_NEW_REV}..."
gcloud run services update-traffic rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
  --to-revisions="${WORKER_NEW_REV}=100"

echo "3. Repointing api (${API_REV}) at production data-plane resources + CORS..."
gcloud run services update rateguard-api --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION}" \
  --update-env-vars "^@^RATEGUARD_CORS_ORIGINS=[\"${PROD_WEB_URL}\"]"
API_NEW_REV="$(gcloud run services describe rateguard-api --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
echo "   Promoting traffic to ${API_NEW_REV}..."
gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT_ID" \
  --to-revisions="${API_NEW_REV}=100"

echo "4. Building web with the production API URL baked in..."
FIREBASE_SUBSTITUTIONS=""
for k in $FIREBASE_WEB_KEYS; do
  v="$(firebase_web_value "$k")"
  if [ -z "$v" ] && [ "$k" != "MEASUREMENT_ID" ]; then
    echo "Error: NEXT_PUBLIC_FIREBASE_${k} is not set (environment or frontend/.env.local)." >&2
    exit 1
  fi
  FIREBASE_SUBSTITUTIONS="${FIREBASE_SUBSTITUTIONS},_NEXT_PUBLIC_FIREBASE_${k}=${v}"
done
gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \
  --substitutions="_IMAGE_TAG=${WEB_IMAGE_TAG},_NEXT_PUBLIC_RATEGUARD_API_URL=${PROD_API_URL}${FIREBASE_SUBSTITUTIONS}"

WEB_IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/rateguard-images/rateguard-web:${WEB_IMAGE_TAG}"

echo "5. Deploying web to a --no-traffic --tag verify revision first..."
gcloud run deploy rateguard-web --image "$WEB_IMAGE" --region "$REGION" --project "$PROJECT_ID" \
  --no-traffic --tag verify

echo "   Fetching the served bundle to confirm the baked API URL before shifting traffic..."
VERIFY_HTML="$(curl -s "https://verify---rateguard-web-nwhotixfva-uc.a.run.app/")"
CHUNK="$(echo "$VERIFY_HTML" | grep -oE '_next/static/chunks/595-[a-z0-9]+\.js' | head -n1)"
if [ -z "$CHUNK" ]; then
  echo "Error: could not locate the expected JS chunk to verify. Refusing to promote web traffic automatically -- inspect https://verify---rateguard-web-nwhotixfva-uc.a.run.app/ manually." >&2
  exit 1
fi
BUNDLE="$(curl -s "https://verify---rateguard-web-nwhotixfva-uc.a.run.app/${CHUNK}")"
if ! echo "$BUNDLE" | grep -q "$PROD_API_URL"; then
  echo "Error: served bundle does not contain the production API URL ($PROD_API_URL). Refusing to promote web traffic." >&2
  exit 1
fi
if echo "$BUNDLE" | grep -qE "candidate---|localhost:8000"; then
  echo "Error: served bundle still references a candidate-tagged or localhost API URL. Refusing to promote web traffic." >&2
  exit 1
fi
echo "   Confirmed: bundle correctly calls ${PROD_API_URL}."

WEB_NEW_REV="$(gcloud run services describe rateguard-web --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
echo "   Promoting web traffic to ${WEB_NEW_REV}..."
gcloud run services update-traffic rateguard-web --region "$REGION" --project "$PROJECT_ID" \
  --to-revisions="${WEB_NEW_REV}=100"

cat <<DONE
========================================================
PROMOTION COMPLETE
========================================================
Now verify manually (this script does not do it for you):
  1) Create one mission through ${PROD_API_URL}/api/v1/missions.
  2) Read it back directly from Firestore:
     curl -H "Authorization: Bearer \$(gcloud auth print-access-token)" \\
       "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/${PROD_FIRESTORE_COLLECTION}/<mission_id>"
  3) Confirm any uploaded source artifact lands under:
     gcloud storage ls "gs://${PROD_GCS_BUCKET}/tenants/<tenant>/sources/<source_id>/"
  4) Do a real browser click-through of the canonical demo end-to-end.
DONE
