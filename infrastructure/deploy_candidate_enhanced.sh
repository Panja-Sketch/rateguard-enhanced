#!/usr/bin/env bash
# RateGuard Enhanced -- Isolated Candidate Deployment (project: rateguard-enhanced)
#
# Builds FOUR immutable images (rating-engine, worker, api, web), each tagged
# with the current git commit SHA (never `latest`), and deploys them as
# `--no-traffic --tag candidate` Cloud Run revisions -- reachable only at
# their own tagged URL, receiving ZERO production traffic. Candidate
# resources (Pub/Sub topic/subscription/DLQ, Firestore collection, BigQuery
# dataset, GCS bucket) are isolated from production via distinct staging
# names, inside the SAME project (rateguard-enhanced) using the SAME three
# authorized service accounts.
#
# This script deliberately does NOT exist for, and must never be pointed at,
# the old `rateguard-ai` project. Its legacy deployment scripts were removed
# from this repository (see git history).
#
# SAFETY: by default (no --deploy-candidate flag) this performs ZERO
# gcloud/network calls -- it only runs `git rev-parse` (local) and prints the
# full plan. Nothing here ever modifies production traffic.
#
# IMPORTANT: a plain `gcloud run services update-traffic ... =100` on the
# revisions this script builds is NOT a full promotion -- they stay wired to
# the isolated STAGING resources above, and rateguard-web's API URL is a
# build-time constant that a traffic shift can never change. Use
# promote_candidate_to_production.sh once a candidate has been tested here.

set -euo pipefail

PROJECT_ID="rateguard-enhanced"
REGION="us-central1"
ARTIFACT_REPO="rateguard-images"
API_SA="rateguard-api-sa@rateguard-enhanced.iam.gserviceaccount.com"
WORKER_SA="rateguard-worker-sa@rateguard-enhanced.iam.gserviceaccount.com"
WEB_SA="rateguard-web-sa@rateguard-enhanced.iam.gserviceaccount.com"
# No standalone rating-engine SA is among the three authorized accounts; it
# runs as a private, worker-only-invocable service under the worker SA
# (matches locked doc section 12.2: "rating-engine-demo: private/authenticated
# if deployed separately" -- it never needs its own external identity beyond
# what lets the worker's Pub/Sub-triggered calls reach it).
RATING_ENGINE_SA="$WORKER_SA"
LOCKED_MODEL_ID="gemini-3.1-flash-lite"
VERTEX_AI_LOCATION="us"
CANDIDATE_TAG="candidate"

STAGING_TOPIC="assurance-runs-staging"
STAGING_SUBSCRIPTION="assurance-worker-staging"
STAGING_DLQ_TOPIC="assurance-runs-staging-dlq"
STAGING_DLQ_INSPECTION_SUBSCRIPTION="assurance-runs-staging-dlq-inspect"
STAGING_FIRESTORE_COLLECTION="assurance_runs_staging"
STAGING_BIGQUERY_DATASET="rateguard_staging"
STAGING_BIGQUERY_PORTFOLIO_TABLE="synthetic_policies"
STAGING_BIGQUERY_RESULTS_TABLE="portfolio_exposure_results"
STAGING_GCS_BUCKET="rateguard-enhanced-artifacts-staging"

ACK_DEADLINE_SECONDS=600
MIN_RETRY_BACKOFF_SECONDS=10
MAX_RETRY_BACKOFF_SECONDS=600
MAX_DELIVERY_ATTEMPTS=5

GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo 'UNKNOWN_SHA')"
DIRTY_SUFFIX=""
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  DIRTY_SUFFIX="-dirty"
fi
IMAGE_TAG="candidate-${GIT_SHA}${DIRTY_SUFFIX}"

RATING_ENGINE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-rating-engine:${IMAGE_TAG}"
WORKER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-worker:${IMAGE_TAG}"
API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-api:${IMAGE_TAG}"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-web:${IMAGE_TAG}"

# api and worker are the same deployable package (locked doc section 12.1:
# "Worker: Same Python package/image as API with separate command"), so they
# share one build/image, exactly like the pre-existing (wrong-project)
# deploy_candidate.sh did. rating-engine is a genuinely separate image
# (backend/rating_engine/Dockerfile, its own cloudbuild.yaml).
BACKEND_IMAGE="$API_IMAGE"

# One env file per service: the API and worker share an image but must not share
# a route surface (RATEGUARD_SERVICE_ROLE=api|worker), so each gets its own file.
CANDIDATE_ENV_FILE_API="infrastructure/.candidate-enhanced-env-api.yaml"
CANDIDATE_ENV_FILE_WORKER="infrastructure/.candidate-enhanced-env-worker.yaml"

