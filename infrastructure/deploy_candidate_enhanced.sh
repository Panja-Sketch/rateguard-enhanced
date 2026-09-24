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
# at a candidate-tagged URL -- that is the job of the three-step verification
# lifecycle below (--prepare-verification, then an OBSERVED manual mission,
# then --complete-verification, or --abort-verification), which uses its own
# disposable, SHA-scoped mission AND impact topics/subscriptions and restores
# every candidate setting it changed.
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
# creates/deletes any resource unless --deploy-candidate,
# --prepare-verification, --complete-verification or --abort-verification is
# passed.

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
PROD_IMPACT_SUBSCRIPTION="impact-batches-worker-sub"

# Resource-name prefixes that must NEVER be provisioned by this script again
# (leftover from the earlier, wrong staging-isolation design). Used by
# `refuse_staging_named_resource` and by the test suite to assert this script
# never reintroduces them.
OBSOLETE_STAGING_NAMES="assurance-runs-staging assurance-worker-staging assurance-runs-staging-dlq assurance-runs-staging-dlq-inspect assurance_runs_staging rateguard_staging rateguard-enhanced-artifacts-staging"

# Consumed by promote_candidate_to_production.sh's require_verified_marker.
# Per full git SHA, in this directory (git-ignored, local, credential-free):
#   <sha>.pending.json  a verification is PREPARED (temporary topics/env in place)
#                       -- promotion refuses while it exists
#   <sha>.aborted       the verification was aborted -- promotion refuses
#   <sha>.evidence      written ONLY by --complete-verification: pinned digests of
#                       all four verified images + revisions + mission id, with
#                       VERIFICATION_COMPLETE=true
#   <sha>               written ONLY by --record-verified, and only when the
#                       evidence above exists and nothing is pending/aborted
# None of these is deleted by cleanup of the temporary Pub/Sub resources --
# audit evidence must survive ephemeral-resource cleanup.
VERIFIED_MARKER_DIR="infrastructure/.candidate-verified"

# SHA-scoped, fully ISOLATED verification topics/subscriptions (see the
# lifecycle comment further down). An earlier version created a disposable
# *subscription* on the production topic (assurance-runs); Pub/Sub fan-out then
# delivered every candidate message to the real production worker too. Neither
# production topic (assurance-runs, impact-batches) may ever be published to or
# subscribed to by candidate verification.
VERIFY_TOPIC_PREFIX="assurance-runs-candidate-verify"
VERIFY_IMPACT_TOPIC_PREFIX="impact-batches-candidate-verify"
VERIFY_SUBSCRIPTION_SUFFIX="-sub"

# Operator-side checker (ADC only; never reads a token) run by --complete-verification.
HELPER_SCRIPT="backend/scripts/verify_candidate_mission.py"

GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo '')"
if [ -z "$GIT_SHA" ]; then
  echo "Error: not inside a git repository (git rev-parse HEAD failed). Refusing to continue." >&2
  exit 1
fi
IMAGE_TAG="candidate-${GIT_SHA}"
# All relative paths below (marker dir, env files, helper) are repo-root relative.
cd "$(git rev-parse --show-toplevel)"

RATING_ENGINE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-rating-engine:${IMAGE_TAG}"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-web:${IMAGE_TAG}"

# api and worker are the same deployable package (locked doc section 12.1:
# "Worker: Same Python package/image as API with separate command"), so they
# MUST be deployed from the exact same image digest. There is deliberately no
# separate WORKER_IMAGE variable -- a prior version of this script declared
# one (pointed at a DIFFERENT Artifact Registry repository path,
# rateguard-worker instead of rateguard-api) and the printed plan quoted it
# for the worker deploy command while the real deploy_candidate() function
# below always used BACKEND_IMAGE for both, so the plan and reality silently
# diverged. BACKEND_IMAGE is now the ONLY image reference either the plan
# text or the real gcloud calls may use for api/worker -- see
# assert_api_worker_same_digest below, run right after both are deployed, and
# test_enhanced_deploy_api_and_worker_share_one_backend_image_variable /
# test_enhanced_deploy_plan_worker_and_api_reference_the_same_image_variable.
BACKEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/rateguard-api:${IMAGE_TAG}"

CANDIDATE_ENV_FILE_API="infrastructure/.candidate-enhanced-env-api.yaml"
CANDIDATE_ENV_FILE_WORKER="infrastructure/.candidate-enhanced-env-worker.yaml"

PY=python3
if ! "$PY" -c "" >/dev/null 2>&1; then PY=python; fi
HELPER_PYTHON="${VERIFY_PYTHON:-$PY}"

PENDING_STATE_FILE="${VERIFIED_MARKER_DIR}/${GIT_SHA}.pending.json"
ABORTED_MARKER="${VERIFIED_MARKER_DIR}/${GIT_SHA}.aborted"
PROD_SUBS_DIR="${VERIFIED_MARKER_DIR}/${GIT_SHA}.prod-subs"

# Trap-state and lifecycle variables: ALL initialised here, at top level,
# BEFORE any trap is installed, so an EXIT trap can never hit an unbound
# variable under `set -u` (the original defect: a function-local flag read by
# an EXIT trap after that function had returned).
PREPARE_ARMED=false
CLEANUP_DONE=false
MISSION_ID=""
MISSION_TOPIC=""
MISSION_SUB=""
IMPACT_VERIFY_TOPIC=""
IMPACT_VERIFY_SUB=""

usage() {
  echo "Usage: $0 [--deploy-candidate | --prepare-verification | --complete-verification --mission-id=<ID> | --abort-verification | --record-verified]"
  echo "  (no flag)                 Discover current production state and print the full plan. Read-only."
  echo "  --deploy-candidate        Build and deploy the candidate revisions (--no-traffic)."
  echo "  --prepare-verification    Create SHA-scoped temporary mission + impact topics/subscriptions, point the"
  echo "                            candidate publishers ONLY at them, and leave the environment in place for one"
  echo "                            observed manual mission. Restores everything itself on failure or signal."
  echo "  --complete-verification --mission-id=<ID>"
  echo "                            Check the finished mission + evidence bundle, prove duplicate delivery is"
  echo "                            harmless, restore + delete the temporary environment, pin verified digests."
  echo "  --abort-verification      Idempotently restore the candidate environment and delete the temporary"
  echo "                            resources. Evidence in Firestore/GCS is preserved."
  echo "  --record-verified         Run ONLY after --complete-verification succeeded -- writes the marker"
  echo "                            promote_candidate_to_production.sh requires."
}

