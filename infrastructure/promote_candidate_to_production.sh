#!/usr/bin/env bash
# RateGuard Enhanced -- Promote an already-deployed, already-tested candidate
# revision to production traffic (project: rateguard-enhanced).
#
# deploy_candidate_enhanced.sh wires every candidate to the SAME production
# Firestore/GCS/BigQuery resources production already uses (see that
# script's header), but the candidate's Pub/Sub topic, rating-engine
# connector URL, CORS origin, and (for web) API base URL are all
# candidate-scoped. A plain `gcloud run services update-traffic` promotion
# is NOT enough on its own -- shifting traffic alone would leave the
# now-100%-traffic revisions still pointed at the candidate's Pub/Sub
# topic/rating-engine URL/CORS origin, and (before the runtime-config fix
# below) rateguard-web's Next.js bundle would still call the candidate-tagged
# API URL forever, since that used to be a build-time constant. This script
# closes every one of those gaps.
#
# BLOCKER 4 fix (this script previously had two real bugs, found in a real
# dry-run, not merely style issues):
#   1) It never repointed RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL /
#      RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL away from the
#      candidate-tagged rating-engine URL deploy_candidate_enhanced.sh wires
#      in -- so a promoted worker/api would keep calling the CANDIDATE
#      rating-engine URL forever, a movable-tag reference in production.
#      Fixed: promotion now explicitly repoints both env vars at the STABLE
#      (untagged) production rating-engine URL.
#   2) It required a --verify-candidate marker but never checked that
#      marker's SHA actually matches the digests currently sitting under the
#      `candidate` tag -- so someone could re-deploy a DIFFERENT, unverified
#      candidate after verification and this script would still promote it.
#      Fixed: require_verified_marker now reads the structured
#      `<sha>.evidence` file deploy_candidate_enhanced.sh --record-verified
#      writes and refuses to promote unless every candidate-tagged revision
#      currently matches the exact digest recorded there.
#
# Prerequisite: a candidate has already been built and deployed via
# `deploy_candidate_enhanced.sh --deploy-candidate`, tested at its
# --tag candidate URLs, and verified via --prepare/--complete-verification +
# --record-verified. This script never builds/rebuilds ANY image (including
# web) -- it reuses whatever image is already running on the service's
# `candidate`-tagged revision for rating-engine/worker/api/web alike, so
# what gets promoted is byte-for-byte what was tested. The web image calls a
# different API URL in production only because RATEGUARD_API_URL (a plain
# runtime Cloud Run env var, never baked into the image) differs -- see
# frontend/src/lib/runtimeConfig.ts and BLOCKER 3.
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
PROD_RATING_ENGINE_URL="https://rateguard-rating-engine-nwhotixfva-uc.a.run.app"
RATING_ENGINE_SA="rateguard-rating-engine-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Resource names that must never appear in what this script promotes -- a
# candidate wired to one of these (a leftover from the earlier, wrong
# staging-isolation design in deploy_candidate_enhanced.sh) is refused rather
# than promoted, see reject_staging_named_resources below.
OBSOLETE_STAGING_NAMES="assurance-runs-staging assurance-worker-staging assurance-runs-staging-dlq assurance-runs-staging-dlq-inspect assurance_runs_staging rateguard_staging rateguard-enhanced-artifacts-staging"

# deploy_candidate_enhanced.sh's `--complete-verification` + `--record-verified` are expected to
# write this marker (containing the full git SHA it verified) on a
# successful, fully-cleaned-up async-worker verification run. Promotion
# refuses to proceed without it unless --skip-verification-check is passed
# explicitly (a documented, deliberate escape hatch for a human operator who
# verified out-of-band -- never the default).
VERIFIED_MARKER_DIR="infrastructure/.candidate-verified"

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

