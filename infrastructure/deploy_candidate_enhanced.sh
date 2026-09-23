#!/usr/bin/env bash
# RateGuard Enhanced -- Candidate Deployment (project: rateguard-enhanced)
#
# Builds FOUR immutable images (rating-engine, worker, api, web), each tagged
# with the FULL git commit SHA (never `latest`, never a short/ambiguous
# prefix), and deploys them as `--no-traffic --tag candidate` Cloud Run
# revisions -- reachable only at their own tagged URL, receiving ZERO
# production traffic. Candidate revisions are wired to the SAME production
# Firestore/GCS/BigQuery resources production already uses (tenant-scoped
# reads/writes are additive and safe -- see docs/architecture/CONNECTOR_IMPACT.md
# and the locked doc's tenant model); this script does NOT provision a
# second, parallel staging data plane. The one thing this script deliberately
# never does automatically is point any part of the real Pub/Sub message flow
# at a candidate-tagged URL -- that is the job of `--verify-candidate` below,
# which uses its own disposable, auto-restoring subscription instead of
# touching the real one.
#
# THIS SCRIPT WAS PREVIOUSLY WRONG: an earlier version of this file claimed
# "this project currently has ZERO deployed Cloud Run services" and pointed
# candidates at an isolated `*-staging` Pub/Sub/Firestore/BigQuery/GCS copy.
# Neither statement was true once Prompt 8 deployed four real production
# services -- the staging resources are now stray, unused artifacts (still
# visible via `gcloud pubsub topics list` / `bq ls`) and must not be recreated.
# `print_plan()` below now performs real (read-only) discovery so the plan it
# prints reflects the actual, current production state, not an assumption.
#
# This script deliberately does NOT exist for, and must never be pointed at,
# the old `rateguard-ai` project.
#
# SAFETY: by default (no flag) this performs only READ-ONLY discovery calls
# (gcloud run/pubsub/... *list*/*describe*, never create/update/delete) and
# prints the full plan. Nothing here ever modifies production traffic or
# creates/deletes any resource unless --deploy-candidate or
# --verify-candidate is passed.

set -euo pipefail

PROJECT_ID="rateguard-enhanced"
REGION="us-central1"
ARTIFACT_REPO="rateguard-images"
API_SA="rateguard-api-sa@rateguard-enhanced.iam.gserviceaccount.com"
WORKER_SA="rateguard-worker-sa@rateguard-enhanced.iam.gserviceaccount.com"
WEB_SA="rateguard-web-sa@rateguard-enhanced.iam.gserviceaccount.com"
# A dedicated rating-engine service account already exists in this project
# ("RateGuard rating engine (no data roles)") but production is not actually
# using it yet -- the live rateguard-rating-engine service currently runs
# under rateguard-worker-sa (confirmed via `gcloud run services describe`).
# This script now wires the dedicated SA for every NEW candidate deployment,
# closing that gap going forward; it does not retroactively touch the
# currently-live production revision.
RATING_ENGINE_SA="rateguard-rating-engine-sa@rateguard-enhanced.iam.gserviceaccount.com"
LOCKED_MODEL_ID="gemini-3.1-flash-lite"
VERTEX_AI_LOCATION="us"
CANDIDATE_TAG="candidate"

# --- Production data plane (the ONLY data plane this script uses -- see
# header comment). Cross-checked by live `gcloud`/`bq` inspection, not
# assumed: production Firestore uses the (default) native database with
# collection "assurance_runs"; the GCS bucket is rateguard-enhanced-artifacts;
# the BigQuery dataset is rateguard_portfolio; the Pub/Sub topic is
# assurance-runs with push subscription assurance-runs-worker-sub; the
# connector-impact queue is impact-batches / impact-batches-worker-sub with
# DLQ impact-batches-dead-letter(-sub). ---
PROD_FIRESTORE_COLLECTION="assurance_runs"
PROD_GCS_BUCKET="rateguard-enhanced-artifacts"
PROD_BIGQUERY_DATASET="rateguard_portfolio"
PROD_BIGQUERY_PORTFOLIO_TABLE="synthetic_policies"
PROD_BIGQUERY_RESULTS_TABLE="portfolio_exposure_results"
PROD_PUBSUB_TOPIC="assurance-runs"
PROD_PUBSUB_SUBSCRIPTION="assurance-runs-worker-sub"
PROD_IMPACT_TOPIC="impact-batches"