MODE=""
set_mode() {
  if [ -n "$MODE" ] && [ "$MODE" != "$1" ]; then
    echo "Error: only one mode flag may be given (already '${MODE}', got '$1')." >&2
    exit 2
  fi
  MODE="$1"
}
for arg in "$@"; do
  case "$arg" in
    --deploy-candidate) set_mode deploy ;;
    --prepare-verification) set_mode prepare ;;
    --complete-verification) set_mode complete ;;
    --abort-verification) set_mode abort ;;
    --record-verified) set_mode record-verified ;;
    --mission-id=*) MISSION_ID="${arg#--mission-id=}" ;;
    --verify-candidate)
      echo "Error: --verify-candidate was replaced by the explicit lifecycle" >&2
      echo "  --prepare-verification / --complete-verification --mission-id=<ID> / --abort-verification." >&2
      exit 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Error: unknown argument '${arg}'." >&2; usage >&2; exit 2 ;;
  esac
done
if [ -n "$MISSION_ID" ] && ! [[ "$MISSION_ID" =~ ^MIS-[0-9A-F]{8}$ ]]; then
  echo "Error: --mission-id must look like MIS-1A2B3C4D." >&2
  exit 2
fi
if [ -n "$MISSION_ID" ] && [ "$MODE" != "complete" ]; then
  echo "Error: --mission-id is only valid with --complete-verification." >&2
  exit 2
fi

# --- Preflight guards (checked before ANY mutating action; --deploy-candidate,
# --prepare-verification, --complete-verification and --abort-verification all
# go through this). ---
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
nothing here is reachable by real users; only an explicit --prepare-verification
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

Preflight guards every mutating mode (--deploy-candidate and the three
verification modes) enforce before any
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
       --memory=512Mi \\
       --update-env-vars RATEGUARD_GIT_SHA=<full SHA>,RATEGUARD_IMAGE_DIGEST=<engine image digest>
     The candidate-TAGGED URL is the connector's REQUEST endpoint; the STABLE
     (untagged) service URL is its Google ID-token AUDIENCE
     (RATEGUARD_RATING_ENGINE_CONNECTOR_AUDIENCE): Cloud Run rejects a token
     minted for a traffic-tagged URL with HTTP 401. Both are written into the
     worker/API env files, generated only after the engine's URLs are known.
     The engine image is built from backend/rating_engine only (no RateGuard code).

  3) Deploy worker candidate (PRIVATE -- Pub/Sub push only, OIDC-authenticated),
     wired at the PRODUCTION Firestore/GCS/BigQuery names above -- from the
     SAME image as API (BACKEND_IMAGE), never a separate rateguard-worker
     repository image:
     gcloud run deploy rateguard-worker --image ${BACKEND_IMAGE} \\
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
     same production Firestore/GCS/BigQuery names -- from the SAME image as
     worker (BACKEND_IMAGE):
     gcloud run deploy rateguard-api --image ${BACKEND_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${API_SA} \\
       --memory=512Mi --env-vars-file=${CANDIDATE_ENV_FILE_API}   # RATEGUARD_SERVICE_ROLE=api

  4b) Assert the API and worker candidate revisions resolved to the EXACT
      same image digest (both come from ${BACKEND_IMAGE}) -- refuses to
      continue otherwise.

  5) Build and deploy web candidate. The API URL is deployment-time RUNTIME
     config (RATEGUARD_API_URL, a plain Cloud Run env var, never baked into
     the image) so this SAME image digest can later be promoted unchanged --
     see BLOCKER 3/4 in promote_candidate_to_production.sh. Only the PUBLIC
     Firebase web config (not a secret) is baked in at build time; refuses
     to build if any NEXT_PUBLIC_FIREBASE_* value is missing:
     gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \\
       --substitutions=_IMAGE_TAG=${IMAGE_TAG},_NEXT_PUBLIC_FIREBASE_*=...
     gcloud run deploy rateguard-web --image ${WEB_IMAGE} \\
       --region ${REGION} --no-traffic --tag ${CANDIDATE_TAG} \\
       --allow-unauthenticated --service-account ${WEB_SA} \\
       --update-env-vars RATEGUARD_API_URL=<candidate-api-tagged-url>

  OIDC audience note: the candidate-tagged worker/rating-engine URLs above
  are valid PUSH ENDPOINTS (they route to the specific candidate revision),
  but Cloud Run's proven-working OIDC audience form (see
  infrastructure/setup_impact_pubsub.sh, the live impact-batches
  subscription) is always the service's STABLE, untagged base URL --
  never a --tag URL. --prepare-verification below uses the stable worker URL
  as the push-auth-token-audience for exactly this reason.

  5b) Point the API CORS allowlist at the candidate web origin (exactly one
      explicit origin, no wildcard), still --no-traffic.

  6) Capture and print: every candidate image digest; confirmation that no
     service's production traffic allocation changed (0% to every candidate
     revision); confirmation that no unauthenticated invoker binding exists
     on worker or rating-engine.

Candidate verification is an explicit THREE-STEP lifecycle (run after
--deploy-candidate, against the already-deployed 0%-traffic candidate):

--prepare-verification
  1) Confirm all four candidate revisions exist at 0% traffic and differ from
     production; record the production revisions and the LIVE, byte-for-byte
     configuration of ${PROD_PUBSUB_SUBSCRIPTION} and ${PROD_IMPACT_SUBSCRIPTION}.
  2) Write the pending-state file (infrastructure/.candidate-verified/<sha>.pending.json:
     names, revisions, SHA, digests, URLs, ORIGINAL candidate env values -- never a
     credential) BEFORE the first mutation.
  3) Create the ISOLATED, SHA-scoped topics + push subscriptions
       mission: ${VERIFY_TOPIC_PREFIX}-<sha12>  -> <candidate worker>/internal/pubsub/assurance
       impact:  ${VERIFY_IMPACT_TOPIC_PREFIX}-<sha12> -> <candidate worker>/internal/pubsub/impact-batch
     OIDC audience = the STABLE, untagged worker URL (never a --tag URL). Neither
     production topic (${PROD_PUBSUB_TOPIC}, ${PROD_IMPACT_TOPIC}) is published to or
     subscribed to, so no production worker can ever receive a candidate mission
     or a candidate impact batch.
  4) Point the candidate API's RATEGUARD_PUBSUB_TOPIC/RATEGUARD_IMPACT_TOPIC and
     the candidate worker's RATEGUARD_IMPACT_TOPIC at the temporary topics only,
     then record the FINAL candidate worker revision.
  5) On ANY failure or signal: restore everything and delete the temporary
     resources. On success: leave the environment in place and print the candidate
     web URL plus instructions for one observed, synthetic
     controlled-workbook-versus-versioned-REST-connector mission.

--complete-verification --mission-id=<ID>   (requires the pending-state file)
  Verify the mission reached a terminal decision (QUEUED/RUNNING/202 is not
  success); validate the evidence bundle (operator ADC, no token): deployment.json
  cloud_run_revision == the recorded candidate worker revision and != production,
  git_sha == the full SHA, controlled-workbook + versioned REST connector
  provenance; publish the same job envelope twice to the isolated mission topic
  and confirm one terminal decision and unchanged evidence; confirm both
  production subscriptions are unchanged; restore the candidate env; delete the
  temporary resources; pin all four verified digests into
  infrastructure/.candidate-verified/<sha>.evidence; clear the pending state.
  Any failed check exits non-zero and refuses promotion.

