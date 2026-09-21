#!/usr/bin/env bash
# RateGuard Enhanced - release deployment by immutable digest (project rateguard-enhanced).
#
#   infrastructure/deploy_release.sh build          # build 4 images tagged with the FULL git SHA, record digests
#   infrastructure/deploy_release.sh stage          # deploy no-traffic revisions tagged `p8rc`, run health checks
#   infrastructure/deploy_release.sh shift 25|50|100 # move production traffic to the tagged revisions
#   infrastructure/deploy_release.sh rollback       # send 100% traffic back to the previous revisions
#
# Requires a clean worktree at the commit being released. Never uses `latest`.
# Digests are written to infrastructure/.release-digests.env (git-ignored).
set -euo pipefail

PROJECT="rateguard-enhanced"
REGION="us-central1"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/rateguard-images"
TAG_NAME="p8rc"
SHA="$(git rev-parse HEAD)"
DIGESTS="infrastructure/.release-digests.env"
API_URL="${RG_API_URL:-https://rateguard-api-nwhotixfva-uc.a.run.app}"

require_clean() {
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "worktree not clean" >&2; exit 1; }
}

digest_of() { gcloud artifacts docker images describe "$1" --project "$PROJECT" --format='value(image_summary.digest)'; }

web_env() {
  local out="" k
  for k in API_KEY AUTH_DOMAIN PROJECT_ID STORAGE_BUCKET MESSAGING_SENDER_ID APP_ID MEASUREMENT_ID; do
    out="${out},_NEXT_PUBLIC_FIREBASE_${k}=$(grep "^NEXT_PUBLIC_FIREBASE_${k}=" frontend/.env.local | cut -d= -f2-)"
  done
  echo "$out"
}

cmd_build() {
  require_clean
  gcloud builds submit . --project "$PROJECT" --config backend/cloudbuild.yaml --substitutions "_IMAGE_TAG=${SHA}"
  gcloud builds submit . --project "$PROJECT" --config backend/rating_engine/cloudbuild.yaml --substitutions "_IMAGE_TAG=${SHA}"
  gcloud builds submit ./frontend --project "$PROJECT" --config frontend/cloudbuild.yaml \
    --substitutions "_IMAGE_TAG=${SHA},_NEXT_PUBLIC_RATEGUARD_API_URL=${API_URL}$(web_env)"
  {
    echo "GIT_SHA=${SHA}"
    echo "BACKEND_DIGEST=$(digest_of ${REPO}/rateguard-api:${SHA})"
    echo "ENGINE_DIGEST=$(digest_of ${REPO}/rateguard-rating-engine:${SHA})"
    echo "WEB_DIGEST=$(digest_of ${REPO}/rateguard-web:${SHA})"
  } > "$DIGESTS"
  cat "$DIGESTS"
}

# Records the currently serving revisions (for rollback) before staging.
cmd_stage() {
  # shellcheck disable=SC1090
  source "$DIGESTS"
  for s in rateguard-worker rateguard-api rateguard-rating-engine rateguard-web; do
    echo "PREV_${s//-/_}=$(gcloud run services describe "$s" --region "$REGION" --project "$PROJECT" --format='value(status.latestReadyRevisionName)')"
  done | tee infrastructure/.previous-revisions.env

  local common=(--region "$REGION" --project "$PROJECT" --no-traffic --tag "$TAG_NAME")
  gcloud run deploy rateguard-rating-engine "${common[@]}" --image "${REPO}/rateguard-rating-engine@${ENGINE_DIGEST}" \
    --update-env-vars "RATEGUARD_GIT_SHA=${GIT_SHA},RATEGUARD_IMAGE_DIGEST=${ENGINE_DIGEST}"
  gcloud run deploy rateguard-worker "${common[@]}" --image "${REPO}/rateguard-api@${BACKEND_DIGEST}" \
    --concurrency 8 --max-instances 6 --timeout 600 \
    --update-env-vars "RATEGUARD_GIT_SHA=${GIT_SHA},RATEGUARD_IMAGE_DIGEST=${BACKEND_DIGEST},RATEGUARD_IMPACT_TOPIC=impact-batches"
  gcloud run deploy rateguard-api "${common[@]}" --image "${REPO}/rateguard-api@${BACKEND_DIGEST}" \
    --update-env-vars "RATEGUARD_GIT_SHA=${GIT_SHA},RATEGUARD_IMAGE_DIGEST=${BACKEND_DIGEST}"
  gcloud run deploy rateguard-web "${common[@]}" --image "${REPO}/rateguard-web@${WEB_DIGEST}"
}

cmd_shift() {
  local pct="${1:?percent}"
  for s in rateguard-rating-engine rateguard-worker rateguard-api rateguard-web; do
    if [ "$pct" = "100" ]; then
      gcloud run services update-traffic "$s" --region "$REGION" --project "$PROJECT" --to-tags "${TAG_NAME}=100"
    else
      gcloud run services update-traffic "$s" --region "$REGION" --project "$PROJECT" --to-tags "${TAG_NAME}=${pct}"
    fi
  done
}

cmd_rollback() {
  # shellcheck disable=SC1091
  source infrastructure/.previous-revisions.env
  gcloud run services update-traffic rateguard-rating-engine --region "$REGION" --project "$PROJECT" --to-revisions "${PREV_rateguard_rating_engine}=100"
  gcloud run services update-traffic rateguard-worker --region "$REGION" --project "$PROJECT" --to-revisions "${PREV_rateguard_worker}=100"
  gcloud run services update-traffic rateguard-api --region "$REGION" --project "$PROJECT" --to-revisions "${PREV_rateguard_api}=100"
  gcloud run services update-traffic rateguard-web --region "$REGION" --project "$PROJECT" --to-revisions "${PREV_rateguard_web}=100"
}

case "${1:-}" in
  build) cmd_build ;;
  stage) cmd_stage ;;
  shift) cmd_shift "${2:-}" ;;
  rollback) cmd_rollback ;;
  *) echo "usage: $0 build|stage|shift <pct>|rollback" >&2; exit 2 ;;
esac