# The image digest actually running on a deployed revision -- used to pin
# promotion to the EXACT digests deploy_candidate_enhanced.sh
# --record-verified recorded, not merely whatever the movable `candidate`
# tag happens to point at right now.
resolve_revision_digest() {
  gcloud run revisions describe "$2" --region "$REGION" --project "$PROJECT_ID" \
    --format="value(spec.containers[0].image)" 2>/dev/null
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
  # Args: <sha> <current rating-engine/worker/api/web candidate revisions...>
  local sha="$1" cur_rating_engine_rev="$2" cur_worker_rev="$3" cur_api_rev="$4" cur_web_rev="$5"
  local evidence="${VERIFIED_MARKER_DIR}/${sha}.evidence"

  # A pending verification means the candidate API/worker still carry TEMPORARY
  # verification topics, and an aborted one was never proven. Neither can be
  # overridden by --skip-verification-check: promoting either would repoint
  # production at deleted/isolated topics or at an unverified candidate.
  if [ -f "${VERIFIED_MARKER_DIR}/${sha}.pending.json" ]; then
    echo "Error: a candidate verification is still PENDING for ${sha} (${VERIFIED_MARKER_DIR}/${sha}.pending.json)." >&2
    echo "  Run deploy_candidate_enhanced.sh --complete-verification --mission-id=<ID> or --abort-verification first." >&2
    exit 1
  fi
  if [ -f "${VERIFIED_MARKER_DIR}/${sha}.aborted" ]; then
    echo "Error: the candidate verification of ${sha} was ABORTED. Re-run --prepare-verification and" >&2
    echo "  --complete-verification before promoting." >&2
    exit 1
  fi

  if [ ! -f "${VERIFIED_MARKER_DIR}/${sha}" ]; then
    if [ "$SKIP_VERIFICATION_CHECK" = true ]; then
      echo "WARNING: --skip-verification-check passed -- promoting ${sha} WITHOUT a recorded" >&2
      echo "completed 'infrastructure/deploy_candidate_enhanced.sh' verification lifecycle pass. This is a" >&2
      echo "deliberate operator override, not the default path." >&2
      return 0
    fi
    echo "Error: no verified marker for ${sha} at ${VERIFIED_MARKER_DIR}/${sha}." >&2
    echo "  Run: infrastructure/deploy_candidate_enhanced.sh --prepare-verification, run one observed mission," >&2
    echo "  then --complete-verification --mission-id=<ID> and --record-verified, or pass" >&2
    echo "  --skip-verification-check to override deliberately." >&2
    exit 1
  fi
  echo "Verified marker found for ${sha}: $(cat "${VERIFIED_MARKER_DIR}/${sha}")"

  if [ ! -f "$evidence" ]; then
    if [ "$SKIP_VERIFICATION_CHECK" = true ]; then
      echo "WARNING: --skip-verification-check passed -- no ${evidence} digest-pin file found;" >&2
      echo "promoting whatever currently sits under the candidate tag, unpinned." >&2
      return 0
    fi
    echo "Error: verified marker exists for ${sha} but ${evidence} (the pinned-digest evidence" >&2
    echo "file) does not. Re-run 'deploy_candidate_enhanced.sh --record-verified', or pass" >&2
    echo "--skip-verification-check to override deliberately." >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$evidence"
  if [ "${VERIFICATION_COMPLETE:-}" != "true" ]; then
    if [ "$SKIP_VERIFICATION_CHECK" = true ]; then
      echo "WARNING: --skip-verification-check passed -- ${evidence} is not from a completed verification." >&2
      return 0
    fi
    echo "Error: ${evidence} is not from a completed --complete-verification. Refusing to promote." >&2
    exit 1
  fi
  local mismatch=""
  [ "${RATING_ENGINE_REVISION:-}" = "$cur_rating_engine_rev" ] || mismatch="${mismatch}rating-engine(verified=${RATING_ENGINE_REVISION:-<none>} current=${cur_rating_engine_rev:-<none>}) "
  [ "${WORKER_REVISION:-}" = "$cur_worker_rev" ] || mismatch="${mismatch}worker(verified=${WORKER_REVISION:-<none>} current=${cur_worker_rev:-<none>}) "
  [ "${API_REVISION:-}" = "$cur_api_rev" ] || mismatch="${mismatch}api(verified=${API_REVISION:-<none>} current=${cur_api_rev:-<none>}) "
  [ "${WEB_REVISION:-}" = "$cur_web_rev" ] || mismatch="${mismatch}web(verified=${WEB_REVISION:-<none>} current=${cur_web_rev:-<none>}) "
  if [ -n "$mismatch" ]; then
    if [ "$SKIP_VERIFICATION_CHECK" = true ]; then
      echo "WARNING: --skip-verification-check passed -- the candidate tag has moved since" >&2
      echo "verification (mismatched: ${mismatch}). Promoting the UNVERIFIED current candidate anyway." >&2
      return 0
    fi
    echo "Error: the candidate tag has moved since ${sha} was verified -- refusing to promote an" >&2
    echo "UNVERIFIED deployment. Mismatched revisions: ${mismatch}" >&2
    echo "  Re-run the verification lifecycle + --record-verified against the current candidate, or pass" >&2
    echo "  --skip-verification-check to override deliberately." >&2
    exit 1
  fi
  echo "Confirmed: all four candidate-tagged revisions match the digests verified at ${sha}"
  echo "  (rating-engine=${RATING_ENGINE_DIGEST:-<unknown>}, worker/api=${WORKER_DIGEST:-<unknown>}, web=${WEB_DIGEST:-<unknown>})."
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

PROMOTE=false
CANARY_PERCENT=""
SKIP_VERIFICATION_CHECK=false
AUTO_ROLLBACK_ON_FAILURE=false
for arg in "$@"; do
  case "$arg" in
    --promote) PROMOTE=true ;;
    --canary-percent=*) CANARY_PERCENT="${arg#*=}" ;;
    --skip-verification-check) SKIP_VERIFICATION_CHECK=true ;;
    --auto-rollback-on-failure) AUTO_ROLLBACK_ON_FAILURE=true ;;
    --help|-h)
      echo "Usage: $0 [--promote] [--canary-percent=N] [--skip-verification-check] [--auto-rollback-on-failure]"
      echo "  (no flag)                   Print the promotion plan. No gcloud/network calls."
      echo "  --promote                   Actually apply it (100% traffic unless --canary-percent is given)."
      echo "  --canary-percent=N          Shift only N% of api/worker/web traffic to the new"
      echo "                              revisions and STOP -- re-run with --promote (no"
      echo "                              --canary-percent) to complete the shift to 100% once"
      echo "                              the canary has been verified."
      echo "  --skip-verification-check   Deliberately bypass the verification marker"
      echo "                              and digest-pin requirement (not the default; use only"
      echo "                              when a human operator has verified the candidate"
      echo "                              out-of-band)."
      echo "  --auto-rollback-on-failure  If the post-promotion web-bundle or Pub/Sub-routing"
      echo "                              check fails, print AND invoke the exact"
      echo "                              infrastructure/rollback.sh command for the prior"
      echo "                              revisions -- rollback.sh still requires its own"
      echo "                              explicit --rollback confirmation and never mutates"
      echo "                              anything by itself (see its own safety note)."
      exit 0
      ;;
  esac