--abort-verification
  Idempotently restore the candidate env from the pending-state file, delete
  ONLY the SHA-scoped temporary resources (already-absent ones are fine),
  confirm the production subscriptions are unchanged, preserve all Firestore/GCS
  evidence, and clear the pending state.

--record-verified (only after --complete-verification succeeded) writes the marker
promote_candidate_to_production.sh requires. Promotion refuses a pending or
aborted verification, and refuses if the candidate tag has moved off the pinned
digests.

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
  - No subscription ever created on, and nothing ever published to, the shared
    production topics ${PROD_PUBSUB_TOPIC} / ${PROD_IMPACT_TOPIC} -- verification
    uses its own isolated, SHA-scoped mission and impact topics, so no candidate
    mission or impact batch can ever reach a production worker via Pub/Sub fan-out.
  - No deploy to, or read/write of, the old 'rateguard-ai' project.

Re-run this plan any time with no arguments (read-only). Pass
--deploy-candidate to build+deploy, or --prepare-verification (after a candidate
already exists) to begin verifying its async path safely.
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
RATEGUARD_GIT_SHA: "${GIT_SHA}"
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
RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL: "${RATING_ENGINE_TAGGED_URL}"
RATEGUARD_RATING_ENGINE_CONNECTOR_AUDIENCE: "${RATING_ENGINE_STABLE_URL}"
RATEGUARD_RATING_ENGINE_CONNECTOR_IS_LOCAL_DEV: "false"
RATEGUARD_RATING_ENGINE_CONNECTOR_AUTH_MODE: "google_id_token"
RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL: "${RATING_ENGINE_TAGGED_URL}"
RATEGUARD_VENDOR_GATEWAY_CONNECTOR_AUDIENCE: "${RATING_ENGINE_STABLE_URL}"
RATEGUARD_IMAGE_DIGEST: "${BACKEND_DIGEST}"
RATEGUARD_RATE_LIMIT_ENABLED: "true"
RATEGUARD_RATE_LIMITS: '{"connector_test":"5/3600","mission_create":"10/3600","source_upload":"30/3600","source_compile":"30/3600","explanation_create":"20/3600","evidence_download":"30/3600","source_download":"60/3600"}'
RATEGUARD_CORS_ORIGINS: '["http://localhost:3000"]'
ENV
  # Deliberately does NOT set RATEGUARD_PUBSUB_SUBSCRIPTION: the candidate
  # worker never subscribes to anything until --prepare-verification creates its
  # own disposable subscriptions (see below) --
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
    --memory=512Mi --port=8080 \
    --update-env-vars "RATEGUARD_GIT_SHA=${GIT_SHA},RATEGUARD_IMAGE_DIGEST=${RATING_ENGINE_DIGEST}"

  RATING_ENGINE_TAGGED_URL=$(get_tagged_url rateguard-rating-engine)
  if [ -z "$RATING_ENGINE_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-rating-engine URL." >&2
    exit 1
  fi
  echo "   Candidate rating-engine endpoint (tagged, request URL): ${RATING_ENGINE_TAGGED_URL}"
  # The Google ID-token AUDIENCE is the stable, untagged service URL. Cloud Run
  # rejects a token minted for a traffic-tagged URL with HTTP 401, so the two
  # settings are deliberately separate and never derived from one another.
  RATING_ENGINE_STABLE_URL=$(get_untagged_url rateguard-rating-engine)
  if [ -z "$RATING_ENGINE_STABLE_URL" ] || [ "$RATING_ENGINE_STABLE_URL" = "$RATING_ENGINE_TAGGED_URL" ]; then
    echo "Error: could not discover the stable (untagged) rateguard-rating-engine URL for the ID-token audience." >&2
    exit 1
  fi
  echo "   Rating-engine ID-token audience (stable, untagged): ${RATING_ENGINE_STABLE_URL}"

  write_candidate_env_file api "$CANDIDATE_ENV_FILE_API"
  write_candidate_env_file worker "$CANDIDATE_ENV_FILE_WORKER"

  echo "3. Deploying candidate worker (PRIVATE, --no-traffic)..."
  gcloud run deploy rateguard-worker \
    --image "$BACKEND_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --no-allow-unauthenticated --service-account "$WORKER_SA" \
    --memory=1Gi --env-vars-file="$CANDIDATE_ENV_FILE_WORKER"

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

  API_TAGGED_URL=$(get_tagged_url rateguard-api)
  if [ -z "$API_TAGGED_URL" ]; then
    echo "Error: could not discover the candidate-tagged rateguard-api URL." >&2
    exit 1
  fi
  echo "   Candidate API URL: ${API_TAGGED_URL}"

  API_REV="$(gcloud run services describe rateguard-api --region "$REGION" --format="value(status.latestCreatedRevisionName)")"
  WORKER_REV="$(gcloud run services describe rateguard-worker --region "$REGION" --format="value(status.latestCreatedRevisionName)")"
  assert_api_worker_same_digest "$API_REV" "$WORKER_REV"

  echo "5. Building and deploying candidate web (API URL is deployment-time"
  echo "   runtime config, RATEGUARD_API_URL -- never baked into this image;"
  echo "   see frontend/src/lib/runtimeConfig.ts)..."
  FIREBASE_SUBSTITUTIONS="$(build_firebase_substitutions)" || exit 1
  gcloud builds submit ./frontend --config=./frontend/cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="$IMAGE_TAG"${FIREBASE_SUBSTITUTIONS}
  gcloud run deploy rateguard-web \
    --image "$WEB_IMAGE" --region "$REGION" --platform managed \
    --no-traffic --tag "$CANDIDATE_TAG" \
    --allow-unauthenticated --service-account "$WEB_SA" \
    --update-env-vars "RATEGUARD_API_URL=${API_TAGGED_URL}"

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
  echo "Next: run '$0 --prepare-verification' to begin verifying the async worker path"
  echo "before promoting (infrastructure/promote_candidate_to_production.sh)."
  echo "========================================================"
}

# --- Candidate verification lifecycle -------------------------------------
#
#   --prepare-verification   creates the SHA-scoped, fully isolated temporary
#                            environment and LEAVES IT IN PLACE for an observed,
#                            manual mission run (it fails closed: on any error
#                            or signal it restores everything itself).
#   --complete-verification  checks the finished mission, proves duplicate
#                            delivery is harmless, restores + deletes, and only
#                            then pins the verified digests.
#   --abort-verification     idempotent restore + delete from the pending state.
#
# Two ISOLATED topic/subscription pairs exist for the duration:
#   mission: assurance-runs-candidate-verify-<sha12>  -> candidate worker /internal/pubsub/assurance
#   impact:  impact-batches-candidate-verify-<sha12>  -> candidate worker /internal/pubsub/impact-batch
# Pub/Sub fan-out delivers every message to EVERY subscription on a topic, so
# neither production topic (assurance-runs, impact-batches) is ever published
# to and no subscription is ever added to one: the production workers can never
# receive a candidate mission or a candidate impact batch.
#
# A pending-state file (infrastructure/.candidate-verified/<sha>.pending.json,
# git-ignored, local, no credentials) is the single source of truth for what to
# restore. It is written BEFORE the first mutation.

