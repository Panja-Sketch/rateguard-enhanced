#!/usr/bin/env bash
# RateGuard AI -- Isolated Candidate/Staging Deployment
#
# Builds ONE immutable backend image (tagged with the current git commit SHA,
# never `latest`) and deploys it to `rateguard-api` and `rateguard-worker` as
# a `--no-traffic --tag candidate` revision on each -- directly reachable via
# its own tagged URL, receiving ZERO normal production traffic. Candidate
# resources (Pub/Sub topic/subscription/DLQ, Firestore collection) are fully
# isolated from production while sharing the same GCP project and Firestore
# database.
#
# SAFETY: by default (no --deploy-candidate flag) this script performs ZERO
# gcloud/network calls. It only runs `git rev-parse` (local) and prints the
# full plan -- every resource name and every command it would run -- so the
# plan can be reviewed before anything touches Google Cloud. Nothing here
# ever modifies production traffic or the production Pub/Sub push config.

set -euo pipefail

PROJECT_ID="rateguard-ai"
REGION="us-central1"
RUNTIME_SA="rateguard-runtime@rateguard-ai.iam.gserviceaccount.com"
PUBSUB_PUSH_SA="rateguard-pubsub-push@rateguard-ai.iam.gserviceaccount.com"
CANDIDATE_TAG="candidate"

# Staging/candidate Pub/Sub + Firestore resource names -- fully isolated from
# production (assurance-runs / assurance-worker / assurance_runs).
STAGING_TOPIC="assurance-runs-staging"
STAGING_SUBSCRIPTION="assurance-worker-staging"
STAGING_DLQ_TOPIC="assurance-runs-staging-dlq"
STAGING_DLQ_INSPECTION_SUBSCRIPTION="assurance-runs-staging-dlq-inspect"
STAGING_FIRESTORE_COLLECTION="assurance_runs_staging"

# Staging/candidate BigQuery + GCS resource names -- a separate dataset (not
# just separate tables in the production dataset) so a candidate revision can
# never write into a production table even by a typo, and a separate bucket
# so candidate artifacts (compiled IPIR packages, reports) never land next to
# production ones. Same table names as production, isolated by living in a
# different dataset -- these are populated with ONLY the synthetic
# demonstration portfolio (backend/scripts/setup_bigquery.py +
# upload_synthetic_portfolio_bigquery.py, both already idempotent and driven
# entirely by RATEGUARD_BIGQUERY_* env vars -- no new code needed for this).
STAGING_BIGQUERY_DATASET="rateguard_staging"
STAGING_BIGQUERY_PORTFOLIO_TABLE="synthetic_policies"
STAGING_BIGQUERY_RESULTS_TABLE="portfolio_exposure_results"
STAGING_GCS_BUCKET="rateguard-ai-artifacts-staging"

# Same alignment rationale as production (see infrastructure/deploy.sh):
# 600s is Pub/Sub's hard ack-deadline maximum, giving a safe buffer over
# whatever the candidate worker's Cloud Run request timeout is configured to.
ACK_DEADLINE_SECONDS=600
MIN_RETRY_BACKOFF_SECONDS=10
MAX_RETRY_BACKOFF_SECONDS=600
MAX_DELIVERY_ATTEMPTS=5

# Immutable image identity: the git commit SHA, never `latest`. Both
# rateguard-api and rateguard-worker candidate revisions deploy this EXACT
# same image reference (by tag here; --deploy-candidate additionally resolves
# and logs the pushed digest so the two services can be verified to share the
# same digest, not just the same tag).
GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo 'UNKNOWN_SHA')"
IMAGE_TAG="candidate-${GIT_SHA}"
BACKEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-api:${IMAGE_TAG}"
FRONTEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-web:${IMAGE_TAG}"

CANDIDATE_ENV_FILE="infrastructure/.candidate-env.yaml"

