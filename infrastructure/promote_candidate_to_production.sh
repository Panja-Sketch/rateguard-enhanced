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

# Resource names that must never appear in what this script promotes -- a
# candidate wired to one of these (a leftover from the earlier, wrong
# staging-isolation design in deploy_candidate_enhanced.sh) is refused rather
# than promoted, see reject_staging_named_resources below.
OBSOLETE_STAGING_NAMES="assurance-runs-staging assurance-worker-staging assurance-runs-staging-dlq assurance-runs-staging-dlq-inspect assurance_runs_staging rateguard_staging rateguard-enhanced-artifacts-staging"

# deploy_candidate_enhanced.sh's `--verify-candidate` mode is expected to
# write this marker (containing the full git SHA it verified) on a
# successful, fully-cleaned-up async-worker verification run. Promotion
# refuses to proceed without it unless --skip-verification-check is passed
# explicitly (a documented, deliberate escape hatch for a human operator who
# verified out-of-band -- never the default).
VERIFIED_MARKER_DIR="infrastructure/.candidate-verified"

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

# The revision currently serving 100% of production traffic for a service --
# captured BEFORE any promotion action, so a rollback target always exists.
current_prod_revision() {
  gcloud run services describe "$1" --region "$REGION" --project "$PROJECT_ID" \
    --format="value(status.traffic)" 2>/dev/null \
    | tr ';' '\n' | grep "'percent': 100" \
    | sed -E "s/.*'revisionName': '([^']+)'.*/\1/"
}

reject_staging_named_resources() {
  # Args: <label> <value...>. Refuses to promote if anything this script is
  # about to reuse/reference matches an obsolete staging-scoped name.
  local label="$1"; shift
  local value stale
  for value in "$@"; do
    for stale in $OBSOLETE_STAGING_NAMES; do
      if [ "$value" = "$stale" ]; then
        echo "Error: ${label} resolved to obsolete staging-named resource '${value}'. Refusing to promote." >&2
        exit 1
      fi
    done
  done
}

require_verified_marker() {
  local sha="$1"
  if [ -f "${VERIFIED_MARKER_DIR}/${sha}" ]; then
    echo "Verified marker found for ${sha}: $(cat "${VERIFIED_MARKER_DIR}/${sha}")"
    return 0
  fi
  if [ "$SKIP_VERIFICATION_CHECK" = true ]; then
    echo "WARNING: --skip-verification-check passed -- promoting ${sha} WITHOUT a recorded" >&2
    echo "'infrastructure/deploy_candidate_enhanced.sh --verify-candidate' pass. This is a" >&2
    echo "deliberate operator override, not the default path." >&2
    return 0
  fi
  echo "Error: no verified marker for ${sha} at ${VERIFIED_MARKER_DIR}/${sha}." >&2
  echo "  Run: infrastructure/deploy_candidate_enhanced.sh --verify-candidate" >&2
  echo "  first, or pass --skip-verification-check to override deliberately." >&2
  exit 1
}

RATING_ENGINE_REV="$(candidate_revision rateguard-rating-engine)"
WORKER_REV="$(candidate_revision rateguard-worker)"
API_REV="$(candidate_revision rateguard-api)"
WEB_REV="$(candidate_revision rateguard-web)"

PRIOR_API_REV="$(current_prod_revision rateguard-api)"
PRIOR_WORKER_REV="$(current_prod_revision rateguard-worker)"
PRIOR_RATING_ENGINE_REV="$(current_prod_revision rateguard-rating-engine)"
PRIOR_WEB_REV="$(current_prod_revision rateguard-web)"

GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo 'UNKNOWN_SHA')"
WEB_IMAGE_TAG="prod-${GIT_SHA}"

PROMOTE=false
CANARY_PERCENT=""
SKIP_VERIFICATION_CHECK=false
for arg in "$@"; do
  case "$arg" in
    --promote) PROMOTE=true ;;
    --canary-percent=*) CANARY_PERCENT="${arg#*=}" ;;
    --skip-verification-check) SKIP_VERIFICATION_CHECK=true ;;
    --help|-h)
      echo "Usage: $0 [--promote] [--canary-percent=N] [--skip-verification-check]"
      echo "  (no flag)                 Print the promotion plan. No gcloud/network calls."
      echo "  --promote                 Actually apply it (100% traffic unless --canary-percent is given)."
      echo "  --canary-percent=N        Shift only N% of api/worker/web traffic to the new"
      echo "                            revisions and STOP -- re-run with --promote (no"
      echo "                            --canary-percent) to complete the shift to 100% once"
      echo "                            the canary has been verified."
      echo "  --skip-verification-check Deliberately bypass the --verify-candidate marker"
      echo "                            requirement (not the default; use only when a human"
      echo "                            operator has verified the candidate out-of-band)."
      exit 0
      ;;
  esac