tagged_revision_name() {
  # Args: <service>. The revision name currently under --tag candidate.
  gcloud run services describe "$1" --region "$REGION" --format="value(status.traffic)" 2>/dev/null \
    | tr ';' '\n' | grep "'tag': '${CANDIDATE_TAG}'" | sed -E "s/.*'revisionName': '([^']+)'.*/\1/"
}

prod_revision_name() {
  # Args: <service>. The revision currently serving 100% of production traffic.
  gcloud run services describe "$1" --region "$REGION" --format="value(status.traffic)" 2>/dev/null \
    | tr ';' '\n' | grep "'percent': 100" | sed -E "s/.*'revisionName': '([^']+)'.*/\1/"
}

candidate_traffic_percent() {
  # Args: <service>. Traffic percent of the candidate-tagged entry (empty == 0).
  gcloud run services describe "$1" --region "$REGION" --format="value(status.traffic)" 2>/dev/null \
    | tr ';' '\n' | grep "'tag': '${CANDIDATE_TAG}'" | sed -nE "s/.*'percent': ([0-9]+).*/\1/p" | head -n1
}

service_env_value() {
  # Args: <service> <env var>. Prints the value on the service's current
  # template, or __UNSET__. Never prints anything else from the service.
  gcloud run services describe "$1" --region "$REGION" --format=json 2>/dev/null | "$PY" -c '
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(3)
containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [{}]
for entry in containers[0].get("env") or []:
    if entry.get("name") == name:
        print(entry.get("value", ""))
        break
else:
    print("__UNSET__")
' "$2" | tr -d '\r'
}

set_candidate_env() {
  # Args: <service> <KEY=VALUE>... (VALUE __UNSET__ removes the variable). Only
  # ever targets the --no-traffic candidate-tagged revision line.
  local svc="$1"; shift
  local set_args="" remove_args="" pair key val
  for pair in "$@"; do
    key="${pair%%=*}"
    val="${pair#*=}"
    if [ "$val" = "__UNSET__" ]; then
      remove_args="${remove_args:+${remove_args},}${key}"
    else
      set_args="${set_args:+${set_args},}${key}=${val}"
    fi
  done
  local args=(run services update "$svc" --region "$REGION" --no-traffic --tag "$CANDIDATE_TAG")
  if [ -n "$set_args" ]; then args+=(--update-env-vars "$set_args"); fi
  if [ -n "$remove_args" ]; then args+=(--remove-env-vars "$remove_args"); fi
  gcloud "${args[@]}" >/dev/null
}

state_set() {
  # Args: <file> <key=value>... Merges string values into a JSON object file.
  "$PY" - "$@" <<'PYEOF'
import json, os, sys
path, pairs = sys.argv[1], sys.argv[2:]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except FileNotFoundError:
    data = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    data[key] = value
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PYEOF
}

state_get() {
  # Args: <file> <key>. Prints the value ('' when absent).
  "$PY" -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        value = json.load(fh).get(sys.argv[2])
except Exception:
    sys.exit(3)
print("" if value is None else value)
' "$1" "$2" | tr -d '\r'
}

kv_get() {
  # Args: <KEY=VALUE file> <key>.
  grep -E "^${2}=" "$1" | head -n1 | cut -d= -f2- | tr -d '\r' || true
}

derive_temp_names() {
  local short="${GIT_SHA:0:12}"
  MISSION_TOPIC="${VERIFY_TOPIC_PREFIX}-${short}"
  MISSION_SUB="${MISSION_TOPIC}${VERIFY_SUBSCRIPTION_SUFFIX}"
  IMPACT_VERIFY_TOPIC="${VERIFY_IMPACT_TOPIC_PREFIX}-${short}"
  IMPACT_VERIFY_SUB="${IMPACT_VERIFY_TOPIC}${VERIFY_SUBSCRIPTION_SUFFIX}"
  local n
  for n in "$MISSION_TOPIC" "$MISSION_SUB" "$IMPACT_VERIFY_TOPIC" "$IMPACT_VERIFY_SUB"; do
    refuse_staging_named_resource "$n"
  done
}

assert_state_matches_derived_names() {
  # The state file is never trusted to name what gets DELETED: every name in it
  # must equal the one derived from this checkout's SHA.
  local field expected
  for field in "git_sha:${GIT_SHA}" "mission_topic:${MISSION_TOPIC}" "mission_subscription:${MISSION_SUB}" \
               "impact_topic:${IMPACT_VERIFY_TOPIC}" "impact_subscription:${IMPACT_VERIFY_SUB}"; do
    expected="${field#*:}"
    if [ "$(state_get "$PENDING_STATE_FILE" "${field%%:*}")" != "$expected" ]; then
      echo "Error: pending state ${PENDING_STATE_FILE} field '${field%%:*}' does not match the value derived from" >&2
      echo "the current commit (${expected}). Refusing to act on it -- check out the SHA it was prepared at." >&2
      return 1
    fi
  done
}

snapshot_prod_subscriptions() {
  # Byte-for-byte copies of the LIVE production subscription configs.
  local sub
  mkdir -p "$PROD_SUBS_DIR"
  for sub in "$PROD_PUBSUB_SUBSCRIPTION" "$PROD_IMPACT_SUBSCRIPTION"; do
    gcloud pubsub subscriptions describe "$sub" --format=json > "${PROD_SUBS_DIR}/${sub}.json"
    if [ ! -s "${PROD_SUBS_DIR}/${sub}.json" ]; then
      echo "Error: could not record the production subscription ${sub}." >&2
      return 1
    fi
  done
}

confirm_prod_subscriptions_unchanged() {
  local sub rc=0
  for sub in "$PROD_PUBSUB_SUBSCRIPTION" "$PROD_IMPACT_SUBSCRIPTION"; do
    if [ ! -f "${PROD_SUBS_DIR}/${sub}.json" ]; then
      echo "Error: no recorded copy of ${sub} in ${PROD_SUBS_DIR}; cannot confirm it is unchanged." >&2
      rc=1
    elif cmp -s "${PROD_SUBS_DIR}/${sub}.json" <(gcloud pubsub subscriptions describe "$sub" --format=json); then
      echo "   Confirmed: production subscription ${sub} is byte-for-byte unchanged."
    else
      echo "Error: production subscription ${sub} CHANGED during verification. Treat production Pub/Sub" >&2
      echo "routing as SUSPECT and investigate before promoting anything." >&2
      rc=1
    fi
  done
  return "$rc"
}