done

print_plan() {
  local re_digest worker_digest api_digest web_digest
  re_digest="$([ -n "$RATING_ENGINE_REV" ] && resolve_revision_digest rateguard-rating-engine "$RATING_ENGINE_REV" || true)"
  worker_digest="$([ -n "$WORKER_REV" ] && resolve_revision_digest rateguard-worker "$WORKER_REV" || true)"
  api_digest="$([ -n "$API_REV" ] && resolve_revision_digest rateguard-api "$API_REV" || true)"
  web_digest="$([ -n "$WEB_REV" ] && resolve_revision_digest rateguard-web "$WEB_REV" || true)"

  cat <<PLAN
========================================================
   RateGuard Enhanced -- Promote Candidate to Production PLAN
========================================================
CANDIDATE RESOURCES (what would be promoted -- reused unchanged; this script
never builds or rebuilds ANY image, including web -- see BLOCKER 4):
  rating-engine:  ${RATING_ENGINE_REV:-<none found>}
  worker:         ${WORKER_REV:-<none found>}
  api:            ${API_REV:-<none found>}
  web:            ${WEB_REV:-<none found>}

VERIFIED DIGESTS (pinned from infrastructure/.candidate-verified/<sha>.evidence
when present; promotion refuses to proceed if the candidate tag has moved off
these -- see require_verified_marker):
  rating-engine:  ${re_digest:-<not resolved>}
  worker/api:     ${worker_digest:-<not resolved>} (must equal api digest: ${api_digest:-<not resolved>})
  web:            ${web_digest:-<not resolved>}

PRODUCTION-CONFIG RELEASE REVISIONS (currently-live, captured now BEFORE any
promotion action -- these are the ROLLBACK REVISIONS if the promotion needs
to be reversed; see infrastructure/rollback.sh):
  rating-engine:  ${PRIOR_RATING_ENGINE_REV:-<none found>}
  worker:         ${PRIOR_WORKER_REV:-<none found>}
  api:            ${PRIOR_API_REV:-<none found>}
  web:            ${PRIOR_WEB_REV:-<none found>}

Verification requirement: this script refuses to promote a candidate SHA
that has no marker at ${VERIFIED_MARKER_DIR}/<full-sha> (written by
'deploy_candidate_enhanced.sh --complete-verification' + '--record-verified'), OR
whose currently-candidate-tagged revisions no longer match the digests
recorded in <full-sha>.evidence, unless --skip-verification-check is passed
explicitly (a deliberate operator override, not the default).

STABLE PRODUCTION URLs these will be repointed at (all pre-existing, never
provisioned by this script; NEVER a movable --tag URL):
  API:            ${PROD_API_URL}
  Web:            ${PROD_WEB_URL}
  Rating-engine:  ${PROD_RATING_ENGINE_URL}
  Firestore collection: ${PROD_FIRESTORE_COLLECTION}
  GCS bucket:            ${PROD_GCS_BUCKET}
  BigQuery dataset:      ${PROD_BIGQUERY_DATASET} (${PROD_BIGQUERY_PORTFOLIO_TABLE} / ${PROD_BIGQUERY_RESULTS_TABLE})
  Pub/Sub topic:         ${PROD_TOPIC}
  Pub/Sub subscription:  ${PROD_SUBSCRIPTION} (push endpoint + OIDC audience
                          are both the STABLE, untagged worker URL -- proven
                          form, see infrastructure/setup_impact_pubsub.sh --
                          never a --tag URL; unchanged by this script, only
                          verified below)

CORS TRANSITION: RATEGUARD_CORS_ORIGINS on the promoted api revision moves
from the candidate web origin (candidate---...) to exactly one explicit
origin, ["${PROD_WEB_URL}"] -- never a wildcard, never both origins at once.

Steps --promote would run, in order:
  1) rateguard-rating-engine: confirm its candidate revision runs under the
     dedicated rating-engine service account, then update-traffic to 100%
     on ${RATING_ENGINE_REV:-<candidate revision>}.
  2) rateguard-worker: --update-env-vars pointing Firestore/GCS/BigQuery/
     Pub/Sub topic AND the rating-engine connector URL at the PRODUCTION
     names/STABLE URL above (BLOCKER 4 fix -- a prior version of this script
     left the candidate-tagged rating-engine URL in place after promotion),
     then update-traffic to 100% on the resulting revision.
  3) rateguard-api: same --update-env-vars, plus RATEGUARD_CORS_ORIGINS ->
     ["${PROD_WEB_URL}"], then update-traffic to 100%.
  4) rateguard-web: reuse the EXACT candidate image digest (no rebuild --
     see BLOCKER 3), deploy it to a --no-traffic --tag verify revision with
     --update-env-vars RATEGUARD_API_URL=${PROD_API_URL} (a plain runtime
     Cloud Run env var, never baked into the image), fetch the served page
     and grep-confirm window.__RATEGUARD_RUNTIME_CONFIG__ carries
     "${PROD_API_URL}" and does NOT contain "candidate---" or
     "localhost:8000" -- only then update-traffic to 100%.
  5) Verify Pub/Sub routing after promotion: describe ${PROD_SUBSCRIPTION}
     and confirm its pushEndpoint/oidcToken.audience still point at the
     STABLE (non-tag) worker URL, never a --tag URL -- refuses to declare
     success otherwise.
  6) Print a verification checklist (does NOT execute the checks): create one
     mission through ${PROD_API_URL}, then read the resulting document
     directly from Firestore (${PROD_FIRESTORE_COLLECTION}) and the uploaded
     artifact directly from gs://${PROD_GCS_BUCKET} -- never trust the app's
     own API response alone for this confirmation.
  7) CLEANUP PLAN: the ephemeral --tag verify web revision from step 4 is
     left in place at 0% traffic (same disposal policy as the candidate
     revisions -- available for investigation, never auto-deleted). No
     ephemeral candidate Pub/Sub queue resources are removed here -- those
     were already deleted by --complete-verification's own cleanup; this script
     never recreates or touches them.
  8) ROLLBACK REVISIONS: print the exact infrastructure/rollback.sh
     invocation using the prior revisions captured above, ready to run
     immediately if anything looks wrong. With --auto-rollback-on-failure,
     a failed post-promotion health/config check (steps 4-5) prints AND
     invokes that same rollback.sh command -- which still requires its own
     explicit --rollback confirmation and prints rather than mutates
     anything by itself (see infrastructure/rollback.sh's own safety note).

With --canary-percent=N instead of a bare --promote, steps 1-3 shift only N%
of rating-engine/worker/api traffic (web is not touched in canary mode) and
the script stops -- re-run with a bare --promote to complete the shift to
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

require_verified_marker "$GIT_SHA" "$RATING_ENGINE_REV" "$WORKER_REV" "$API_REV" "${WEB_REV:-}"

echo "Confirming the candidate rating-engine revision runs under the dedicated service account..."
RATING_ENGINE_CANDIDATE_SA="$(gcloud run revisions describe "$RATING_ENGINE_REV" --region "$REGION" --project "$PROJECT_ID" --format="value(spec.serviceAccountName)")"
if [ "$RATING_ENGINE_CANDIDATE_SA" != "$RATING_ENGINE_SA" ]; then
  echo "Error: candidate rating-engine revision ${RATING_ENGINE_REV} runs under" >&2
  echo "'${RATING_ENGINE_CANDIDATE_SA:-<none>}', not the dedicated ${RATING_ENGINE_SA}. Refusing to promote." >&2
  exit 1
fi
echo "   Confirmed: ${RATING_ENGINE_SA}."

echo "Prior production revisions captured for rollback (infrastructure/rollback.sh):"
echo "  api=${PRIOR_API_REV:-<none>} worker=${PRIOR_WORKER_REV:-<none>} web=${PRIOR_WEB_REV:-<none>} rating-engine=${PRIOR_RATING_ENGINE_REV:-<none>}"

run_rollback_now() {
  echo ""
  echo "!!! Post-promotion check failed. Rollback revisions: api=${PRIOR_API_REV:-<unknown>}" >&2
  echo "!!! worker=${PRIOR_WORKER_REV:-<unknown>} web=${PRIOR_WEB_REV:-<unknown>}" >&2
  echo "!!! rating-engine=${PRIOR_RATING_ENGINE_REV:-<unknown>}" >&2
  if [ "$AUTO_ROLLBACK_ON_FAILURE" = true ]; then
    echo "!!! --auto-rollback-on-failure was passed -- invoking infrastructure/rollback.sh now" >&2
    echo "!!! (it still requires its own explicit confirmation and will not mutate anything" >&2
    echo "!!! by itself -- see its own safety note)." >&2
    infrastructure/rollback.sh --rollback \
      --api-revision="${PRIOR_API_REV:-}" \
      --worker-revision="${PRIOR_WORKER_REV:-}" \
      --web-revision="${PRIOR_WEB_REV:-}" \
      --rating-engine-revision="${PRIOR_RATING_ENGINE_REV:-}" || true
  else
    echo "!!! Run manually (or re-run this promotion with --auto-rollback-on-failure):" >&2
    echo "!!!   infrastructure/rollback.sh --rollback --api-revision=${PRIOR_API_REV:-<unknown>} \\" >&2
    echo "!!!     --worker-revision=${PRIOR_WORKER_REV:-<unknown>} --web-revision=${PRIOR_WEB_REV:-<unknown>} \\" >&2
    echo "!!!     --rating-engine-revision=${PRIOR_RATING_ENGINE_REV:-<unknown>}" >&2
  fi
  exit 1
}

if [ -n "$CANARY_PERCENT" ]; then
  echo "1. Canary: shifting ${CANARY_PERCENT}% of rating-engine traffic to ${RATING_ENGINE_REV}..."
  gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${RATING_ENGINE_REV}=${CANARY_PERCENT}"
else
  echo "1. Promoting rating-engine (${RATING_ENGINE_REV}) to 100%..."
  gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${RATING_ENGINE_REV}=100"
fi

echo "2. Repointing worker (${WORKER_REV}) at production data-plane resources AND the STABLE"
echo "   (untagged) production rating-engine URL -- BLOCKER 4 fix: a prior version of this"
echo "   script never repointed this, so a promoted worker kept calling the movable"
echo "   candidate-tagged rating-engine URL forever..."
gcloud run services update rateguard-worker --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION},RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL=${PROD_RATING_ENGINE_URL},RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL=${PROD_RATING_ENGINE_URL}"
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