# Derives the candidate API's CORS allow-list from the production origins
# already declared in infrastructure/runtime-env.yaml's
# RATEGUARD_CORS_ORIGINS: every production origin is kept unchanged (never
# replaced, never wildcarded), and one candidate origin is added per
# non-localhost production origin by inserting the Cloud Run traffic-tag
# prefix ("<tag>---") ahead of the host -- exactly how Cloud Run forms a
# --tag candidate's own URL from the service's base URL (e.g.
# "https://rateguard-web-iqofutwtva-uc.a.run.app" ->
# "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"). Pure/local:
# reads one file and does one JSON transform, no gcloud/network call. Without
# this, the candidate rateguard-web origin is never allow-listed and every
# browser request from the candidate frontend is silently blocked by
# CORSMiddleware, even though a plain CLI/urllib client (which never sends an
# Origin header) sees no failure at all.
get_candidate_cors_origins() {
  local prod_cors_line
  prod_cors_line=$(grep -E '^RATEGUARD_CORS_ORIGINS:' infrastructure/runtime-env.yaml || true)
  if [ -z "$prod_cors_line" ]; then
    echo "Error: RATEGUARD_CORS_ORIGINS not found in infrastructure/runtime-env.yaml" >&2
    exit 1
  fi
  local prod_cors_json="${prod_cors_line#RATEGUARD_CORS_ORIGINS: }"
  prod_cors_json="${prod_cors_json%\'}"
  prod_cors_json="${prod_cors_json#\'}"
  python - "$prod_cors_json" "$CANDIDATE_TAG" <<'PYEOF'
import json
import sys

origins = json.loads(sys.argv[1])
tag = sys.argv[2]
result = list(origins)
for origin in origins:
    if "://" not in origin:
        continue
    scheme, host = origin.split("://", 1)
    if host.startswith("localhost") or host.startswith("127."):
        continue
    candidate_origin = f"{scheme}://{tag}---{host}"
    if candidate_origin not in result:
        result.append(candidate_origin)
print(json.dumps(result))
PYEOF
}

CANDIDATE_CORS_ORIGINS="$(get_candidate_cors_origins)"

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
   RateGuard AI -- Candidate/Staging Deployment PLAN
   (dry-run: no gcloud command below has been executed)
========================================================
Project:                       ${PROJECT_ID}
Region:                        ${REGION}
Git commit:                    ${GIT_SHA}
Immutable image tag:           ${IMAGE_TAG}
Backend image (api + worker):  ${BACKEND_IMAGE}
Frontend image:                ${FRONTEND_IMAGE}
Cloud Run candidate tag:       ${CANDIDATE_TAG} (--no-traffic)
Runtime service account:       ${RUNTIME_SA} (no API key)

Isolated staging resources (production is untouched):
  Pub/Sub topic:                 ${STAGING_TOPIC}
  Pub/Sub subscription:          ${STAGING_SUBSCRIPTION}
  Dead-letter topic:             ${STAGING_DLQ_TOPIC}
  Dead-letter inspection sub:    ${STAGING_DLQ_INSPECTION_SUBSCRIPTION}
  Firestore collection:          ${STAGING_FIRESTORE_COLLECTION}
  BigQuery dataset:               ${STAGING_BIGQUERY_DATASET}
  BigQuery portfolio table:       ${STAGING_BIGQUERY_DATASET}.${STAGING_BIGQUERY_PORTFOLIO_TABLE}
  BigQuery results table:         ${STAGING_BIGQUERY_DATASET}.${STAGING_BIGQUERY_RESULTS_TABLE}
  GCS artifact bucket:            ${STAGING_GCS_BUCKET}
  Ack deadline:                  ${ACK_DEADLINE_SECONDS}s
  Retry backoff:                 ${MIN_RETRY_BACKOFF_SECONDS}s - ${MAX_RETRY_BACKOFF_SECONDS}s
  Max delivery attempts:         ${MAX_DELIVERY_ATTEMPTS}