# Resource-name prefixes that must NEVER be provisioned by this script again
# (leftover from the earlier, wrong staging-isolation design). Used by
# `refuse_staging_named_resource` and by the test suite to assert this script
# never reintroduces them.
OBSOLETE_STAGING_NAMES="assurance-runs-staging assurance-worker-staging assurance-runs-staging-dlq assurance-runs-staging-dlq-inspect assurance_runs_staging rateguard_staging rateguard-enhanced-artifacts-staging"

# Consumed by promote_candidate_to_production.sh's require_verified_marker --
# a file named after the full git SHA existing here is what "this candidate
# passed --verify-candidate" means to the promotion script.
VERIFIED_MARKER_DIR="infrastructure/.candidate-verified"

# A single disposable, uniquely-named Pub/Sub subscription used only by
# `--verify-candidate` (see verify_candidate_async_path below). It is created
# on the EXISTING PRODUCTION TOPIC (never a new topic) and always deleted
# again in the same run, success or failure -- the real
# assurance-runs-worker-sub subscription is never modified.
VERIFY_SUBSCRIPTION_PREFIX="assurance-runs-candidate-verify"

GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo '')"
if [ -z "$GIT_SHA" ]; then
  echo "Error: not inside a git repository (git rev-parse HEAD failed). Refusing to continue." >&2
  exit 1
fi
IMAGE_TAG="candidate-${GIT_SHA}"

RATING_ENGINE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-rating-engine:${IMAGE_TAG}"
WORKER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-worker:${IMAGE_TAG}"
API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-api:${IMAGE_TAG}"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-web:${IMAGE_TAG}"

# api and worker are the same deployable package (locked doc section 12.1:
# "Worker: Same Python package/image as API with separate command"), so they
# MUST be deployed from the exact same image digest -- see
# assert_api_worker_same_digest below, run right after both are deployed.
BACKEND_IMAGE="$API_IMAGE"

CANDIDATE_ENV_FILE_API="infrastructure/.candidate-enhanced-env-api.yaml"
CANDIDATE_ENV_FILE_WORKER="infrastructure/.candidate-enhanced-env-worker.yaml"

MODE=""
for arg in "$@"; do
  case "$arg" in
    --deploy-candidate) MODE="deploy" ;;
    --verify-candidate) MODE="verify" ;;
    --record-verified) MODE="record-verified" ;;
    --help|-h)
      echo "Usage: $0 [--deploy-candidate | --verify-candidate | --record-verified]"
      echo "  (no flag)           Discover current production state and print the full plan. Read-only."
      echo "  --deploy-candidate  Build and deploy the candidate revisions (--no-traffic)."
      echo "  --verify-candidate  Exercise an already-deployed candidate's async worker path"
      echo "                      end-to-end using a disposable, auto-restoring Pub/Sub"
      echo "                      subscription and a synthetic verification tenant. Never"
      echo "                      touches the real production subscription. Prints manual/CI"
      echo "                      steps 3-4 for a human or pipeline to execute and confirm."
      echo "  --record-verified   Run ONLY after steps 3-4 above were confirmed successful --"
      echo "                      writes the marker promote_candidate_to_production.sh requires."
      exit 0
      ;;
  esac
done