restore_service_env() {
  # Args: <service> <ENV_KEY:state-field>... Restores only values that differ.
  local svc="$1"; shift
  local spec key field orig cur
  local pairs=()
  for spec in "$@"; do
    key="${spec%%:*}"
    field="${spec#*:}"
    orig="$(state_get "$PENDING_STATE_FILE" "$field")" || orig=""
    if [ -z "$orig" ]; then
      echo "Error: pending state has no original value for ${svc} ${key}." >&2
      return 1
    fi
    cur="$(service_env_value "$svc" "$key")" || { echo "Error: cannot read ${svc} ${key}." >&2; return 1; }
    if [ "$cur" != "$orig" ]; then pairs+=("${key}=${orig}"); fi
  done
  if [ "${#pairs[@]}" -eq 0 ]; then
    echo "   ${svc}: environment already at its original values."
    return 0
  fi
  echo "   ${svc}: restoring ${pairs[*]}"
  set_candidate_env "$svc" "${pairs[@]}"
}

delete_temp_resource() {
  # Args: <topics|subscriptions> <name>. Only ever called with derived, SHA-scoped names.
  local kind="$1" name="$2"
  if gcloud pubsub "$kind" describe "$name" >/dev/null 2>&1; then
    if gcloud pubsub "$kind" delete "$name" >/dev/null; then
      echo "   deleted ${kind%s} ${name}"
    else
      echo "Error: failed to delete ${kind%s} ${name}. MANUAL CLEANUP REQUIRED: gcloud pubsub ${kind} delete ${name}" >&2
      return 1
    fi
  else
    echo "   ${kind%s} ${name}: already absent."
  fi
}

teardown_verification_environment() {
  # Shared by --abort-verification, --complete-verification and prepare
  # failure/signal handling. Attempts EVERY step; returns nonzero if any failed.
  # Never touches Firestore/GCS evidence, only the candidate env + temp topics/subs.
  local rc=0
  echo "Restoring the candidate API/worker environment to its recorded original values..."
  restore_service_env rateguard-api "RATEGUARD_PUBSUB_TOPIC:original_api_pubsub_topic" \
    "RATEGUARD_IMPACT_TOPIC:original_api_impact_topic" || rc=1
  restore_service_env rateguard-worker "RATEGUARD_IMPACT_TOPIC:original_worker_impact_topic" || rc=1
  echo "Deleting the SHA-scoped temporary subscriptions and topics (acceptance evidence is never touched)..."
  delete_temp_resource subscriptions "$MISSION_SUB" || rc=1
  delete_temp_resource subscriptions "$IMPACT_VERIFY_SUB" || rc=1
  delete_temp_resource topics "$MISSION_TOPIC" || rc=1
  delete_temp_resource topics "$IMPACT_VERIFY_TOPIC" || rc=1
  echo "Confirming the production subscriptions are byte-for-byte unchanged..."
  confirm_prod_subscriptions_unchanged || rc=1
  return "$rc"
}

clear_pending_state() {
  rm -f "$PENDING_STATE_FILE" "${PENDING_STATE_FILE}.tmp"
  rm -rf "$PROD_SUBS_DIR"
}

on_prepare_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  if [ "$PREPARE_ARMED" = true ] && [ "$CLEANUP_DONE" = false ]; then
    CLEANUP_DONE=true
    PREPARE_ARMED=false
    [ "$rc" -ne 0 ] || rc=1
    echo "" >&2
    echo "PREPARATION FAILED or was interrupted (exit ${rc}) -- restoring the candidate environment and" >&2
    echo "deleting the temporary verification resources..." >&2
    if teardown_verification_environment >&2; then
      clear_pending_state
      echo "Candidate environment restored; temporary resources removed; nothing is pending." >&2
    else
      echo "Error: automatic cleanup was INCOMPLETE. Pending state RETAINED at ${PENDING_STATE_FILE}." >&2
      echo "Re-run: $0 --abort-verification   (safe to repeat)" >&2
    fi
  fi
  exit "$rc"
}

install_prepare_traps() {
  # Trap-state variables (PREPARE_ARMED, CLEANUP_DONE) are initialised at the
  # top of the script, BEFORE this runs -- an EXIT trap firing after a function
  # has returned must never see an unset (or function-local) variable under
  # `set -u`. That was the original (removed) --verify-candidate defect.
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  trap on_prepare_exit EXIT
}

require_candidate_revision() {
  # Args: <service>. Prints the candidate-tagged revision after confirming it
  # exists, serves 0% and is not the production revision.
  local svc="$1" rev prod pct
  rev="$(tagged_revision_name "$svc")"
  prod="$(prod_revision_name "$svc")"
  pct="$(candidate_traffic_percent "$svc")"
  if [ -z "$rev" ]; then
    echo "Error: no candidate-tagged revision for ${svc}. Run '$0 --deploy-candidate' first." >&2
    return 1
  fi
  if [ "${pct:-0}" != "0" ]; then
    echo "Error: candidate revision ${rev} of ${svc} is serving ${pct}% of traffic (must be 0%)." >&2
    return 1
  fi
  if [ -z "$prod" ] || [ "$rev" = "$prod" ]; then
    echo "Error: candidate revision of ${svc} (${rev}) is not distinct from production (${prod:-<none>})." >&2
    return 1
  fi
  printf '%s' "$rev"
}