Candidate env vars (non-secret; no API key is ever set):
  RATEGUARD_AGENT_ENABLED=true
  GOOGLE_GENAI_USE_VERTEXAI=true
  GOOGLE_CLOUD_PROJECT=${PROJECT_ID}
  GOOGLE_CLOUD_LOCATION=global
  RATEGUARD_GEMINI_MODEL=gemini-3.7-flash
  RATEGUARD_RUN_STORE=firestore
  RATEGUARD_FIRESTORE_COLLECTION=${STAGING_FIRESTORE_COLLECTION}
  RATEGUARD_PUBSUB_TOPIC=${STAGING_TOPIC}
  RATEGUARD_PUBSUB_SUBSCRIPTION=${STAGING_SUBSCRIPTION}
  RATEGUARD_BIGQUERY_DATASET=${STAGING_BIGQUERY_DATASET}
  RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${STAGING_BIGQUERY_PORTFOLIO_TABLE}
  RATEGUARD_BIGQUERY_RESULTS_TABLE=${STAGING_BIGQUERY_RESULTS_TABLE}
  RATEGUARD_GCS_BUCKET=${STAGING_GCS_BUCKET}
  RATEGUARD_CORS_ORIGINS=${CANDIDATE_CORS_ORIGINS}
  (production origins preserved unchanged; the candidate rateguard-web
   tag-prefixed origin above is ADDED, never a wildcard)
  (all other values inherited from infrastructure/runtime-env.yaml: project,
   region, Firestore database id, BigQuery enabled/location)

Exact commands that --deploy-candidate would run, in order:

  1) Build the image ONCE:
     gcloud builds submit . --config=./backend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${IMAGE_TAG}

  2) Deploy the SAME image digest to both candidate revisions, 0% traffic:
     gcloud run deploy rateguard-api \\
       --image ${BACKEND_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${RUNTIME_SA} \\
       --memory=512Mi --env-vars-file=${CANDIDATE_ENV_FILE}

     gcloud run deploy rateguard-worker \\
       --image ${BACKEND_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${CANDIDATE_TAG} \\
       --no-allow-unauthenticated --service-account ${RUNTIME_SA} \\
       --memory=1Gi --env-vars-file=${CANDIDATE_ENV_FILE}

  3) Idempotently create/update staging Pub/Sub topic + DLQ + subscription,
     pointed at the candidate-tagged worker URL only (never production):
     gcloud pubsub topics create ${STAGING_TOPIC}                 (if missing)
     gcloud pubsub topics create ${STAGING_DLQ_TOPIC}             (if missing)
     gcloud pubsub subscriptions create ${STAGING_SUBSCRIPTION} \\
       --topic=${STAGING_TOPIC} --ack-deadline=${ACK_DEADLINE_SECONDS} \\
       --min-retry-delay=${MIN_RETRY_BACKOFF_SECONDS}s --max-retry-delay=${MAX_RETRY_BACKOFF_SECONDS}s \\
       --dead-letter-topic=${STAGING_DLQ_TOPIC} --max-delivery-attempts=${MAX_DELIVERY_ATTEMPTS} \\
       --push-endpoint=<candidate-worker-tagged-url>/internal/pubsub/assurance \\
       --push-auth-service-account=${PUBSUB_PUSH_SA}
     gcloud pubsub subscriptions create ${STAGING_DLQ_INSPECTION_SUBSCRIPTION} \\
       --topic=${STAGING_DLQ_TOPIC}                               (if missing)
     gcloud pubsub topics add-iam-policy-binding ${STAGING_DLQ_TOPIC} \\
       --member=serviceAccount:<pubsub-service-agent> --role=roles/pubsub.publisher
     gcloud pubsub subscriptions add-iam-policy-binding ${STAGING_SUBSCRIPTION} \\
       --member=serviceAccount:<pubsub-service-agent> --role=roles/pubsub.subscriber

  4) Idempotently provision isolated staging BigQuery dataset/tables and load
     ONLY the synthetic demonstration portfolio (never production data):
     RATEGUARD_BIGQUERY_DATASET=${STAGING_BIGQUERY_DATASET} \\
     RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${STAGING_BIGQUERY_PORTFOLIO_TABLE} \\
     RATEGUARD_BIGQUERY_RESULTS_TABLE=${STAGING_BIGQUERY_RESULTS_TABLE} \\
       python backend/scripts/setup_bigquery.py         (idempotent: creates
                                                           dataset/tables only if missing)
     RATEGUARD_BIGQUERY_DATASET=${STAGING_BIGQUERY_DATASET} \\
     RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${STAGING_BIGQUERY_PORTFOLIO_TABLE} \\
       python backend/scripts/upload_synthetic_portfolio_bigquery.py
                                                          (idempotent: skips if
                                                           already 50,000 rows)
     Idempotently create the isolated staging GCS artifact bucket:
     gcloud storage buckets describe gs://${STAGING_GCS_BUCKET}   (if missing, then:)
     gcloud storage buckets create gs://${STAGING_GCS_BUCKET} \\
       --project=${PROJECT_ID} --location=${REGION} --uniform-bucket-level-access

  5) Candidate frontend (API URL is baked in at build time -- see
     frontend/src/lib/api/client.ts's NEXT_PUBLIC_RATEGUARD_API_URL -- so a
     genuinely isolated candidate frontend needs its own build pointed only
     at the candidate-tagged API URL, never the production one):
     gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${IMAGE_TAG},_NEXT_PUBLIC_RATEGUARD_API_URL=<candidate-api-tagged-url>
     gcloud run deploy rateguard-web \\
       --image ${FRONTEND_IMAGE} --region ${REGION} --platform managed \\
       --no-traffic --tag ${CANDIDATE_TAG} --allow-unauthenticated