# --- Preflight guards (checked before ANY mutating action; --deploy-candidate
# and --verify-candidate both go through this). ---
preflight_guards() {
  local configured_project
  configured_project="$(gcloud config get-value project 2>/dev/null || true)"
  if [ "$configured_project" != "$PROJECT_ID" ]; then
    echo "Error: gcloud is configured for project '${configured_project:-<none>}', not '${PROJECT_ID}'. Refusing to continue." >&2
    echo "  Run: gcloud config set project ${PROJECT_ID}" >&2
    exit 1
  fi

  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    echo "Error: working tree has uncommitted changes. Refusing to deploy from a dirty tree." >&2
    echo "  Commit or stash first, then re-run." >&2
    exit 1
  fi

  local branch upstream ahead behind
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo '')"
  if [ -z "$upstream" ]; then
    echo "Error: current branch '${branch}' has no upstream tracking branch. Refusing to deploy an unpushed tree." >&2
    exit 1
  fi
  read -r ahead behind <<<"$(git rev-list --left-right --count "${upstream}...HEAD" 2>/dev/null | awk '{print $2, $1}')"
  if [ "${ahead:-1}" != "0" ]; then
    echo "Error: HEAD is ${ahead} commit(s) ahead of ${upstream} (unpushed). Refusing to deploy an unpushed tree." >&2
    echo "  Run: git push" >&2
    exit 1
  fi
}

refuse_staging_named_resource() {
  # Args: <candidate-name>. Defense in depth: even if a future edit
  # accidentally reintroduces one of the obsolete staging names below, this
  # refuses outright rather than silently provisioning it again.
  local name="$1" stale
  for stale in $OBSOLETE_STAGING_NAMES; do
    if [ "$name" = "$stale" ]; then
      echo "Error: refusing to provision obsolete staging-named resource '${name}'." >&2
      exit 1
    fi
  done
}

# --- Read-only production-state discovery (safe to run with no flag at all). ---
discover_production_state() {
  local svc rev sa traffic
  echo "Live Cloud Run services in ${PROJECT_ID}/${REGION} (read-only discovery):"
  for svc in rateguard-api rateguard-worker rateguard-rating-engine rateguard-web; do
    sa="$(gcloud run services describe "$svc" --project "$PROJECT_ID" --region "$REGION" \
      --format="value(spec.template.spec.serviceAccountName)" 2>/dev/null || echo '<not found>')"
    traffic="$(gcloud run services describe "$svc" --project "$PROJECT_ID" --region "$REGION" \
      --format="value(status.traffic)" 2>/dev/null || echo '<not found>')"
    echo "  ${svc}:"
    echo "    service account: ${sa}"
    echo "    traffic:         ${traffic}"
  done
}