done

print_plan() {
  cat <<PLAN
========================================================
   RateGuard Enhanced -- Promote Candidate to Production PLAN
========================================================
Currently candidate-tagged revisions (what would be promoted -- exact
already-built, already-tested image digests; NOTHING here is rebuilt except
web, which must be rebuilt to bake in the production API URL):
  rating-engine:  ${RATING_ENGINE_REV:-<none found>}
  worker:         ${WORKER_REV:-<none found>}
  api:            ${API_REV:-<none found>}
  web:            ${WEB_REV:-<none found>} (rebuilt with prod API URL, not reused as-is)

Currently-live production revisions (captured now, BEFORE any promotion
action -- these are the rollback targets if the promotion needs to be
reversed; see infrastructure/rollback.sh):
  rating-engine:  ${PRIOR_RATING_ENGINE_REV:-<none found>}
  worker:         ${PRIOR_WORKER_REV:-<none found>}
  api:            ${PRIOR_API_REV:-<none found>}
  web:            ${PRIOR_WEB_REV:-<none found>}

Verification requirement: this script refuses to promote a candidate SHA
that has no marker at ${VERIFIED_MARKER_DIR}/<full-sha> written by
'deploy_candidate_enhanced.sh --verify-candidate', unless
--skip-verification-check is passed explicitly (a deliberate operator
override, not the default).

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
  6) Verify Pub/Sub routing after promotion: describe ${PROD_SUBSCRIPTION}
     and confirm its pushEndpoint/oidcToken.audience still point at the
     STABLE (non-tag) worker URL, never a --tag URL -- refuses to declare
     success otherwise.
  7) Print a verification checklist (does NOT execute the checks): create one
     mission through ${PROD_API_URL}, then read the resulting document
     directly from Firestore (${PROD_FIRESTORE_COLLECTION}) and the uploaded
     artifact directly from gs://${PROD_GCS_BUCKET} -- never trust the app's
     own API response alone for this confirmation.
  8) Print the exact infrastructure/rollback.sh invocation using the prior
     revisions captured above, ready to run immediately if anything looks wrong.

With --canary-percent=N instead of a bare --promote, steps 1-3 shift only N%
of rating-engine/worker/api traffic (web is not built/shifted in canary mode)
and the script stops -- re-run with a bare --promote to complete the shift to
100% once the canary has been verified.
PLAN
}

if [ "$PROMOTE" = false ]; then
  print_plan
  exit 0
fi

configured_project="$(gcloud config get-value project 2>/dev/null || true)"
if [ "$configured_project" != "$PROJECT_ID" ]; then
  echo "Error: gcloud is configured for project '${configured_project:-<none>}', not '${PROJECT_ID}'. Refusing to continue." >&2
  exit 1
fi

if [ -z "$RATING_ENGINE_REV" ] || [ -z "$WORKER_REV" ] || [ -z "$API_REV" ]; then
  echo "Error: could not find a candidate-tagged revision for one or more of" >&2
  echo "rating-engine/worker/api. Run deploy_candidate_enhanced.sh --deploy-candidate" >&2
  echo "first, or check --tag candidate manually with 'gcloud run services describe'." >&2
  exit 1
fi

reject_staging_named_resources "candidate revision names" \
  "$RATING_ENGINE_REV" "$WORKER_REV" "$API_REV" "${WEB_REV:-}"

require_verified_marker "$GIT_SHA"

echo "Prior production revisions captured for rollback (infrastructure/rollback.sh):"
echo "  api=${PRIOR_API_REV:-<none>} worker=${PRIOR_WORKER_REV:-<none>} web=${PRIOR_WEB_REV:-<none>} rating-engine=${PRIOR_RATING_ENGINE_REV:-<none>}"

