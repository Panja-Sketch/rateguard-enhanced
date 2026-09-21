#!/usr/bin/env bash
# Idempotently provisions the connector-impact Pub/Sub resources (project rateguard-enhanced):
#
#   topic         impact-batches                       (worker publishes batch references)
#   subscription  impact-batches-worker-sub            (push -> worker /internal/pubsub/impact-batch, OIDC)
#   DLQ topic     impact-batches-dead-letter
#   DLQ pull sub  impact-batches-dead-letter-sub       (inspection only)
#
# Messages carry opaque references only (tenant_id, job_id, batch_no) - never
# rows, inputs or premiums. Batch handling is idempotent, so redelivery is safe.
#
# Usage: infrastructure/setup_impact_pubsub.sh [WORKER_URL]
set -euo pipefail

PROJECT="rateguard-enhanced"
PROJECT_NUMBER="316435199506"
WORKER_SA="rateguard-worker-sa@${PROJECT}.iam.gserviceaccount.com"
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
WORKER_URL="${1:-$(gcloud run services describe rateguard-worker --region us-central1 --project "$PROJECT" --format='value(status.url)')}"

TOPIC="impact-batches"
DLQ_TOPIC="impact-batches-dead-letter"
SUB="impact-batches-worker-sub"
DLQ_SUB="impact-batches-dead-letter-sub"

ensure_topic() { gcloud pubsub topics describe "$1" --project "$PROJECT" >/dev/null 2>&1 || gcloud pubsub topics create "$1" --project "$PROJECT"; }

ensure_topic "$TOPIC"
ensure_topic "$DLQ_TOPIC"

gcloud pubsub subscriptions describe "$DLQ_SUB" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud pubsub subscriptions create "$DLQ_SUB" --topic "$DLQ_TOPIC" --project "$PROJECT" --ack-deadline=60

if ! gcloud pubsub subscriptions describe "$SUB" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud pubsub subscriptions create "$SUB" --topic "$TOPIC" --project "$PROJECT" \
    --push-endpoint="${WORKER_URL}/internal/pubsub/impact-batch" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$WORKER_URL" \
    --ack-deadline=600 --min-retry-delay=10s --max-retry-delay=600s \
    --dead-letter-topic="projects/${PROJECT}/topics/${DLQ_TOPIC}" --max-delivery-attempts=5
fi

# The Pub/Sub service agent forwards dead letters: publisher on the DLQ topic,
# subscriber on the source subscription (resource-scoped, nothing project-wide).
gcloud pubsub topics add-iam-policy-binding "$DLQ_TOPIC" --project "$PROJECT" \
  --member="serviceAccount:${PUBSUB_AGENT}" --role="roles/pubsub.publisher" >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding "$SUB" --project "$PROJECT" \
  --member="serviceAccount:${PUBSUB_AGENT}" --role="roles/pubsub.subscriber" >/dev/null

# The worker (batch coordinator) publishes batch references; the API never does.
gcloud pubsub topics add-iam-policy-binding "$TOPIC" --project "$PROJECT" \
  --member="serviceAccount:${WORKER_SA}" --role="roles/pubsub.publisher" >/dev/null

echo "Impact Pub/Sub resources ready: $TOPIC, $SUB (push), $DLQ_TOPIC, $DLQ_SUB"