echo "3. Repointing api (${API_REV}) at production data-plane resources + rating-engine URL + CORS..."
gcloud run services update rateguard-api --region "$REGION" --project "$PROJECT_ID" \
  --update-env-vars "RATEGUARD_FIRESTORE_COLLECTION=${PROD_FIRESTORE_COLLECTION},RATEGUARD_GCS_BUCKET=${PROD_GCS_BUCKET},RATEGUARD_BIGQUERY_DATASET=${PROD_BIGQUERY_DATASET},RATEGUARD_BIGQUERY_PORTFOLIO_TABLE=${PROD_BIGQUERY_PORTFOLIO_TABLE},RATEGUARD_BIGQUERY_RESULTS_TABLE=${PROD_BIGQUERY_RESULTS_TABLE},RATEGUARD_PUBSUB_TOPIC=${PROD_TOPIC},RATEGUARD_PUBSUB_SUBSCRIPTION=${PROD_SUBSCRIPTION},RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL=${PROD_RATING_ENGINE_URL},RATEGUARD_VENDOR_GATEWAY_CONNECTOR_BASE_URL=${PROD_RATING_ENGINE_URL}" \
  --update-env-vars "^@^RATEGUARD_CORS_ORIGINS=[\"${PROD_WEB_URL}\"]"
