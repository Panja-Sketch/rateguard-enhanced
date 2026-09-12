#!/usr/bin/env bash
# Automated RateGuard Cloud Deployment & Multi-Service Architecture Configuration Script

set -euo pipefail

PROJECT_ID="rateguard-ai"
REGION="us-central1"
RUNTIME_SA="rateguard-runtime@rateguard-ai.iam.gserviceaccount.com"
PUBSUB_PUSH_SA="rateguard-pubsub-push@rateguard-ai.iam.gserviceaccount.com"
BACKEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-api:latest"

echo "========================================================"
echo "   RateGuard AI -- Production Cloud Deployment          "
echo "   Project: $PROJECT_ID | Region: $REGION"
echo "========================================================"

gcloud config set project "$PROJECT_ID"

# 0. Resolve Project Number & Pub/Sub Service Agent
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
echo "   Project Number: $PROJECT_NUMBER"
echo "   Pub/Sub Service Agent: $PUBSUB_SERVICE_AGENT"

# 1. Idempotent Service Account Setup & Least-Privilege IAM Roles
echo "\n1. Verifying and configuring Service Accounts..."

# Runtime SA
if ! gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
  echo "   Creating Runtime Service Account ($RUNTIME_SA)..."
  gcloud iam service-accounts create rateguard-runtime \
    --display-name="RateGuard Runtime Service Account"
else
  echo "   Runtime Service Account exists: $RUNTIME_SA"
fi

# Grant least-privilege roles to Runtime SA
RUNTIME_ROLES=(
  "roles/aiplatform.user"       # Vertex AI / Gemini 3.7 Flash invocation
  "roles/datastore.user"        # Firestore read/write
  "roles/bigquery.dataEditor"   # BigQuery results table write
  "roles/bigquery.jobUser"      # BigQuery query execution
  "roles/storage.objectUser"    # Cloud Storage artifact access
  "roles/pubsub.publisher"      # Pub/Sub message publishing
)

for role in "${RUNTIME_ROLES[@]}"; do
  echo "   Granting $role to Runtime SA..."
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="$role" >/dev/null
done

# Pub/Sub Push SA
if ! gcloud iam service-accounts describe "$PUBSUB_PUSH_SA" >/dev/null 2>&1; then
  echo "   Creating Pub/Sub Push Service Account ($PUBSUB_PUSH_SA)..."
  gcloud iam service-accounts create rateguard-pubsub-push \
    --display-name="RateGuard PubSub Push Invoker SA"
else
  echo "   Pub/Sub Push Service Account exists: $PUBSUB_PUSH_SA"
fi

# Grant roles/iam.serviceAccountTokenCreator to Google-managed Pub/Sub Service Agent on Push SA
echo "   Granting roles/iam.serviceAccountTokenCreator to Pub/Sub Service Agent on Push SA..."
gcloud iam service-accounts add-iam-policy-binding "$PUBSUB_PUSH_SA" \
  --member="serviceAccount:$PUBSUB_SERVICE_AGENT" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null

# Note on actAs requirement for deployer
echo "   [IAM Verification] Deployer identity must hold roles/iam.serviceAccountUser on $PUBSUB_PUSH_SA to execute modify-push-config."

# 2. Build Backend Docker Container via Cloud Build (Repository Root Context)
echo "\n2. Building Backend Docker Container via Cloud Build..."
gcloud builds submit . \
  --config=./backend/cloudbuild.yaml

# 3. Deploy Public API Cloud Run Service (rateguard-api)
echo "\n3. Deploying Public API Service (rateguard-api)..."
gcloud run deploy rateguard-api \
  --image "$BACKEND_IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --memory=512Mi \
  --env-vars-file=./infrastructure/runtime-env.yaml

API_URL=$(gcloud run services describe rateguard-api --region "$REGION" --format "value(status.url)")
echo "   Public API URL: $API_URL"

# 4. Deploy Private Worker Cloud Run Service (rateguard-worker) using SAME Backend Image
echo "\n4. Deploying Private Worker Service (rateguard-worker)..."
gcloud run deploy rateguard-worker \
  --image "$BACKEND_IMAGE" \
  --region "$REGION" \
  --platform managed \
  --no-allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --memory=1Gi \
  --env-vars-file=./infrastructure/runtime-env.yaml

WORKER_URL=$(gcloud run services describe rateguard-worker --region "$REGION" --format "value(status.url)")
echo "   Private Worker URL: $WORKER_URL"

# Grant roles/run.invoker to Pub/Sub Push SA on rateguard-worker
echo "   Granting roles/run.invoker to Pub/Sub Push SA on rateguard-worker..."
gcloud run services add-iam-policy-binding rateguard-worker \
  --region "$REGION" \
  --member="serviceAccount:$PUBSUB_PUSH_SA" \
  --role="roles/run.invoker" >/dev/null

# 4b. Idempotent Pub/Sub Topic, Subscription, and Dead-Letter Queue Setup
#
# Ack deadline / worker timeout alignment: rateguard-worker's Cloud Run
# request timeout is 300s (this script does not change that in this pass).
# Pub/Sub's push-subscription ack deadline caps at 600s — the hard platform
# maximum — which is what ACK_DEADLINE_SECONDS is set to below specifically
# so it always exceeds the worker's request timeout with a safe buffer (a
# full 300s / 2x margin today). A mission whose synchronous processing
# legitimately takes longer than the ack deadline would otherwise be
# redelivered by Pub/Sub mid-flight even though the worker is still correctly
# working on it. Duplicate delivery is still possible regardless of this
# alignment (Pub/Sub is at-least-once by design) — that is exactly why the
# MissionExecutionService atomic lease/idempotency protection
# (backend/app/services/mission_execution_service.py) must remain in place;
# it is the real correctness guarantee, this timing alignment only reduces
# how often it gets exercised.
ACK_DEADLINE_SECONDS=600
MIN_RETRY_BACKOFF_SECONDS=10
MAX_RETRY_BACKOFF_SECONDS=600
MAX_DELIVERY_ATTEMPTS=5
DLQ_TOPIC="assurance-runs-dlq"
DLQ_INSPECTION_SUBSCRIPTION="assurance-runs-dlq-inspect"