prepare_verification() {
  preflight_guards
  gcloud config set project "$PROJECT_ID" >/dev/null
  derive_temp_names

  if [ -f "$PENDING_STATE_FILE" ]; then
    echo "Error: a verification is already pending for ${GIT_SHA} (${PENDING_STATE_FILE})." >&2
    echo "  Finish it with '$0 --complete-verification --mission-id=<ID>' or discard it with '$0 --abort-verification'." >&2
    exit 1
  fi
  local n
  for n in "$MISSION_SUB" "$IMPACT_VERIFY_SUB"; do
    if gcloud pubsub subscriptions describe "$n" >/dev/null 2>&1; then
      echo "Error: ${n} already exists but no pending state records it (orphan from an unrecorded run)." >&2
      echo "  Inspect, then delete manually: gcloud pubsub subscriptions delete ${n}" >&2
      exit 1
    fi
  done
  for n in "$MISSION_TOPIC" "$IMPACT_VERIFY_TOPIC"; do
    if gcloud pubsub topics describe "$n" >/dev/null 2>&1; then
      echo "Error: ${n} already exists but no pending state records it (orphan from an unrecorded run)." >&2
      echo "  Inspect, then delete manually: gcloud pubsub topics delete ${n}" >&2
      exit 1
    fi
  done

  echo "1. Confirming the four candidate revisions exist at 0% traffic and differ from production..."
  local rating_rev worker_rev api_rev web_rev
  rating_rev="$(require_candidate_revision rateguard-rating-engine)" || exit 1
  worker_rev="$(require_candidate_revision rateguard-worker)" || exit 1
  api_rev="$(require_candidate_revision rateguard-api)" || exit 1
  web_rev="$(require_candidate_revision rateguard-web)" || exit 1

  local prod_rating_rev prod_worker_rev prod_api_rev prod_web_rev
  prod_rating_rev="$(prod_revision_name rateguard-rating-engine)"
  prod_worker_rev="$(prod_revision_name rateguard-worker)"
  prod_api_rev="$(prod_revision_name rateguard-api)"
  prod_web_rev="$(prod_revision_name rateguard-web)"

  local worker_tagged_url worker_untagged_url api_tagged_url web_tagged_url
  worker_tagged_url="$(get_tagged_url rateguard-worker)"
  worker_untagged_url="$(get_untagged_url rateguard-worker)"
  api_tagged_url="$(get_tagged_url rateguard-api)"
  web_tagged_url="$(get_tagged_url rateguard-web)"
  if [ -z "$worker_tagged_url" ] || [ -z "$worker_untagged_url" ] || [ -z "$api_tagged_url" ] || [ -z "$web_tagged_url" ]; then
    echo "Error: could not discover the candidate worker/api/web URLs. Run '$0 --deploy-candidate' first." >&2
    exit 1
  fi

  local rating_image worker_image api_image web_image backend_artifact_digest
  rating_image="$(resolve_revision_digest rateguard-rating-engine "$rating_rev")"
  worker_image="$(resolve_revision_digest rateguard-worker "$worker_rev")"
  api_image="$(resolve_revision_digest rateguard-api "$api_rev")"
  web_image="$(resolve_revision_digest rateguard-web "$web_rev")"
  backend_artifact_digest="$(resolve_image_digest "$BACKEND_IMAGE")"
  if [ -z "$rating_image" ] || [ -z "$worker_image" ] || [ -z "$api_image" ] || [ -z "$web_image" ] || [ -z "$backend_artifact_digest" ]; then
    echo "Error: could not resolve all four candidate image digests. Refusing to continue." >&2
    exit 1
  fi
  assert_api_worker_same_digest "$api_rev" "$worker_rev"

  local orig_api_topic orig_api_impact orig_worker_impact
  orig_api_topic="$(service_env_value rateguard-api RATEGUARD_PUBSUB_TOPIC)" || exit 1
  orig_api_impact="$(service_env_value rateguard-api RATEGUARD_IMPACT_TOPIC)" || exit 1
  orig_worker_impact="$(service_env_value rateguard-worker RATEGUARD_IMPACT_TOPIC)" || exit 1

  echo "2. Recording the production revisions and the LIVE production subscription configurations"
  echo "   (${PROD_PUBSUB_SUBSCRIPTION}, ${PROD_IMPACT_SUBSCRIPTION}) byte for byte..."
  snapshot_prod_subscriptions || exit 1

  # A new preparation invalidates any earlier verification of this SHA: the
  # candidate environment is about to change, so old evidence no longer proves it.
  rm -f "${VERIFIED_MARKER_DIR}/${GIT_SHA}" "${VERIFIED_MARKER_DIR}/${GIT_SHA}.evidence" "$ABORTED_MARKER"

  # Written BEFORE the first mutation; every restore step reads only this file.
  state_set "$PENDING_STATE_FILE" \
    "phase=preparing" "git_sha=${GIT_SHA}" "project_id=${PROJECT_ID}" "region=${REGION}" \
    "prepared_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "firestore_collection=${PROD_FIRESTORE_COLLECTION}" "gcs_bucket=${PROD_GCS_BUCKET}" \
    "mission_topic=${MISSION_TOPIC}" "mission_subscription=${MISSION_SUB}" \
    "impact_topic=${IMPACT_VERIFY_TOPIC}" "impact_subscription=${IMPACT_VERIFY_SUB}" \
    "worker_tagged_url=${worker_tagged_url}" "worker_stable_url=${worker_untagged_url}" \
    "api_tagged_url=${api_tagged_url}" "web_tagged_url=${web_tagged_url}" \
    "original_api_pubsub_topic=${orig_api_topic}" "original_api_impact_topic=${orig_api_impact}" \
    "original_worker_impact_topic=${orig_worker_impact}" \
    "initial_candidate_worker_revision=${worker_rev}" "initial_candidate_api_revision=${api_rev}" \
    "candidate_rating_engine_revision=${rating_rev}" "candidate_web_revision=${web_rev}" \
    "production_worker_revision=${prod_worker_rev}" "production_api_revision=${prod_api_rev}" \
    "production_rating_engine_revision=${prod_rating_rev}" "production_web_revision=${prod_web_rev}" \
    "rating_engine_image=${rating_image}" "worker_image=${worker_image}" "api_image=${api_image}" "web_image=${web_image}" \
    "candidate_worker_image_digest=${backend_artifact_digest}" \
    "production_subscriptions_dir=${PROD_SUBS_DIR}"

  PREPARE_ARMED=true
  install_prepare_traps

  echo "3. Creating the isolated mission topic ${MISSION_TOPIC} and impact topic ${IMPACT_VERIFY_TOPIC}"
  echo "   (NEVER ${PROD_PUBSUB_TOPIC} / ${PROD_IMPACT_TOPIC}, and no subscription is added to either)..."
  gcloud pubsub topics create "$MISSION_TOPIC" >/dev/null
  gcloud pubsub topics create "$IMPACT_VERIFY_TOPIC" >/dev/null
  gcloud pubsub topics add-iam-policy-binding "$MISSION_TOPIC" \
    --member="serviceAccount:${API_SA}" --role="roles/pubsub.publisher" >/dev/null
  gcloud pubsub topics add-iam-policy-binding "$IMPACT_VERIFY_TOPIC" \
    --member="serviceAccount:${WORKER_SA}" --role="roles/pubsub.publisher" >/dev/null

  echo "4. Creating the push subscriptions -- each routed ONLY to the candidate worker's own route."
  echo "   Push endpoint = candidate-tagged worker URL; OIDC audience = the STABLE, untagged worker URL"
  echo "   (the proven form; see infrastructure/setup_impact_pubsub.sh -- never a --tag URL)."
  gcloud pubsub subscriptions create "$MISSION_SUB" \
    --topic="$MISSION_TOPIC" \
    --ack-deadline=600 --expiration-period=2d \
    --push-endpoint="${worker_tagged_url}/internal/pubsub/assurance" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$worker_untagged_url" >/dev/null
  # No dead-letter topic: the production DLQ must never receive candidate messages.
  gcloud pubsub subscriptions create "$IMPACT_VERIFY_SUB" \
    --topic="$IMPACT_VERIFY_TOPIC" \
    --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s --expiration-period=2d \
    --push-endpoint="${worker_tagged_url}/internal/pubsub/impact-batch" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$worker_untagged_url" >/dev/null

  echo "5. Pointing the candidate publishers ONLY at the temporary topics (still --no-traffic)..."
  set_candidate_env rateguard-worker "RATEGUARD_IMPACT_TOPIC=${IMPACT_VERIFY_TOPIC}"
  set_candidate_env rateguard-api "RATEGUARD_PUBSUB_TOPIC=${MISSION_TOPIC}" "RATEGUARD_IMPACT_TOPIC=${IMPACT_VERIFY_TOPIC}"

  if [ "$(service_env_value rateguard-worker RATEGUARD_IMPACT_TOPIC)" != "$IMPACT_VERIFY_TOPIC" ] \
    || [ "$(service_env_value rateguard-api RATEGUARD_PUBSUB_TOPIC)" != "$MISSION_TOPIC" ] \
    || [ "$(service_env_value rateguard-api RATEGUARD_IMPACT_TOPIC)" != "$IMPACT_VERIFY_TOPIC" ]; then
    echo "Error: candidate environment does not carry the temporary topics after the update." >&2
    exit 1
  fi

  echo "6. Recording the FINAL candidate revisions (after every temporary environment change)..."
  local final_worker_rev final_api_rev
  final_worker_rev="$(require_candidate_revision rateguard-worker)" || exit 1
  final_api_rev="$(require_candidate_revision rateguard-api)" || exit 1
  assert_api_worker_same_digest "$final_api_rev" "$final_worker_rev"
  local svc want
  for svc in "rateguard-worker:${prod_worker_rev}" "rateguard-api:${prod_api_rev}" \
             "rateguard-rating-engine:${prod_rating_rev}" "rateguard-web:${prod_web_rev}"; do
    want="${svc#*:}"
    if [ "$(prod_revision_name "${svc%%:*}")" != "$want" ]; then
      echo "Error: production revision of ${svc%%:*} changed during preparation." >&2
      exit 1
    fi
  done

  echo "   Confirming the production subscriptions are still byte-for-byte unchanged..."
  confirm_prod_subscriptions_unchanged || exit 1

  state_set "$PENDING_STATE_FILE" \
    "candidate_worker_revision=${final_worker_rev}" "candidate_api_revision=${final_api_rev}" "phase=ready"

  # Success: the environment is deliberately LEFT IN PLACE for the observed manual run.
  PREPARE_ARMED=false
  trap - EXIT INT TERM HUP

  local short="${GIT_SHA:0:12}"
  cat <<READY
========================================================
CANDIDATE VERIFICATION ENVIRONMENT READY (0% production traffic)
Full SHA:                  ${GIT_SHA}
Candidate worker revision: ${final_worker_rev}   (production: ${prod_worker_rev})
Isolated mission topic:    ${MISSION_TOPIC} -> ${worker_tagged_url}/internal/pubsub/assurance
Isolated impact topic:     ${IMPACT_VERIFY_TOPIC} -> ${worker_tagged_url}/internal/pubsub/impact-batch
Pending state:             ${PENDING_STATE_FILE}

Candidate web URL:         ${web_tagged_url}

RUN ONE OBSERVED, SYNTHETIC MISSION (controlled workbook vs versioned REST connector):
  1. Open the candidate web URL above and sign in with your usual demo-tenant account
     in the browser. (Do not paste passwords or tokens into a terminal or this script.)
  2. On the SOURCES page, upload and compile a controlled workbook as Source A, choose the
     versioned REST connector with an explicit engine version as Source B, and paste this
     into the optional "Mission name" field (surrounding spaces are trimmed):
        [CANDIDATE-VERIFY-${short}] controlled workbook vs versioned REST connector
     Then press "Execute Assurance". (The New Mission wizard offers bundled samples only.)
  3. Wait until the mission reaches a terminal decision (QUEUED/RUNNING/202 is NOT success).
  4. Note the mission ID (MIS-XXXXXXXX), then run:
        $0 --complete-verification --mission-id=<MISSION_ID>
     or, to discard this environment:
        $0 --abort-verification

Complete/abort need operator Application Default Credentials
(gcloud auth application-default login) and the backend Python dependencies.
The temporary subscriptions self-expire after 2 days if you forget them.
========================================================
READY
}