DEPLOY_CANDIDATE=false
for arg in "$@"; do
  case "$arg" in
    --deploy-candidate) DEPLOY_CANDIDATE=true ;;
    --help|-h)
      echo "Usage: $0 [--deploy-candidate]"
      echo "  (no flag)           Print the full candidate deployment plan. No GCP calls."
      echo "  --deploy-candidate  Actually build and deploy the candidate resources."
      exit 0
      ;;
  esac
done

print_plan() {
  cat <<PLAN
========================================================
   RateGuard Enhanced -- Candidate Deployment PLAN
   (dry-run: no gcloud command below has been executed)
========================================================
Project:                        ${PROJECT_ID}
Region:                         ${REGION}
Artifact Registry repo:         ${ARTIFACT_REPO}
Git commit:                     ${GIT_SHA}${DIRTY_SUFFIX}
Immutable image tag:            ${IMAGE_TAG}
Rating-engine image:            ${RATING_ENGINE_IMAGE}
Worker/API image (shared):      ${BACKEND_IMAGE}
Web image:                      ${WEB_IMAGE}
Cloud Run candidate tag:        ${CANDIDATE_TAG} (--no-traffic on every service)
Gemini model / location:        ${LOCKED_MODEL_ID} / ${VERTEX_AI_LOCATION} (Vertex AI ADC, no API key)
Service accounts:
  rateguard-api:                 ${API_SA}
  rateguard-worker:               ${WORKER_SA}
  rateguard-rating-engine:        ${RATING_ENGINE_SA} (private, worker-invoker-only)
  rateguard-web:                   ${WEB_SA}

Isolated staging resources (production untouched -- this project currently
has ZERO deployed Cloud Run services, so "production" does not yet exist;
these candidate resources are still kept staging-scoped for forward safety):
  Pub/Sub topic:                  ${STAGING_TOPIC}
  Pub/Sub subscription:           ${STAGING_SUBSCRIPTION}
  Dead-letter topic:              ${STAGING_DLQ_TOPIC}
  Dead-letter inspection sub:     ${STAGING_DLQ_INSPECTION_SUBSCRIPTION}
  Firestore collection:           ${STAGING_FIRESTORE_COLLECTION}
  BigQuery dataset:                ${STAGING_BIGQUERY_DATASET}
  GCS artifact bucket:             ${STAGING_GCS_BUCKET}
  Ack deadline / retry / attempts: ${ACK_DEADLINE_SECONDS}s / ${MIN_RETRY_BACKOFF_SECONDS}-${MAX_RETRY_BACKOFF_SECONDS}s / ${MAX_DELIVERY_ATTEMPTS}

Exact commands that --deploy-candidate would run, in order:

  1) Build all four images (immutable tag, never 'latest'):
     gcloud builds submit . --config=./backend/rating_engine/cloudbuild.yaml --substitutions=_IMAGE_TAG=${IMAGE_TAG}
     gcloud builds submit . --config=./backend/cloudbuild.yaml --substitutions=_IMAGE_TAG=${IMAGE_TAG}
     (web build deferred until the candidate API URL is known -- step 5)

  2) Deploy rating-engine candidate (PRIVATE -- no unauthenticated invocation):
     gcloud run deploy rateguard-rating-engine --image ${RATING_ENGINE_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --no-allow-unauthenticated --service-account ${RATING_ENGINE_SA} \\
       --memory=512Mi

  3) Deploy worker candidate (PRIVATE -- Pub/Sub push only, OIDC-authenticated):
     gcloud run deploy rateguard-worker --image ${WORKER_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --no-allow-unauthenticated --service-account ${WORKER_SA} \\
       --memory=1Gi --env-vars-file=${CANDIDATE_ENV_FILE_WORKER}   # RATEGUARD_SERVICE_ROLE=worker
     gcloud run services add-iam-policy-binding rateguard-worker \\
       --member=serviceAccount:${WORKER_SA} --role=roles/run.invoker  (rating-engine caller)

  4) Idempotently configure isolated staging Pub/Sub + DLQ, pointed at the
     candidate-tagged worker URL, OIDC audience pinned to its untagged URL.

  5) Deploy API candidate (public, verifies Firebase ID tokens):
     gcloud run deploy rateguard-api --image ${API_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${API_SA} \\
       --memory=512Mi --env-vars-file=${CANDIDATE_ENV_FILE_API}   # RATEGUARD_SERVICE_ROLE=api
     (public at the network layer, but every /api/v1 route verifies a Firebase
      ID token; the internal Pub/Sub route is NOT served by this service)

  6) Idempotently provision staging BigQuery dataset/tables (synthetic
     portfolio only) and the staging GCS bucket (uniform access, versioned).

  7) Build and deploy web candidate (API URL and the PUBLIC Firebase web config
     baked in at build time from NEXT_PUBLIC_FIREBASE_* -- read from the shell
     environment or frontend/.env.local; the script refuses to build if any is
     missing):
     gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${IMAGE_TAG},_NEXT_PUBLIC_RATEGUARD_API_URL=<candidate-api-tagged-url>,_NEXT_PUBLIC_FIREBASE_*=...
     gcloud run deploy rateguard-web --image ${WEB_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${WEB_SA}

  7b) Point the API CORS allowlist at the candidate web origin (exactly one
     explicit origin, no wildcard), still --no-traffic:
     gcloud run services update rateguard-api --region ${REGION} --no-traffic \\
       --tag ${CANDIDATE_TAG} --update-env-vars 'RATEGUARD_CORS_ORIGINS=["<candidate-web-url>"]'

  8) Postcondition verification: every candidate image digest recorded, every
     service's production traffic allocation confirmed unchanged (100% to no
     revision, since none exists yet), no unauthenticated invoker binding on
     worker or rating-engine.