API_NEW_REV="$(gcloud run services describe rateguard-api --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
if [ -n "$CANARY_PERCENT" ]; then
  echo "   Canary: shifting ${CANARY_PERCENT}% of traffic to ${API_NEW_REV}..."
  gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${API_NEW_REV}=${CANARY_PERCENT}"
  echo ""
  echo "Canary at ${CANARY_PERCENT}% for rating-engine/worker/api. Web was NOT touched in"
  echo "canary mode. Verify the canary, then re-run with a bare --promote to complete to 100%"
  echo "(including the web runtime-config deploy step)."
  exit 0
else
  echo "   Promoting traffic to ${API_NEW_REV} (100%)..."
  gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT_ID" \
    --to-revisions="${API_NEW_REV}=100"
fi

echo "4. Reusing the EXACT candidate web image digest (no rebuild -- BLOCKER 3/4 fix: a prior"
echo "   version of this script rebuilt the web image here so it could bake in the production"
echo "   API URL at build time; that meant the promoted image was NEVER byte-for-byte what was"
echo "   verified, and a web image built for a candidate API URL could never be safely reused)."
if [ -z "$WEB_REV" ]; then
  echo "Error: no candidate-tagged rateguard-web revision found. Run deploy_candidate_enhanced.sh" >&2
  echo "--deploy-candidate first." >&2
  exit 1