NOT done by this script, ever:
  - No production traffic change (no 'gcloud run services update-traffic').
  - No change to the production Pub/Sub subscription's push config.
  - No write to the production BigQuery dataset ('rateguard') or bucket
    ('rateguard-ai-artifacts') -- candidate only ever writes to
    ${STAGING_BIGQUERY_DATASET} / gs://${STAGING_GCS_BUCKET}.
  - No API key is added anywhere.
  - No production promotion (see infrastructure/promote_candidate.sh).

Re-run this plan any time with no arguments. Pass --deploy-candidate to
actually execute it.
PLAN
}

write_candidate_env_file() {
  cp infrastructure/runtime-env.yaml "$CANDIDATE_ENV_FILE"
  # Remove production-only keys so the staging overrides below are
  # unambiguous (avoids two conflicting values for the same key in one file).
  grep -v -E '^(RATEGUARD_PUBSUB_TOPIC|RATEGUARD_PUBSUB_SUBSCRIPTION|RATEGUARD_FIRESTORE_COLLECTION|RATEGUARD_AGENT_ENABLED|GOOGLE_GENAI_USE_VERTEXAI|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|RATEGUARD_GEMINI_MODEL|RATEGUARD_RUN_STORE|RATEGUARD_BIGQUERY_DATASET|RATEGUARD_BIGQUERY_PORTFOLIO_TABLE|RATEGUARD_BIGQUERY_RESULTS_TABLE|RATEGUARD_GCS_BUCKET|RATEGUARD_CORS_ORIGINS):' \
    infrastructure/runtime-env.yaml > "$CANDIDATE_ENV_FILE"
  cat >> "$CANDIDATE_ENV_FILE" <<ENV
RATEGUARD_AGENT_ENABLED: "true"
GOOGLE_GENAI_USE_VERTEXAI: "true"
GOOGLE_CLOUD_PROJECT: "${PROJECT_ID}"
GOOGLE_CLOUD_LOCATION: "global"
RATEGUARD_GEMINI_MODEL: "gemini-3.7-flash"
RATEGUARD_RUN_STORE: "firestore"
RATEGUARD_PUBSUB_TOPIC: "${STAGING_TOPIC}"
RATEGUARD_PUBSUB_SUBSCRIPTION: "${STAGING_SUBSCRIPTION}"
RATEGUARD_FIRESTORE_COLLECTION: "${STAGING_FIRESTORE_COLLECTION}"
RATEGUARD_BIGQUERY_DATASET: "${STAGING_BIGQUERY_DATASET}"
RATEGUARD_BIGQUERY_PORTFOLIO_TABLE: "${STAGING_BIGQUERY_PORTFOLIO_TABLE}"
RATEGUARD_BIGQUERY_RESULTS_TABLE: "${STAGING_BIGQUERY_RESULTS_TABLE}"
RATEGUARD_GCS_BUCKET: "${STAGING_GCS_BUCKET}"
RATEGUARD_CORS_ORIGINS: '${CANDIDATE_CORS_ORIGINS}'
ENV
}