NOT done by this script, ever:
  - No production traffic change ('gcloud run services update-traffic').
  - No GEMINI_API_KEY / GOOGLE_API_KEY / Firebase private-key JSON anywhere, and
    no --set-secrets mapping for FIREBASE_ADMIN_KEY (that Secret Manager secret
    is left in place; only the runtime dependency on it is removed).
  - No broad Owner/Editor/storage-admin IAM grant.
  - No deploy to, or read/write of, the old 'rateguard-ai' project.

Re-run this plan any time with no arguments. Pass --deploy-candidate to
actually execute it.
PLAN
}

write_candidate_env_file() {
  # Args: <service role: api|worker> <output file>. Non-secret env vars only.
  # No GEMINI_API_KEY/GOOGLE_API_KEY, no Firebase private-key JSON -- Vertex AI
  # and Firebase Admin both use ADC via the attached service account.
  local role="$1" out="$2"
  cat > "$out" <<ENV
RATEGUARD_SERVICE_ROLE: "${role}"
RATEGUARD_ENVIRONMENT: "candidate"
RATEGUARD_AGENT_ENABLED: "true"
RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION: "10"
RATEGUARD_MAX_PROBE_ROUNDS: "3"
RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD: "0.8"
GOOGLE_GENAI_USE_VERTEXAI: "true"
GOOGLE_CLOUD_PROJECT: "${PROJECT_ID}"
GOOGLE_CLOUD_LOCATION: "${VERTEX_AI_LOCATION}"
VERTEX_AI_LOCATION: "${VERTEX_AI_LOCATION}"
RATEGUARD_GOOGLE_CLOUD_PROJECT: "${PROJECT_ID}"
RATEGUARD_GOOGLE_CLOUD_REGION: "${REGION}"
RATEGUARD_GEMINI_MODEL: "${LOCKED_MODEL_ID}"
RATEGUARD_FIREBASE_PROJECT_ID: "${PROJECT_ID}"
RATEGUARD_RUN_STORE: "firestore"
RATEGUARD_ARTIFACT_STORE: "gcs"
RATEGUARD_FIRESTORE_DATABASE: "(default)"
RATEGUARD_FIRESTORE_COLLECTION: "${STAGING_FIRESTORE_COLLECTION}"
RATEGUARD_GCS_BUCKET: "${STAGING_GCS_BUCKET}"
RATEGUARD_BIGQUERY_ENABLED: "true"
RATEGUARD_BIGQUERY_DATASET: "${STAGING_BIGQUERY_DATASET}"
RATEGUARD_BIGQUERY_PORTFOLIO_TABLE: "${STAGING_BIGQUERY_PORTFOLIO_TABLE}"
RATEGUARD_BIGQUERY_RESULTS_TABLE: "${STAGING_BIGQUERY_RESULTS_TABLE}"
RATEGUARD_ASYNC_ENABLED: "true"
RATEGUARD_EXECUTION_MODE: "pubsub"
RATEGUARD_PUBSUB_TOPIC: "${STAGING_TOPIC}"
RATEGUARD_PUBSUB_SUBSCRIPTION: "${STAGING_SUBSCRIPTION}"
RATEGUARD_DATA_DIR: "/app/data"
RATEGUARD_RATE_LIMIT_ENABLED: "true"
RATEGUARD_RATE_LIMITS: '{"connector_test":"5/3600","mission_create":"10/3600","source_upload":"30/3600","source_compile":"30/3600","explanation_create":"20/3600","evidence_download":"30/3600","source_download":"60/3600"}'
RATEGUARD_CORS_ORIGINS: '["http://localhost:3000"]'
ENV
}