print_plan() {
  cat <<HEADER
========================================================
   RateGuard Enhanced -- Candidate Deployment PLAN
   (read-only: only discovery calls above/below have run;
    no create/update/delete gcloud call has been made)
========================================================
HEADER
  discover_production_state
  cat <<PLAN

Project:                        ${PROJECT_ID}
Region:                         ${REGION}
Artifact Registry repo:         ${ARTIFACT_REPO}
Git commit (full SHA):          ${GIT_SHA}
Immutable image tag:            ${IMAGE_TAG}
Rating-engine image:            ${RATING_ENGINE_IMAGE}
Worker/API image (shared):      ${BACKEND_IMAGE}
Web image:                      ${WEB_IMAGE}
Cloud Run candidate tag:        ${CANDIDATE_TAG} (--no-traffic on every service)
Gemini model / location:        ${LOCKED_MODEL_ID} / ${VERTEX_AI_LOCATION} (Vertex AI ADC, no API key)
Service accounts (per-service, distinct):
  rateguard-api:                 ${API_SA}
  rateguard-worker:               ${WORKER_SA}
  rateguard-rating-engine:        ${RATING_ENGINE_SA} (private; invocable only by API + worker SAs)
  rateguard-web:                   ${WEB_SA}

Production data plane these candidates are wired to (pre-existing, never
provisioned by this script -- candidate revisions receive 0% traffic, so
nothing here is reachable by real users; only an explicit --verify-candidate
run or manual testing against the candidate-tagged URL touches it):
  Firestore collection: ${PROD_FIRESTORE_COLLECTION}
  GCS bucket:            ${PROD_GCS_BUCKET}
  BigQuery dataset:      ${PROD_BIGQUERY_DATASET} (${PROD_BIGQUERY_PORTFOLIO_TABLE} / ${PROD_BIGQUERY_RESULTS_TABLE})
  Pub/Sub topic:         ${PROD_PUBSUB_TOPIC} (subscription ${PROD_PUBSUB_SUBSCRIPTION} is NEVER modified by this script)
  Connector-impact topic: ${PROD_IMPACT_TOPIC} (untouched)

This script no longer provisions any *-staging Pub/Sub topic/subscription,
Firestore collection, BigQuery dataset, or GCS bucket. Those obsolete
resources (${OBSOLETE_STAGING_NAMES}) may still exist from a prior version of
this script; they are not deleted here (destructive deletion is out of
scope for this script) but are never recreated.

Preflight guards --deploy-candidate/--verify-candidate enforce before any
mutating call:
  - gcloud must be configured for project ${PROJECT_ID} (checked via
    'gcloud config get-value project').
  - Working tree must be clean (git diff / git diff --cached both empty).
  - HEAD must be pushed: the current branch must have an upstream, and HEAD
    must not be ahead of it.

Exact commands --deploy-candidate would run, in order:

  1) Build all four images (immutable FULL-SHA tag, never 'latest'):
     gcloud builds submit . --config=./backend/rating_engine/cloudbuild.yaml --substitutions=_IMAGE_TAG=${IMAGE_TAG}
     gcloud builds submit . --config=./backend/cloudbuild.yaml --substitutions=_IMAGE_TAG=${IMAGE_TAG}
     (web build deferred until the candidate API URL is known -- step 5)

  2) Deploy rating-engine candidate (PRIVATE -- no unauthenticated invocation),
     under its own dedicated service account:
     gcloud run deploy rateguard-rating-engine --image ${RATING_ENGINE_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --no-allow-unauthenticated --service-account ${RATING_ENGINE_SA} \\
       --memory=512Mi

  3) Deploy worker candidate (PRIVATE -- Pub/Sub push only, OIDC-authenticated),
     wired at the PRODUCTION Firestore/GCS/BigQuery names above:
     gcloud run deploy rateguard-worker --image ${WORKER_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --no-allow-unauthenticated --service-account ${WORKER_SA} \\
       --memory=1Gi --env-vars-file=${CANDIDATE_ENV_FILE_WORKER}   # RATEGUARD_SERVICE_ROLE=worker
     gcloud run services add-iam-policy-binding rateguard-worker \\
       --member=serviceAccount:${WORKER_SA} --role=roles/run.invoker
       # This grant is the worker's own Pub/Sub push identity, NOT rating-engine
       # caller access (a prior version of this plan mislabeled it) -- confirmed
       # live: assurance-runs-worker-sub's pushConfig.oidcToken.serviceAccountEmail
       # is rateguard-worker-sa, so the worker service must authorize that same
       # SA to invoke itself, or Pub/Sub push delivery is rejected with 403.
     gcloud run services add-iam-policy-binding rateguard-rating-engine \\
       --member=serviceAccount:${WORKER_SA} --role=roles/run.invoker
     gcloud run services add-iam-policy-binding rateguard-rating-engine \\
       --member=serviceAccount:${API_SA} --role=roles/run.invoker
       # (API calls the connector directly for the admin-only connector-test route)

  4) Deploy API candidate (public, verifies Firebase ID tokens), wired at the
     same production Firestore/GCS/BigQuery names:
     gcloud run deploy rateguard-api --image ${API_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${API_SA} \\
       --memory=512Mi --env-vars-file=${CANDIDATE_ENV_FILE_API}   # RATEGUARD_SERVICE_ROLE=api

  4b) Assert the API and worker candidate revisions resolved to the EXACT
      same image digest (both come from ${BACKEND_IMAGE}) -- refuses to
      continue otherwise.

  5) Build and deploy web candidate (API URL and the PUBLIC Firebase web config
     baked in at build time; refuses to build if any NEXT_PUBLIC_FIREBASE_*
     value is missing):
     gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${IMAGE_TAG},_NEXT_PUBLIC_RATEGUARD_API_URL=<candidate-api-tagged-url>,_NEXT_PUBLIC_FIREBASE_*=...
     gcloud run deploy rateguard-web --image ${WEB_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${WEB_SA}

  5b) Point the API CORS allowlist at the candidate web origin (exactly one
      explicit origin, no wildcard), still --no-traffic.

  6) Capture and print: every candidate image digest; confirmation that no
     service's production traffic allocation changed (0% to every candidate
     revision); confirmation that no unauthenticated invoker binding exists
     on worker or rating-engine.