if [ -n "$CANARY_PERCENT" ]; then
  echo "1. Canary: shifting ${CANARY_PERCENT}% of rating-engine traffic to ${RATING_ENGINE_REV}..."
  gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${RATING_ENGINE_REV}=${CANARY_PERCENT}"
else
  echo "1. Promoting rating-engine (${RATING_ENGINE_REV}) to 100%..."
  gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${RATING_ENGINE_REV}=100"
fi

echo "2. Repointing worker (${WORKER_REV}) at production data-plane resources..."
gcloud run services update rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION}"
WORKER_NEW_REV="$(gcloud run services describe rateguard-worker --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
if [ -n "$CANARY_PERCENT" ]; then
  echo "   Canary: shifting ${CANARY_PERCENT}% of traffic to ${WORKER_NEW_REV}..."
  gcloud run services update-traffic rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${WORKER_NEW_REV}=${CANARY_PERCENT}"
else
  echo "   Promoting traffic to ${WORKER_NEW_REV} (100%)..."
  gcloud run services update-traffic rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${WORKER_NEW_REV}=100"
fi

echo "3. Repointing api (${API_REV}) at production data-plane resources + CORS..."
gcloud run services update rateguard-api --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION}" \
  --update-env-vars "^@^RATEGUARD_CORS_ORIGINS=[\"${PROD_WEB_URL}\"]"
API_NEW_REV="$(gcloud run services describe rateguard-api --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
if [ -n "$CANARY_PERCENT" ]; then
  echo "   Canary: shifting ${CANARY_PERCENT}% of traffic to ${API_NEW_REV}..."
  gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${API_NEW_REV}=${CANARY_PERCENT}"
  echo ""
  echo "Canary at ${CANARY_PERCENT}% for rating-engine/worker/api. Web was NOT built/shifted in"
  echo "canary mode. Verify the canary, then re-run with a bare --promote to complete to 100%"
  echo "(including the web build/deploy/CORS steps)."
  exit 0
else
  echo "   Promoting traffic to ${API_NEW_REV} (100%)..."
  gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${API_NEW_REV}=100"
fi

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

echo "6. Verifying Pub/Sub routing after promotion..."
PUSH_ENDPOINT_AFTER="$(gcloud pubsub subscriptions describe "$PROD_SUBSCRIPTION" --project "$PROJECT_ID" --format="value(pushConfig.pushEndpoint)")"
PUSH_AUDIENCE_AFTER="$(gcloud pubsub subscriptions describe "$PROD_SUBSCRIPTION" --project "$PROJECT_ID" --format="value(pushConfig.oidcToken.audience)")"
if echo "$PUSH_ENDPOINT_AFTER" | grep -qE "candidate---|verify---"; then
  echo "Error: ${PROD_SUBSCRIPTION} push endpoint (${PUSH_ENDPOINT_AFTER}) is tag-scoped" >&2
  echo "(candidate--- or verify---) after promotion -- it must be the stable worker URL." >&2
  echo "Investigate before treating this promotion as safe." >&2
  exit 1
fi
echo "   Confirmed: ${PROD_SUBSCRIPTION} pushes to ${PUSH_ENDPOINT_AFTER} (audience ${PUSH_AUDIENCE_AFTER}), a stable non-tag URL."

cat <<DONE
========================================================
PROMOTION COMPLETE
========================================================
Immediate rollback, if needed (prior revisions captured before this run):
  infrastructure/rollback.sh --rollback \\
    --api-revision=${PRIOR_API_REV:-<unknown>} \\
    --worker-revision=${PRIOR_WORKER_REV:-<unknown>} \\
    --web-revision=${PRIOR_WEB_REV:-<unknown>} \\
    --rating-engine-revision=${PRIOR_RATING_ENGINE_REV:-<unknown>}

Now verify manually (this script does not do it for you):
  1) Create one mission through ${PROD_API_URL}/api/v1/missions.
  2) Read it back directly from Firestore:
     curl -H "Authorization: Bearer \$(gcloud auth print-access-token)" \\
       "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/${PROD_FIRESTORE_COLLECTION}/<mission_id>"
  3) Confirm any uploaded source artifact lands under:
     gcloud storage ls "gs://${PROD_GCS_BUCKET}/tenants/<tenant>/sources/<source_id>/"
  4) Do a real browser click-through of the canonical demo end-to-end.
DONE