fi
WEB_DIGEST_IMAGE="$(resolve_revision_digest rateguard-web "$WEB_REV")"
if [ -z "$WEB_DIGEST_IMAGE" ]; then
  echo "Error: could not resolve the image digest running on candidate web revision ${WEB_REV}." >&2
  exit 1
fi
echo "   Candidate web digest (unchanged, being promoted as-is): ${WEB_DIGEST_IMAGE}"

echo "   Deploying that exact digest to a --no-traffic --tag verify revision, with"
echo "   RATEGUARD_API_URL=${PROD_API_URL} as deployment-time RUNTIME config (never baked"
echo "   into the image -- see frontend/src/lib/runtimeConfig.ts)..."
gcloud run deploy rateguard-web --image "$WEB_DIGEST_IMAGE" --region "$REGION" --project "$PROJECT_ID" \
  --no-traffic --tag verify \
  --update-env-vars "RATEGUARD_API_URL=${PROD_API_URL}"

echo "   Fetching the served page to confirm the injected runtime config before shifting traffic..."
VERIFY_HTML="$(curl -s "https://verify---rateguard-web-nwhotixfva-uc.a.run.app/")"
if ! echo "$VERIFY_HTML" | grep -q "__RATEGUARD_RUNTIME_CONFIG__"; then
  echo "Error: served page does not contain window.__RATEGUARD_RUNTIME_CONFIG__ -- refusing to" >&2
  echo "promote web traffic automatically. Inspect https://verify---rateguard-web-nwhotixfva-uc.a.run.app/ manually." >&2
  exit 1