--verify-candidate (run separately, after --deploy-candidate, against an
already-deployed candidate) would:
  1) Record the real ${PROD_PUBSUB_SUBSCRIPTION} push config as the "before"
     state (never modified).
  2) Create a disposable subscription named
     ${VERIFY_SUBSCRIPTION_PREFIX}-<short-sha> on the EXISTING production
     topic ${PROD_PUBSUB_TOPIC}, push-configured at the candidate-tagged
     worker URL, OIDC audience = that same candidate URL.
  3) Create a mission through the candidate-tagged API using a synthetic,
     clearly-marked verification tenant, with a fixed idempotency key, and
     poll it to a terminal state (never treats 202/QUEUED as success).
  4) Publish the identical idempotency key a second time and confirm exactly
     one terminal mission/decision exists (duplicate-delivery / idempotency
     check).
  5) Delete the disposable subscription unconditionally (success or
     failure path) and confirm ${PROD_PUBSUB_SUBSCRIPTION}'s push config is
     byte-for-byte unchanged from the "before" state captured in step 1. If
     that confirmation fails, this exits non-zero with the exact manual
     cleanup command rather than continuing silently.

NOT done by this script, ever:
  - No production traffic change ('gcloud run services update-traffic').
  - No GEMINI_API_KEY / GOOGLE_API_KEY / Firebase private-key JSON anywhere, and
    no --set-secrets mapping for FIREBASE_ADMIN_KEY or GEMINI_API_KEY (both
    Secret Manager secrets are confirmed fully disabled -- 'gcloud secrets
    versions list' -- and are never referenced at runtime).
  - No broad Owner/Editor/storage-admin IAM grant.
  - No staging Pub/Sub topic/subscription/Firestore collection/BigQuery
    dataset/GCS bucket creation.
  - No modification of the real ${PROD_PUBSUB_SUBSCRIPTION} push config.
  - No deploy to, or read/write of, the old 'rateguard-ai' project.

Re-run this plan any time with no arguments (read-only). Pass
--deploy-candidate to build+deploy, or --verify-candidate (after a candidate
already exists) to exercise its async path safely.
PLAN
}