run_helper() {
  # Args: <check|duplicate> <mission id>. Operator ADC only; no token is read.
  PYTHONPATH="backend${PYTHONPATH:+:${PYTHONPATH}}" "$HELPER_PYTHON" "$HELPER_SCRIPT" "$1" \
    --state-file "$PENDING_STATE_FILE" --mission-id "$2"
}

complete_verification() {
  preflight_guards
  gcloud config set project "$PROJECT_ID" >/dev/null
  derive_temp_names

  if [ -z "$MISSION_ID" ]; then
    echo "Error: --complete-verification requires --mission-id=<ID>." >&2
    exit 2
  fi
  if [ ! -f "$PENDING_STATE_FILE" ]; then
    echo "Error: no pending verification for ${GIT_SHA} (${PENDING_STATE_FILE}). Run '$0 --prepare-verification' first." >&2
    exit 1
  fi
  assert_state_matches_derived_names || exit 1
  if [ "$(state_get "$PENDING_STATE_FILE" phase)" != "ready" ]; then
    echo "Error: the pending state is not 'ready' (phase=$(state_get "$PENDING_STATE_FILE" phase)); run '$0 --abort-verification'." >&2
    exit 1
  fi

  echo "1. Confirming the deployed revisions are exactly the ones recorded at preparation..."
  local svc spec
  for spec in "rateguard-worker:candidate_worker_revision:production_worker_revision" \
              "rateguard-api:candidate_api_revision:production_api_revision" \
              "rateguard-rating-engine:candidate_rating_engine_revision:production_rating_engine_revision" \
              "rateguard-web:candidate_web_revision:production_web_revision"; do
    svc="${spec%%:*}"
    if [ "$(tagged_revision_name "$svc")" != "$(state_get "$PENDING_STATE_FILE" "$(echo "$spec" | cut -d: -f2)")" ]; then
      echo "Error: the candidate revision of ${svc} changed since preparation. Run '$0 --abort-verification' and start over." >&2
      exit 1
    fi
    if [ "$(prod_revision_name "$svc")" != "$(state_get "$PENDING_STATE_FILE" "$(echo "$spec" | cut -d: -f3)")" ]; then
      echo "Error: the PRODUCTION revision of ${svc} changed since preparation. Refusing." >&2
      exit 1
    fi
  done

  echo "2. Checking mission ${MISSION_ID}: terminal decision, evidence bundle, candidate revision/SHA, provenance..."
  if ! run_helper check "$MISSION_ID" >/dev/null; then
    echo "Error: mission checks failed. Verification is NOT complete; promotion is refused." >&2
    echo "  The isolated environment is still in place: fix and re-run, or '$0 --abort-verification'." >&2
    exit 1
  fi
  echo "3. Live duplicate-delivery test: same envelope published twice to ${MISSION_TOPIC}..."
  if ! run_helper duplicate "$MISSION_ID" >/dev/null; then
    echo "Error: duplicate-delivery checks failed. Verification is NOT complete; promotion is refused." >&2
    exit 1
  fi
  echo "4. Confirming the production subscriptions are unchanged..."
  confirm_prod_subscriptions_unchanged || exit 1

  echo "5. Restoring the candidate environment and deleting the temporary resources..."
  if ! teardown_verification_environment; then
    echo "Error: cleanup incomplete; the pending state is RETAINED and promotion is refused." >&2
    echo "  Re-run '$0 --abort-verification' (idempotent) to finish cleaning up." >&2
    exit 1
  fi

  echo "6. Pinning the four verified image digests (post-restore candidate revisions)..."
  local rating_rev worker_rev api_rev web_rev
  rating_rev="$(require_candidate_revision rateguard-rating-engine)" || exit 1
  worker_rev="$(require_candidate_revision rateguard-worker)" || exit 1
  api_rev="$(require_candidate_revision rateguard-api)" || exit 1
  web_rev="$(require_candidate_revision rateguard-web)" || exit 1
  local rating_image worker_image api_image web_image
  rating_image="$(resolve_revision_digest rateguard-rating-engine "$rating_rev")"
  worker_image="$(resolve_revision_digest rateguard-worker "$worker_rev")"
  api_image="$(resolve_revision_digest rateguard-api "$api_rev")"
  web_image="$(resolve_revision_digest rateguard-web "$web_rev")"
  if [ -z "$worker_image" ] || [ "$worker_image" != "$api_image" ] \
    || [ "$worker_image" != "$(state_get "$PENDING_STATE_FILE" worker_image)" ] \
    || [ "$rating_image" != "$(state_get "$PENDING_STATE_FILE" rating_engine_image)" ] \
    || [ "$web_image" != "$(state_get "$PENDING_STATE_FILE" web_image)" ]; then
    echo "Error: the restored candidate images do not match the digests recorded at preparation." >&2
    echo "  Refusing to pin; the pending state is RETAINED. Run '$0 --abort-verification'." >&2
    exit 1
  fi

  mkdir -p "$VERIFIED_MARKER_DIR"
  cat > "${VERIFIED_MARKER_DIR}/${GIT_SHA}.evidence" <<EVIDENCE
GIT_SHA=${GIT_SHA}
VERIFICATION_COMPLETE=true
VERIFIED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MISSION_ID=${MISSION_ID}
VERIFIED_WORKER_REVISION=$(state_get "$PENDING_STATE_FILE" candidate_worker_revision)
VERIFIED_API_REVISION=$(state_get "$PENDING_STATE_FILE" candidate_api_revision)
RATING_ENGINE_REVISION=${rating_rev}
RATING_ENGINE_DIGEST=${rating_image}
WORKER_REVISION=${worker_rev}
WORKER_DIGEST=${worker_image}
API_REVISION=${api_rev}
API_DIGEST=${api_image}
WEB_REVISION=${web_rev}
WEB_DIGEST=${web_image}
EVIDENCE
  clear_pending_state
  echo "========================================================"
  echo "VERIFICATION COMPLETE for ${GIT_SHA} (mission ${MISSION_ID})."
  echo "Digests pinned in ${VERIFIED_MARKER_DIR}/${GIT_SHA}.evidence; pending state cleared."
  echo "Next: $0 --record-verified"
  echo "========================================================"
}