echo "\n4b. Verifying Pub/Sub topic/subscription/dead-letter-queue configuration..."

if ! gcloud pubsub topics describe assurance-runs >/dev/null 2>&1; then
  echo "   Creating Pub/Sub topic 'assurance-runs'..."
  gcloud pubsub topics create assurance-runs
else
  echo "   Pub/Sub topic 'assurance-runs' exists."
fi

if ! gcloud pubsub topics describe "$DLQ_TOPIC" >/dev/null 2>&1; then
  echo "   Creating dead-letter topic '$DLQ_TOPIC'..."
  gcloud pubsub topics create "$DLQ_TOPIC"
else
  echo "   Dead-letter topic '$DLQ_TOPIC' exists."
fi

if ! gcloud pubsub subscriptions describe assurance-worker >/dev/null 2>&1; then
  echo "   Creating Pub/Sub subscription 'assurance-worker' (push endpoint configured in step 5)..."
  gcloud pubsub subscriptions create assurance-worker \
    --topic=assurance-runs \
    --ack-deadline="$ACK_DEADLINE_SECONDS" \
    --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" \
    --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" \
    --dead-letter-topic="$DLQ_TOPIC" \
    --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
else
  echo "   Pub/Sub subscription 'assurance-worker' exists; reapplying timing/retry/dead-letter configuration (idempotent update)..."
  gcloud pubsub subscriptions update assurance-worker \
    --ack-deadline="$ACK_DEADLINE_SECONDS" \
    --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" \
    --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" \
    --dead-letter-topic="$DLQ_TOPIC" \
    --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
fi

# Pull-based inspection subscription on the DLQ topic so a poison message can
# be manually pulled and examined rather than only ever silently retained.
# Not consumed by any running service.
if ! gcloud pubsub subscriptions describe "$DLQ_INSPECTION_SUBSCRIPTION" >/dev/null 2>&1; then
  echo "   Creating dead-letter inspection subscription '$DLQ_INSPECTION_SUBSCRIPTION'..."
  gcloud pubsub subscriptions create "$DLQ_INSPECTION_SUBSCRIPTION" \
    --topic="$DLQ_TOPIC"
else
  echo "   Dead-letter inspection subscription '$DLQ_INSPECTION_SUBSCRIPTION' exists."
fi

# Narrowly-scoped grants required for Pub/Sub to forward undeliverable
# messages to the dead-letter topic: the Pub/Sub service agent needs
# publisher rights on the DLQ topic specifically (not project-wide), and
# subscriber rights on the source subscription specifically. Both
# `add-iam-policy-binding` calls are naturally idempotent (re-adding an
# existing binding is a no-op, not an error).
echo "   Granting narrowly-scoped dead-letter forwarding permissions to Pub/Sub Service Agent..."
gcloud pubsub topics add-iam-policy-binding "$DLQ_TOPIC" \
  --member="serviceAccount:$PUBSUB_SERVICE_AGENT" \
  --role="roles/pubsub.publisher" >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding assurance-worker \
  --member="serviceAccount:$PUBSUB_SERVICE_AGENT" \
  --role="roles/pubsub.subscriber" >/dev/null

# 5. Configure Pub/Sub Subscription Push to Private Worker Endpoint
echo "\n5. Configuring Pub/Sub Subscription ('assurance-worker') Push to Private Worker..."
gcloud pubsub subscriptions modify-push-config assurance-worker \
  --push-endpoint="${WORKER_URL}/internal/pubsub/assurance" \
  --push-auth-service-account="$PUBSUB_PUSH_SA"

# 6. Build and Deploy Next.js Frontend with Embedded Build-Time API URL (rateguard-web)
echo "\n6. Building and Deploying Frontend Web Dashboard (rateguard-web)..."
gcloud builds submit ./frontend \
  --config=./frontend/cloudbuild.yaml \
  --substitutions=_NEXT_PUBLIC_RATEGUARD_API_URL="$API_URL"

gcloud run deploy rateguard-web \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/rateguard/rateguard-web:latest" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated

WEB_URL=$(gcloud run services describe rateguard-web --region "$REGION" --format "value(status.url)")
echo "   Public Web Dashboard URL: $WEB_URL"

# 7. Update Backend CORS Config for Deployed Web Origin using env file
echo "\n7. Updating Backend CORS for Deployed Web Origin..."
TEMP_ENV="infrastructure/.deploy-env.yaml"
cp infrastructure/runtime-env.yaml "$TEMP_ENV"
echo "RATEGUARD_CORS_ORIGINS: '[\"http://localhost:3000\",\"${WEB_URL}\"]'" >> "$TEMP_ENV"

gcloud run deploy rateguard-api \
  --image "$BACKEND_IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --memory=512Mi \
  --env-vars-file="$TEMP_ENV"

rm -f "$TEMP_ENV"

echo "\n========================================================"
echo "DEPLOYMENT COMPLETED SUCCESSFULLY!"
echo "Public API Service:      $API_URL"
echo "Private Worker Service:  $WORKER_URL"
echo "Public Web Dashboard:    $WEB_URL"
echo "Pub/Sub Push Endpoint:   ${WORKER_URL}/internal/pubsub/assurance"
echo "Backend Image:           $BACKEND_IMAGE"
echo "Gemini Model:            gemini-3.7-flash"
echo "========================================================"