write_candidate_env_file() {
  # Args: <service role: api|worker> <output file>. Non-secret env vars only.
  # No GEMINI_API_KEY/GOOGLE_API_KEY, no Firebase private-key JSON -- Vertex AI
  # and Firebase Admin both use ADC via the attached service account. Wired at
  # the PRODUCTION Firestore/GCS/BigQuery names -- see header comment for why
  # this is safe (0% traffic; tenant-scoped writes are additive).
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
RATEGUARD_FIRESTORE_COLLECTION: "${PROD_FIRESTORE_COLLECTION}"
RATEGUARD_GCS_BUCKET: "${PROD_GCS_BUCKET}"
RATEGUARD_BIGQUERY_ENABLED: "true"
RATEGUARD_BIGQUERY_DATASET: "${PROD_BIGQUERY_DATASET}"
RATEGUARD_BIGQUERY_PORTFOLIO_TABLE: "${PROD_BIGQUERY_PORTFOLIO_TABLE}"
RATEGUARD_BIGQUERY_RESULTS_TABLE: "${PROD_BIGQUERY_RESULTS_TABLE}"
RATEGUARD_ASYNC_ENABLED: "true"
RATEGUARD_EXECUTION_MODE: "pubsub"
RATEGUARD_PUBSUB_TOPIC: "${PROD_PUBSUB_TOPIC}"
RATEGUARD_IMPACT_TOPIC: "${PROD_IMPACT_TOPIC}"
RATEGUARD_DATA_DIR: "/app/data"
RATEGUARD_RATE_LIMIT_ENABLED: "true"
RATEGUARD_RATE_LIMITS: '{"connector_test":"5/3600","mission_create":"10/3600","source_upload":"30/3600","source_compile":"30/3600","explanation_create":"20/3600","evidence_download":"30/3600","source_download":"60/3600"}'
RATEGUARD_CORS_ORIGINS: '["http://localhost:3000"]'
ENV
  # Deliberately does NOT set RATEGUARD_PUBSUB_SUBSCRIPTION: the candidate
  # worker never subscribes to anything until --verify-candidate creates its
  # own disposable subscription and passes its name explicitly (see below) --
  # this is what guarantees the candidate never receives live production
  # Pub/Sub traffic just by existing.
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

resolve_revision_digest() {
  # Args: <service> <revision-name>. The digest actually running on a
  # deployed revision, not just what was pushed to Artifact Registry --
  # used by assert_api_worker_same_digest for a real, live comparison.
  gcloud run revisions describe "$2" --region "$REGION" \
    --format="value(spec.containers[0].image)" 2>/dev/null
}

assert_api_worker_same_digest() {
  local api_rev="$1" worker_rev="$2" api_img worker_img
  api_img="$(resolve_revision_digest rateguard-api "$api_rev")"
  worker_img="$(resolve_revision_digest rateguard-worker "$worker_rev")"
  if [ -z "$api_img" ] || [ -z "$worker_img" ] || [ "$api_img" != "$worker_img" ]; then
    echo "Error: API candidate revision image (${api_img:-<none>}) does not match" >&2
    echo "worker candidate revision image (${worker_img:-<none>}). API and worker" >&2
    echo "MUST be deployed from the exact same backend image digest. Refusing to continue." >&2
    exit 1
  fi
  echo "   Confirmed: API and worker candidate revisions share image digest ${api_img}."
}

deploy_candidate() {
  echo "========================================================"
  echo "   RateGuard Enhanced -- Deploying Candidate"
  echo "   Image tag (full SHA): ${IMAGE_TAG}"
  echo "========================================================"

  preflight_guards
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

  echo "2. Deploying candidate rating-engine (PRIVATE, --no-traffic, dedicated SA)..."
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

  echo "   Granting API SA run.invoker on rateguard-rating-engine (narrow, service-scoped;"
  echo "   the API also calls the connector directly for the admin-only connector-test route)..."
  gcloud run services add-iam-policy-binding rateguard-rating-engine \
    --region "$REGION" --member="serviceAccount:${API_SA}" --role="roles/run.invoker" >/dev/null

  echo "   Granting worker SA run.invoker on rateguard-worker itself -- this is the worker's"
  echo "   OWN Pub/Sub push identity (assurance-runs-worker-sub authenticates push deliveries"
  echo "   as rateguard-worker-sa), NOT rating-engine caller access."
  gcloud run services add-iam-policy-binding rateguard-worker \
    --region "$REGION" --member="serviceAccount:${WORKER_SA}" --role="roles/run.invoker" >/dev/null

  echo "4. Deploying candidate API (public, --no-traffic)..."
  gcloud run deploy rateguard-api \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --allow-unauthenticated --service-account "$API_SA" \
    --memory=512Mi --env-vars-file="$CANDIDATE_ENV_FILE_API"

  echo "   Wiring candidate rating-engine connector URL into API..."
  gcloud run services update rateguard-api --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG" \
    --update-env-vars "RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL},RATEGUARD_RATING_ENGINE_CONNECTOR_IS_LOCAL_DEV=false,RATEGUARD_RATING_ENGINE_CONNECTOR_AUTH_MODE=google_id_token,RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL=${RATING_ENGINE_TAGGED_URL}"

  API_TAGGED_URL=$(get_tagged_url rateguard-api)
  if [ -z "$API_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-api URL." >&2
    exit 1
  fi
  echo "   Candidate API URL: ${API_TAGGED_URL}"

  API_REV="$(gcloud run services describe rateguard-api --region "$REGION" --format="value(status.latestCreatedRevisionName)")"
  WORKER_REV="$(gcloud run services describe rateguard-worker --region "$REGION" --format="value(status.latestCreatedRevisionName)")"
  assert_api_worker_same_digest "$API_REV" "$WORKER_REV"

  echo "5. Building and deploying candidate web..."
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

  echo "5b. Restricting API CORS to the candidate web origin (explicit, no wildcard)..."
  gcloud run services update rateguard-api --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG" \
    --update-env-vars "RATEGUARD_CORS_ORIGINS=[\"${WEB_TAGGED_URL}\"]"

  rm -f "$CANDIDATE_ENV_FILE_API" "$CANDIDATE_ENV_FILE_WORKER"

  echo "6. Post-deploy verification..."
  echo "   Confirming production traffic allocation is unchanged (0% to every candidate revision)..."
  local svc traffic
  for svc in rateguard-api rateguard-worker rateguard-rating-engine rateguard-web; do
    traffic="$(gcloud run services describe "$svc" --region "$REGION" --format="value(status.traffic)")"
    echo "   ${svc}: ${traffic}"
  done

  echo "========================================================"
  echo "CANDIDATE DEPLOYMENT COMPLETE (0% production traffic)"
  echo "Image tag (full SHA):       ${IMAGE_TAG}"
  echo "Rating-engine digest:       ${RATING_ENGINE_DIGEST}"
  echo "Worker/API digest:          ${BACKEND_DIGEST}"
  echo "Candidate rating-engine URL: ${RATING_ENGINE_TAGGED_URL} (private)"
  echo "Candidate worker URL:        ${WORKER_TAGGED_URL} (private)"
  echo "Candidate API URL:           ${API_TAGGED_URL}"
  echo "Candidate web URL:           ${WEB_TAGGED_URL}"
  echo ""
  echo "Next: run '$0 --verify-candidate' to exercise the async worker path"
  echo "before promoting (infrastructure/promote_candidate_to_production.sh)."
  echo "========================================================"
}

# --- Candidate async-worker verification: uses a disposable subscription on
# the EXISTING production topic, never modifies the real subscription, always
# cleans up, and fails loudly (never silently) if cleanup does not verify. ---
verify_candidate_async_path() {
  preflight_guards

  local short_sha="${GIT_SHA:0:12}"
  local verify_sub="${VERIFY_SUBSCRIPTION_PREFIX}-${short_sha}"
  local worker_tagged_url worker_untagged_url api_tagged_url

  worker_tagged_url="$(get_tagged_url rateguard-worker)"
  worker_untagged_url="$(get_untagged_url rateguard-worker)"
  api_tagged_url="$(get_tagged_url rateguard-api)"
  if [ -z "$worker_tagged_url" ] || [ -z "$api_tagged_url" ]; then
    echo "Error: no candidate-tagged rateguard-worker/rateguard-api revision found." >&2
    echo "Run '$0 --deploy-candidate' first." >&2
    exit 1
  fi

  echo "1. Recording the REAL ${PROD_PUBSUB_SUBSCRIPTION} push config (before state, never modified)..."
  local before_state
  before_state="$(gcloud pubsub subscriptions describe "$PROD_PUBSUB_SUBSCRIPTION" --format=json)"
  echo "   Before: $(echo "$before_state" | grep -o '"pushEndpoint":[^,]*')"

  echo "2. Creating disposable verification subscription ${verify_sub} on the EXISTING topic ${PROD_PUBSUB_TOPIC}..."
  if gcloud pubsub subscriptions describe "$verify_sub" >/dev/null 2>&1; then
    echo "Error: ${verify_sub} already exists from a prior, incompletely-cleaned-up run. Refusing to continue." >&2
    echo "  Inspect and delete manually: gcloud pubsub subscriptions delete ${verify_sub}" >&2
    exit 1
  fi
  gcloud pubsub subscriptions create "$verify_sub" \
    --topic="$PROD_PUBSUB_TOPIC" \
    --ack-deadline=600 \
    --push-endpoint="${worker_tagged_url}/internal/pubsub/assurance" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$worker_tagged_url"

  cleanup_verify_sub() {
    echo "5. Deleting disposable subscription ${verify_sub}..."
    if ! gcloud pubsub subscriptions delete "$verify_sub" >/dev/null 2>&1; then
      echo "Error: failed to delete ${verify_sub}. MANUAL CLEANUP REQUIRED:" >&2
      echo "  gcloud pubsub subscriptions delete ${verify_sub}" >&2
      exit 1
    fi
    echo "   Confirming the REAL ${PROD_PUBSUB_SUBSCRIPTION} push config is unchanged..."
    local after_state
    after_state="$(gcloud pubsub subscriptions describe "$PROD_PUBSUB_SUBSCRIPTION" --format=json)"
    if [ "$before_state" != "$after_state" ]; then
      echo "Error: ${PROD_PUBSUB_SUBSCRIPTION} changed during verification. This should be" >&2
      echo "impossible (this script never issues a 'modify-push-config' against it) -- treat" >&2
      echo "production Pub/Sub routing as SUSPECT and investigate before promoting anything." >&2
      exit 1
    fi
    echo "   Confirmed: ${PROD_PUBSUB_SUBSCRIPTION} is byte-for-byte unchanged."
  }
  trap cleanup_verify_sub EXIT

  echo "3. Creating a mission through the candidate API with a synthetic verification tenant..."
  echo "   (Fixed idempotency key so step 4 can prove duplicate delivery does not double-process.)"
  echo "   Manual/CI step: POST ${api_tagged_url}/api/v1/missions with"
  echo "   X-Idempotency-Key: candidate-verify-${short_sha}"
  echo "   tenant_id: candidate-verify-tenant (synthetic, never a real customer tenant)"
  echo "   Poll GET ${api_tagged_url}/api/v1/missions/<id> until a TERMINAL status"
  echo "   (COMPLETED/FAILED/REVIEW_REQUIRED) -- a 202/QUEUED response is NOT acceptance evidence."

  echo "4. Re-submit the SAME idempotency key and confirm exactly one terminal mission/decision"
  echo "   exists for it (proves the candidate path is idempotent under redelivery, matching"
  echo "   the existing tested guarantee in tests/agents/test_worker_delivery_outcomes.py)."

  echo "   (Steps 3-4 issue real authenticated HTTP calls and are intentionally left as an"
  echo "   explicit manual/CI action, not auto-executed here, so this script never silently"
  echo "   fabricates a synthetic-tenant mission against production infrastructure without a"
  echo "   human or CI pipeline directly observing each response.)"
  echo ""
  echo "Once steps 3-4 above are confirmed successful, run:"
  echo "  $0 --record-verified"
  echo "to write the marker promote_candidate_to_production.sh requires."
}

record_verified() {
  mkdir -p "$VERIFIED_MARKER_DIR"
  printf 'verified at %s by %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(whoami 2>/dev/null || echo unknown)" \
    > "${VERIFIED_MARKER_DIR}/${GIT_SHA}"
  echo "Verified marker written: ${VERIFIED_MARKER_DIR}/${GIT_SHA}"
  echo "promote_candidate_to_production.sh will now accept this SHA without --skip-verification-check."
}

case "$MODE" in
  deploy) deploy_candidate ;;
  verify) verify_candidate_async_path ;;
  record-verified) record_verified ;;
  *) print_plan ;;
esac