abort_verification() {
  preflight_guards
  gcloud config set project "$PROJECT_ID" >/dev/null
  derive_temp_names
  if [ ! -f "$PENDING_STATE_FILE" ]; then
    echo "No pending verification for ${GIT_SHA} (${PENDING_STATE_FILE} absent) -- nothing to restore."
    return 0
  fi
  assert_state_matches_derived_names || exit 1
  if ! teardown_verification_environment; then
    echo "Error: abort incomplete. Pending state RETAINED at ${PENDING_STATE_FILE}; re-run '$0 --abort-verification'." >&2
    exit 1
  fi
  clear_pending_state
  mkdir -p "$VERIFIED_MARKER_DIR"
  rm -f "${VERIFIED_MARKER_DIR}/${GIT_SHA}" "${VERIFIED_MARKER_DIR}/${GIT_SHA}.evidence"
  printf 'aborted at %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ABORTED_MARKER"
  echo "Verification aborted: candidate environment restored, temporary resources deleted."
  echo "Any Firestore/GCS acceptance evidence was preserved. Promotion of ${GIT_SHA} stays refused"
  echo "until a new --prepare-verification / --complete-verification succeeds."
}

record_verified() {
  # Writes the marker promote_candidate_to_production.sh requires -- ONLY for a
  # verification that --complete-verification finished (its evidence file says
  # so) and that has no pending or aborted state.
  local evidence="${VERIFIED_MARKER_DIR}/${GIT_SHA}.evidence"
  if [ -f "$PENDING_STATE_FILE" ]; then
    echo "Error: a verification is still pending for ${GIT_SHA}. Complete or abort it first." >&2
    exit 1
  fi
  if [ -f "$ABORTED_MARKER" ]; then
    echo "Error: verification of ${GIT_SHA} was aborted. Refusing to record it as verified." >&2
    exit 1
  fi
  if [ ! -f "$evidence" ] || [ "$(kv_get "$evidence" VERIFICATION_COMPLETE)" != "true" ] \
    || [ "$(kv_get "$evidence" GIT_SHA)" != "$GIT_SHA" ] || [ -z "$(kv_get "$evidence" MISSION_ID)" ]; then
    echo "Error: no completed verification for ${GIT_SHA}. Run '$0 --prepare-verification' then" >&2
    echo "'$0 --complete-verification --mission-id=<ID>' first." >&2
    exit 1
  fi
  local svc key rev
  for svc in "rateguard-rating-engine:RATING_ENGINE" "rateguard-worker:WORKER" "rateguard-api:API" "rateguard-web:WEB"; do
    key="${svc#*:}"
    rev="$(tagged_revision_name "${svc%%:*}")"
    if [ -z "$rev" ] || [ "$rev" != "$(kv_get "$evidence" "${key}_REVISION")" ] \
      || [ "$(resolve_revision_digest "${svc%%:*}" "$rev")" != "$(kv_get "$evidence" "${key}_DIGEST")" ]; then
      echo "Error: the candidate ${svc%%:*} deployment has moved since verification completed. Refusing." >&2
      exit 1
    fi
  done
  mkdir -p "$VERIFIED_MARKER_DIR"
  printf 'verified at %s by %s (mission %s)\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(whoami 2>/dev/null || echo unknown)" "$(kv_get "$evidence" MISSION_ID)" \
    > "${VERIFIED_MARKER_DIR}/${GIT_SHA}"
  echo "Verified marker written: ${VERIFIED_MARKER_DIR}/${GIT_SHA}"
  echo "Pinned digests: ${evidence}"
  echo "promote_candidate_to_production.sh will now accept this SHA without --skip-verification-check,"
  echo "and will refuse to promote if the candidate tag has since moved off these exact digests."
}

case "$MODE" in
  deploy) deploy_candidate ;;
  prepare) prepare_verification ;;
  complete) complete_verification ;;
  abort) abort_verification ;;
  record-verified) preflight_guards; record_verified ;;
  *) print_plan ;;
esac