# Public Firebase web config for the web build. Read from the environment or
# from frontend/.env.local (grep only -- the file is never sourced/evaluated).
FIREBASE_WEB_KEYS="API_KEY AUTH_DOMAIN PROJECT_ID STORAGE_BUCKET MESSAGING_SENDER_ID APP_ID MEASUREMENT_ID"
firebase_web_value() {
  local key="NEXT_PUBLIC_FIREBASE_$1" val
  val="${!key:-}"
  if [ -z "$val" ] && [ -f frontend/.env.local ]; then
    val="$(grep -E "^${key}=" frontend/.env.local | head -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
  fi
  printf '%s' "$val"
}
build_firebase_substitutions() {
  local out="" k v
  for k in $FIREBASE_WEB_KEYS; do
    v="$(firebase_web_value "$k")"
    if [ -z "$v" ] && [ "$k" != "MEASUREMENT_ID" ]; then
      echo "Error: NEXT_PUBLIC_FIREBASE_${k} is not set (environment or frontend/.env.local)." >&2
      return 1
    fi
    out="${out},_NEXT_PUBLIC_FIREBASE_${k}=${v}"
  done
  printf '%s' "$out"
}

get_tagged_url() {
  local service="$1"
  local py=python3
  if ! "$py" -c "" >/dev/null 2>&1; then py=python; fi
  gcloud run services describe "$service" --region "$REGION" --format=json 2>/dev/null \
    | "$py" -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for entry in data.get('status', {}).get('traffic', []) or []:
    if entry.get('tag') == '${CANDIDATE_TAG}':
        print(entry.get('url', ''))
        break
"
}

get_untagged_url() {
  gcloud run services describe "$1" --region "$REGION" --format="value(status.url)" 2>/dev/null
}

resolve_image_digest() {
  gcloud artifacts docker images describe "$1" --format="value(image_summary.digest)" 2>/dev/null
}