fi
if ! echo "$VERIFY_HTML" | grep -q "$PROD_API_URL"; then
  echo "Error: served page's runtime config does not contain the production API URL ($PROD_API_URL)." >&2
  echo "Refusing to promote web traffic." >&2
  exit 1
fi
if echo "$VERIFY_HTML" | grep -qE "candidate---|localhost:8000"; then
  echo "Error: served page's runtime config still references a candidate-tagged or localhost" >&2
  echo "API URL. Refusing to promote web traffic." >&2
  exit 1
fi
echo "   Confirmed: runtime config correctly injects ${PROD_API_URL}, the SAME image digest as"
echo "   the verified candidate (${WEB_DIGEST_IMAGE})."

WEB_NEW_REV="$(gcloud run services describe rateguard-web --region "$REGION" --project "$PROJECT_ID" --format="value(status.latestCreatedRevisionName)")"
echo "   Promoting web traffic to ${WEB_NEW_REV}..."
gcloud run services update-traffic rateguard-web --region "$REGION" --project "$PROJECT_ID" \
  --to-revisions="${WEB_NEW_REV}=100" || run_rollback_now

echo "5. Verifying Pub/Sub routing after promotion..."
PUSH_ENDPOINT_AFTER="$(gcloud pubsub subscriptions describe "$PROD_SUBSCRIPTION" --project "$PROJECT_ID" --format="value(pushConfig.pushEndpoint)")"
PUSH_AUDIENCE_AFTER="$(gcloud pubsub subscriptions describe "$PROD_SUBSCRIPTION" --project "$PROJECT_ID" --format="value(pushConfig.oidcToken.audience)")"
if echo "$PUSH_ENDPOINT_AFTER" | grep -qE "candidate---|verify---"; then
  echo "Error: ${PROD_SUBSCRIPTION} push endpoint (${PUSH_ENDPOINT_AFTER}) is tag-scoped" >&2
  echo "(candidate--- or verify---) after promotion -- it must be the stable worker URL." >&2
  echo "Investigate before treating this promotion as safe." >&2
  run_rollback_now
fi
if echo "$PUSH_AUDIENCE_AFTER" | grep -qE "candidate---|verify---"; then
  echo "Error: ${PROD_SUBSCRIPTION} OIDC audience (${PUSH_AUDIENCE_AFTER}) is tag-scoped after" >&2
  echo "promotion -- it must be the stable, untagged worker URL, the proven-working form (see" >&2
  echo "infrastructure/setup_impact_pubsub.sh). Investigate before treating this promotion as safe." >&2
  run_rollback_now
fi
echo "   Confirmed: ${PROD_SUBSCRIPTION} pushes to ${PUSH_ENDPOINT_AFTER} (audience ${PUSH_AUDIENCE_AFTER}), both stable non-tag URLs."

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