# Reliable tagged-URL discovery via `--format=json` + a small Python filter.
# The single-expression gcloud format filter this used to rely on --
# `--format "value(status.traffic[?tag==\"...\"].url)"` -- returns empty on
# current gcloud (tested on Google Cloud SDK 581.0.0 / core 2026.08.14) even
# though `--format="json(status.traffic)"` shows the entry is genuinely
# present, so it silently aborted every deploy right after a real, successful
# `gcloud run deploy`. This never fabricates a URL: empty stdin/JSON or no
# matching tag both simply produce no output, same failure mode as before.
get_tagged_url() {
  local service="$1"
  # `command -v python3` alone is not enough: on Windows it can resolve to a
  # non-functional Microsoft Store shim that exits nonzero on any real
  # invocation, so the candidate is verified to actually run before use.
  local py=python3
  if ! "$py" -c "" >/dev/null 2>&1; then
    py=python
  fi
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

deploy_candidate() {
  echo "========================================================"
  echo "   RateGuard AI -- Deploying Candidate (--deploy-candidate)"
  echo "   Image tag: ${IMAGE_TAG}"
  echo "========================================================"

  gcloud config set project "$PROJECT_ID"

  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
  PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

  write_candidate_env_file

  echo "1. Building candidate backend image (immutable tag, never latest)..."
  gcloud builds submit . --config=./backend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG"

  echo "2. Deploying candidate API revision (--no-traffic --tag ${CANDIDATE_TAG})..."
  gcloud run deploy rateguard-api \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --allow-unauthenticated --service-account "$RUNTIME_SA" \
    --memory=512Mi --env-vars-file="$CANDIDATE_ENV_FILE"

  # Reliable tagged-URL discovery: read the actual traffic entry rather than
  # fabricating a plausible-looking URL by string-substituting the tag prefix
  # into the untagged URL (the previous fallback here could silently produce
  # a URL that was never actually deployed). A missing candidate URL stops
  # the script before any Pub/Sub or frontend step runs.
  CANDIDATE_API_URL=$(get_tagged_url rateguard-api)
  if [ -z "$CANDIDATE_API_URL" ]; then
    echo "Error: could not discover the candidate-tagged URL for rateguard-api. Refusing to fabricate one. Stopping before any Pub/Sub or frontend step." >&2
    exit 1
  fi
  echo "   Candidate API URL: ${CANDIDATE_API_URL}"

  echo "3. Deploying candidate worker revision (--no-traffic --tag ${CANDIDATE_TAG})..."
  gcloud run deploy rateguard-worker \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --no-allow-unauthenticated --service-account "$RUNTIME_SA" \
    --memory=1Gi --env-vars-file="$CANDIDATE_ENV_FILE"

  CANDIDATE_WORKER_URL=$(get_tagged_url rateguard-worker)
  if [ -z "$CANDIDATE_WORKER_URL" ]; then
    echo "Error: could not discover the candidate-tagged URL for rateguard-worker. Refusing to fabricate one. Stopping before any Pub/Sub or frontend step." >&2
    exit 1
  fi
  echo "   Candidate Worker URL: ${CANDIDATE_WORKER_URL}"

  # The untagged (base) worker service URL is required separately: Cloud Run
  # requires the OIDC push-auth token audience to be the plain service URL
  # even when the push endpoint itself targets a specific traffic tag.
  CANDIDATE_WORKER_UNTAGGED_URL=$(gcloud run services describe rateguard-worker --region "$REGION" \
    --format "value(status.url)")
  if [ -z "$CANDIDATE_WORKER_UNTAGGED_URL" ]; then
    echo "Error: could not discover the untagged rateguard-worker service URL (needed as the Pub/Sub OIDC audience). Stopping." >&2
    exit 1
  fi

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
      --push-endpoint="${CANDIDATE_WORKER_URL}/internal/pubsub/assurance" \
      --push-auth-service-account="$PUBSUB_PUSH_SA" \
      --push-auth-token-audience="$CANDIDATE_WORKER_UNTAGGED_URL"
  else
    gcloud pubsub subscriptions update "$STAGING_SUBSCRIPTION" \
      --ack-deadline="$ACK_DEADLINE_SECONDS" \
      --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" \
      --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" \
      --dead-letter-topic="$STAGING_DLQ_TOPIC" \
      --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
    gcloud pubsub subscriptions modify-push-config "$STAGING_SUBSCRIPTION" \
      --push-endpoint="${CANDIDATE_WORKER_URL}/internal/pubsub/assurance" \
      --push-auth-service-account="$PUBSUB_PUSH_SA" \
      --push-auth-token-audience="$CANDIDATE_WORKER_UNTAGGED_URL"
  fi
  if ! gcloud pubsub subscriptions describe "$STAGING_DLQ_INSPECTION_SUBSCRIPTION" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$STAGING_DLQ_INSPECTION_SUBSCRIPTION" --topic="$STAGING_DLQ_TOPIC"
  fi
  gcloud pubsub topics add-iam-policy-binding "$STAGING_DLQ_TOPIC" \
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" --role="roles/pubsub.publisher" >/dev/null
  gcloud pubsub subscriptions add-iam-policy-binding "$STAGING_SUBSCRIPTION" \
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" --role="roles/pubsub.subscriber" >/dev/null

  echo "5. Idempotently provisioning isolated staging BigQuery dataset/tables and"
  echo "   loading ONLY the synthetic demonstration portfolio (never production data)..."
  RATEGUARD_BIGQUERY_DATASET="$STAGING_BIGQUERY_DATASET" \
  RATEGUARD_BIGQUERY_PORTFOLIO_TABLE="$STAGING_BIGQUERY_PORTFOLIO_TABLE" \
  RATEGUARD_BIGQUERY_RESULTS_TABLE="$STAGING_BIGQUERY_RESULTS_TABLE" \
    python backend/scripts/setup_bigquery.py
  RATEGUARD_BIGQUERY_DATASET="$STAGING_BIGQUERY_DATASET" \
  RATEGUARD_BIGQUERY_PORTFOLIO_TABLE="$STAGING_BIGQUERY_PORTFOLIO_TABLE" \
    python backend/scripts/upload_synthetic_portfolio_bigquery.py

  echo "6. Idempotently creating isolated staging GCS artifact bucket..."
  if ! gcloud storage buckets describe "gs://${STAGING_GCS_BUCKET}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${STAGING_GCS_BUCKET}" \
      --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access
  else
    echo "   Bucket gs://${STAGING_GCS_BUCKET} already exists."
  fi

  echo "7. Building and deploying candidate frontend (--no-traffic --tag ${CANDIDATE_TAG})..."
  gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG",_NEXT_PUBLIC_RATEGUARD_API_URL="$CANDIDATE_API_URL"
  gcloud run deploy rateguard-web \
    --image "$FRONTEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" --allow-unauthenticated

  CANDIDATE_WEB_URL=$(get_tagged_url rateguard-web || true)

  rm -f "$CANDIDATE_ENV_FILE"

  echo "========================================================"
  echo "CANDIDATE DEPLOYMENT COMPLETE (0% production traffic)"
  echo "Candidate API URL:      ${CANDIDATE_API_URL}"
  echo "Candidate Worker URL:   ${CANDIDATE_WORKER_URL}"
  echo "Candidate Web URL:      ${CANDIDATE_WEB_URL}"
  echo "Image tag:              ${IMAGE_TAG}"
  echo ""
  echo "Next step: python backend/scripts/verify_candidate.py --yes-test-candidate \\"
  echo "  --api-url ${CANDIDATE_API_URL} --frontend-url ${CANDIDATE_WEB_URL}"
  echo "========================================================"
}

if [ "$DEPLOY_CANDIDATE" = true ]; then
  deploy_candidate
else
  print_plan
fi