deploy_candidate() {
  echo "========================================================"
  echo "   RateGuard Enhanced -- Deploying Candidate"
  echo "   Image tag: ${IMAGE_TAG}"
  echo "========================================================"

  gcloud config set project "$PROJECT_ID" >/dev/null

  write_candidate_env_file api "$CANDIDATE_ENV_FILE_API"
  write_candidate_env_file worker "$CANDIDATE_ENV_FILE_WORKER"

  echo "1a. Building rating-engine image..."
  gcloud builds submit . --config=./backend/rating_engine/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG"

  echo "1b. Building worker/api image (shared)..."
  gcloud builds submit . --config=./backend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG"

  RATING_ENGINE_DIGEST=$(resolve_image_digest "$RATING_ENGINE_IMAGE")
  BACKEND_DIGEST=$(resolve_image_digest "$BACKEND_IMAGE")
  if [ -z "$RATING_ENGINE_DIGEST" ] || [ -z "$BACKEND_DIGEST" ]; then
    echo "Error: could not resolve a pushed image digest. Refusing to continue." >&2
    exit 1
  fi
  echo "   rating-engine digest: ${RATING_ENGINE_DIGEST}"
  echo "   worker/api digest:    ${BACKEND_DIGEST}"

  echo "2. Deploying candidate rating-engine (PRIVATE, --no-traffic)..."
  gcloud run deploy rateguard-rating-engine \
    --image "$RATING_ENGINE_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --no-allow-unauthenticated --service-account "$RATING_ENGINE_SA" \
    --memory=512Mi --port=8080

  RATING_ENGINE_TAGGED_URL=$(get_tagged_url rateguard-rating-engine)
  if [ -z "$RATING_ENGINE_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-rating-engine URL." >&2
    exit 1
  fi
  echo "   Candidate rating-engine URL: ${RATING_ENGINE_TAGGED_URL}"

  echo "3. Deploying candidate worker (PRIVATE, --no-traffic)..."
  gcloud run deploy rateguard-worker \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --no-allow-unauthenticated --service-account "$WORKER_SA" \
    --memory=1Gi --env-vars-file="$CANDIDATE_ENV_FILE_WORKER"

  # The connector base URL(s) can only be known once the candidate
  # rating-engine's tagged URL is resolved (above), so they can't live in
  # the static env-vars-file written before any deploy happens -- wired in
  # here via --update-env-vars, the same pattern already used for the web
  # origin CORS wiring below. Without this, the worker's connector client
  # falls back to Settings' http://127.0.0.1:8000 default and every
  # connector-backed mission on this candidate fails closed with
  # CONNECTOR_TRANSPORT_ERROR (both rating-engine-demo and
  # vendor-gateway-demo point at the same rating-engine service today).
  echo "   Wiring candidate rating-engine connector URL into worker..."
  gcloud run services update rateguard-worker --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG" \
    --update-env-vars "RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL},RATEGUARD_RATING_ENGINE_CONNECTOR_IS_LOCAL_DEV=false,RATEGUARD_RATING_ENGINE_CONNECTOR_AUTH_MODE=google_id_token,RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL}"

  WORKER_TAGGED_URL=$(get_tagged_url rateguard-worker)
  WORKER_UNTAGGED_URL=$(get_untagged_url rateguard-worker)
  if [ -z "$WORKER_TAGGED_URL" ] || [ -z "$WORKER_UNTAGGED_URL" ]; then
    echo "Error: could not discover the candidate rateguard-worker URL(s)." >&2
    exit 1
  fi
  echo "   Candidate worker URL: ${WORKER_TAGGED_URL}"

  echo "   Granting worker SA run.invoker on rateguard-rating-engine (narrow, service-scoped)..."
  gcloud run services add-iam-policy-binding rateguard-rating-engine \
    --region "$REGION" --member="serviceAccount:${WORKER_SA}" --role="roles/run.invoker" >/dev/null

  # The API service also calls the connector directly (POST
  # /api/v1/connectors/{id}/test, an admin-only golden-case health check),
  # so it needs the same narrow, service-scoped invoker grant as the worker.
  echo "   Granting API SA run.invoker on rateguard-rating-engine (narrow, service-scoped)..."
  gcloud run services add-iam-policy-binding rateguard-rating-engine \
    --region "$REGION" --member="serviceAccount:${API_SA}" --role="roles/run.invoker" >/dev/null

  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
  PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

  echo "4. Idempotently configuring isolated staging Pub/Sub + DLQ..."
  if ! gcloud pubsub topics describe "$STAGING_TOPIC" >/dev/null 2>&1; then
    gcloud pubsub topics create "$STAGING_TOPIC"
  fi
  if ! gcloud pubsub topics describe "$STAGING_DLQ_TOPIC" >/dev/null 2>&1; then
    gcloud pubsub topics create "$STAGING_DLQ_TOPIC"
  fi
  if ! gcloud pubsub subscriptions describe "$STAGING_SUBSCRIPTION" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$STAGING_SUBSCRIPTION" \
      --topic="$STAGING_TOPIC" \
      --ack-deadline="$ACK_DEADLINE_SECONDS" \
      --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" \
      --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" \
      --dead-letter-topic="$STAGING_DLQ_TOPIC" \
      --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS" \
      --push-endpoint="${WORKER_TAGGED_URL}/internal/pubsub/assurance" \
      --push-auth-service-account="$WORKER_SA" \
      --push-auth-token-audience="$WORKER_UNTAGGED_URL"
  else
    gcloud pubsub subscriptions update "$STAGING_SUBSCRIPTION" \
      --ack-deadline="$ACK_DEADLINE_SECONDS" \
      --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" \
      --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" \
      --dead-letter-topic="$STAGING_DLQ_TOPIC" \
      --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
    gcloud pubsub subscriptions modify-push-config "$STAGING_SUBSCRIPTION" \
      --push-endpoint="${WORKER_TAGGED_URL}/internal/pubsub/assurance" \
      --push-auth-service-account="$WORKER_SA" \
      --push-auth-token-audience="$WORKER_UNTAGGED_URL"
  fi
  if ! gcloud pubsub subscriptions describe "$STAGING_DLQ_INSPECTION_SUBSCRIPTION" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$STAGING_DLQ_INSPECTION_SUBSCRIPTION" --topic="$STAGING_DLQ_TOPIC"
  fi
  gcloud pubsub topics add-iam-policy-binding "$STAGING_DLQ_TOPIC" \
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" --role="roles/pubsub.publisher" >/dev/null
  gcloud pubsub subscriptions add-iam-policy-binding "$STAGING_SUBSCRIPTION" \
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" --role="roles/pubsub.subscriber" >/dev/null
  gcloud run services add-iam-policy-binding rateguard-worker \
    --region "$REGION" --member="serviceAccount:${WORKER_SA}" --role="roles/run.invoker" >/dev/null

  echo "5. Deploying candidate API (public, --no-traffic)..."
  gcloud run deploy rateguard-api \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --allow-unauthenticated --service-account "$API_SA" \
    --memory=512Mi --env-vars-file="$CANDIDATE_ENV_FILE_API"

  # Same wiring as the worker above -- the API service also drives
  # ConnectorClient directly (app/api/connectors.py's connector-test route).
  echo "   Wiring candidate rating-engine connector URL into API..."
  gcloud run services update rateguard-api --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG" \
    --update-env-vars "RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL},RATEGUARD_RATING_ENGINE_CONNECTOR_IS_LOCAL_DEV=false,RATEGUARD_RATING_ENGINE_CONNECTOR_AUTH_MODE=google_id_token,RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL}"

  API_TAGGED_URL=$(get_tagged_url rateguard-api)
  if [ -z "$API_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-api URL." >&2
    exit 1
  fi
  echo "   Candidate API URL: ${API_TAGGED_URL}"

  echo "6. Idempotently provisioning staging BigQuery dataset/tables and GCS bucket..."
  RATEGUARD_BIGQUERY_DATASET="$STAGING_BIGQUERY_DATASET" \
  RATEGUARD_BIGQUERY_PORTFOLIO_TABLE="$STAGING_BIGQUERY_PORTFOLIO_TABLE" \
  RATEGUARD_BIGQUERY_RESULTS_TABLE="$STAGING_BIGQUERY_RESULTS_TABLE" \
    python backend/scripts/setup_bigquery.py || echo "   (setup_bigquery.py not runnable from this shell context; run manually if needed)"
  RATEGUARD_BIGQUERY_DATASET="$STAGING_BIGQUERY_DATASET" \
  RATEGUARD_BIGQUERY_PORTFOLIO_TABLE="$STAGING_BIGQUERY_PORTFOLIO_TABLE" \
    python backend/scripts/upload_synthetic_portfolio_bigquery.py || echo "   (upload_synthetic_portfolio_bigquery.py not runnable from this shell context; run manually if needed)"

  if ! gcloud storage buckets describe "gs://${STAGING_GCS_BUCKET}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${STAGING_GCS_BUCKET}" \
      --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access
  else
    echo "   Bucket gs://${STAGING_GCS_BUCKET} already exists."
  fi

  echo "7. Building and deploying candidate web..."
  FIREBASE_SUBSTITUTIONS="$(build_firebase_substitutions)" || exit 1
  gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG",_NEXT_PUBLIC_RATEGUARD_API_URL="$API_TAGGED_URL"${FIREBASE_SUBSTITUTIONS}
  gcloud run deploy rateguard-web \
    --image "$WEB_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --allow-unauthenticated --service-account "$WEB_SA"

  WEB_TAGGED_URL=$(get_tagged_url rateguard-web || true)
  if [ -z "$WEB_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-web URL (needed for the API CORS allowlist)." >&2
    exit 1
  fi

  echo "7b. Restricting API CORS to the candidate web origin (explicit, no wildcard)..."
  gcloud run services update rateguard-api --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG" \
    --update-env-vars "RATEGUARD_CORS_ORIGINS=[\"${WEB_TAGGED_URL}\"]"

  rm -f "$CANDIDATE_ENV_FILE_API" "$CANDIDATE_ENV_FILE_WORKER"

  echo "========================================================"
  echo "CANDIDATE DEPLOYMENT COMPLETE (0% production traffic)"
  echo "Image tag:                  ${IMAGE_TAG}"
  echo "Rating-engine digest:       ${RATING_ENGINE_DIGEST}"
  echo "Worker/API digest:          ${BACKEND_DIGEST}"
  echo "Candidate rating-engine URL: ${RATING_ENGINE_TAGGED_URL} (private)"
  echo "Candidate worker URL:        ${WORKER_TAGGED_URL} (private)"
  echo "Candidate API URL:           ${API_TAGGED_URL}"
  echo "Candidate web URL:           ${WEB_TAGGED_URL}"
  echo "========================================================"
}

if [ "$DEPLOY_CANDIDATE" = true ]; then
  deploy_candidate
else
  print_plan
fi
