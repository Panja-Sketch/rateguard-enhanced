# RateGuard AI -- Production Cloud Deployment Automation Script (PowerShell)

$ErrorActionPreference = "Stop"

$PROJECT_ID = "rateguard-ai"
$REGION = "us-central1"
$RUNTIME_SA = "rateguard-runtime@rateguard-ai.iam.gserviceaccount.com"
$PUBSUB_PUSH_SA = "rateguard-pubsub-push@rateguard-ai.iam.gserviceaccount.com"
$BACKEND_IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/rateguard/rateguard-api:latest"
$FRONTEND_IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/rateguard/rateguard-web:latest"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory=$true)][string]$StageName,
        [Parameter(Mandatory=$true)][scriptblock]$ScriptBlock
    )
    & $ScriptBlock
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nError: $StageName failed (exit code $LASTEXITCODE). Deployment stopped." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host "========================================================"
Write-Host "   RateGuard AI -- Production Cloud Deployment          "
Write-Host "   Project: $PROJECT_ID | Region: $REGION"
Write-Host "========================================================"

Invoke-CheckedCommand -StageName "gcloud config" -ScriptBlock {
    gcloud config set project "$PROJECT_ID"
}

# 0. Resolve Project Number & Pub/Sub Service Agent
$PROJECT_NUMBER = ""
Invoke-CheckedCommand -StageName "Resolving project number" -ScriptBlock {
    $global:PROJECT_NUMBER_VAL = (gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)").Trim()
}
$PROJECT_NUMBER = $global:PROJECT_NUMBER_VAL
$PUBSUB_SERVICE_AGENT = "service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
Write-Host "   Project Number: $PROJECT_NUMBER"
Write-Host "   Pub/Sub Service Agent: $PUBSUB_SERVICE_AGENT"

# 1. Idempotent Service Account Setup & Least-Privilege IAM Roles
Write-Host "`n1. Verifying and configuring Service Accounts..."

# Runtime SA
$saExists = $null
try {
  $saExists = gcloud iam service-accounts describe "$RUNTIME_SA" --format="value(email)" 2>$null
} catch {}

if (-not $saExists) {
  Write-Host "   Creating Runtime Service Account ($RUNTIME_SA)..."
  Invoke-CheckedCommand -StageName "Creating Runtime SA" -ScriptBlock {
    gcloud iam service-accounts create rateguard-runtime `
      --display-name="RateGuard Runtime Service Account"
  }
} else {
  Write-Host "   Runtime Service Account exists: $RUNTIME_SA"
}

# Grant least-privilege roles to Runtime SA
$RUNTIME_ROLES = @(
  "roles/aiplatform.user",       # Vertex AI / Gemini 3.7 Flash invocation
  "roles/datastore.user",        # Firestore read/write
  "roles/bigquery.dataEditor",   # BigQuery results table write
  "roles/bigquery.jobUser",      # BigQuery query execution
  "roles/storage.objectUser",    # Cloud Storage artifact access
  "roles/pubsub.publisher"      # Pub/Sub message publishing
)

foreach ($role in $RUNTIME_ROLES) {
  Write-Host "   Granting $role to Runtime SA..."
  Invoke-CheckedCommand -StageName "Granting $role to Runtime SA" -ScriptBlock {
    gcloud projects add-iam-policy-binding "$PROJECT_ID" `
      --member="serviceAccount:$RUNTIME_SA" `
      --role="$role" | Out-Null
  }
}

# Pub/Sub Push SA
$pushSaExists = $null
try {
  $pushSaExists = gcloud iam service-accounts describe "$PUBSUB_PUSH_SA" --format="value(email)" 2>$null
} catch {}

if (-not $pushSaExists) {
  Write-Host "   Creating Pub/Sub Push Service Account ($PUBSUB_PUSH_SA)..."
  Invoke-CheckedCommand -StageName "Creating Pub/Sub Push SA" -ScriptBlock {
    gcloud iam service-accounts create rateguard-pubsub-push `
      --display-name="RateGuard PubSub Push Invoker SA"
  }
} else {
  Write-Host "   Pub/Sub Push Service Account exists: $PUBSUB_PUSH_SA"
}

# Grant roles/iam.serviceAccountTokenCreator to Google-managed Pub/Sub Service Agent on Push SA
Write-Host "   Granting roles/iam.serviceAccountTokenCreator to Pub/Sub Service Agent on Push SA..."
Invoke-CheckedCommand -StageName "Granting Token Creator to Pub/Sub Service Agent" -ScriptBlock {
  gcloud iam service-accounts add-iam-policy-binding "$PUBSUB_PUSH_SA" `
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" `
    --role="roles/iam.serviceAccountTokenCreator" | Out-Null
}

# Note on actAs requirement for deployer
Write-Host "   [IAM Verification] Deployer identity must hold roles/iam.serviceAccountUser on $PUBSUB_PUSH_SA to execute modify-push-config."

# 2. Build Backend Docker Container via Cloud Build (Repository Root Context)
Write-Host "`n2. Building Backend Docker Container via Cloud Build..."
Invoke-CheckedCommand -StageName "Backend Cloud Build" -ScriptBlock {
  gcloud builds submit . `
    --config=./backend/cloudbuild.yaml
}

# 3. Deploy Public API Cloud Run Service (rateguard-api)
Write-Host "`n3. Deploying Public API Service (rateguard-api)..."
Invoke-CheckedCommand -StageName "rateguard-api deployment" -ScriptBlock {
  gcloud run deploy rateguard-api `
    --image "$BACKEND_IMAGE" `
    --region "$REGION" `
    --platform managed `
    --allow-unauthenticated `
    --service-account "$RUNTIME_SA" `
    --memory=512Mi `
    --env-vars-file=./infrastructure/runtime-env.yaml
}

$API_URL = ""
Invoke-CheckedCommand -StageName "Describing rateguard-api URL" -ScriptBlock {
  $global:API_URL_VAL = (gcloud run services describe rateguard-api --region "$REGION" --format "value(status.url)").Trim()
}
$API_URL = $global:API_URL_VAL
Write-Host "   Public API URL: $API_URL"

# 4. Deploy Private Worker Cloud Run Service (rateguard-worker) using SAME Backend Image
Write-Host "`n4. Deploying Private Worker Service (rateguard-worker)..."
Invoke-CheckedCommand -StageName "rateguard-worker deployment" -ScriptBlock {
  gcloud run deploy rateguard-worker `
    --image "$BACKEND_IMAGE" `
    --region "$REGION" `
    --platform managed `
    --no-allow-unauthenticated `
    --service-account "$RUNTIME_SA" `
    --memory=1Gi `
    --env-vars-file=./infrastructure/runtime-env.yaml
}

$WORKER_URL = ""
Invoke-CheckedCommand -StageName "Describing rateguard-worker URL" -ScriptBlock {
  $global:WORKER_URL_VAL = (gcloud run services describe rateguard-worker --region "$REGION" --format "value(status.url)").Trim()
}
$WORKER_URL = $global:WORKER_URL_VAL
Write-Host "   Private Worker URL: $WORKER_URL"

# Grant roles/run.invoker to Pub/Sub Push SA on rateguard-worker
Write-Host "   Granting roles/run.invoker to Pub/Sub Push SA on rateguard-worker..."
Invoke-CheckedCommand -StageName "Granting run.invoker on rateguard-worker" -ScriptBlock {
  gcloud run services add-iam-policy-binding rateguard-worker `
    --region "$REGION" `
    --member="serviceAccount:$PUBSUB_PUSH_SA" `
    --role="roles/run.invoker" | Out-Null
}

# 4b. Idempotent Pub/Sub Topic, Subscription, and Dead-Letter Queue Setup
#
# Ack deadline / worker timeout alignment: rateguard-worker's Cloud Run
# request timeout is 300s (this script does not change that in this pass).
# Pub/Sub's push-subscription ack deadline caps at 600s -- the hard platform
# maximum -- which is what ACK_DEADLINE_SECONDS is set to below specifically
# so it always exceeds the worker's request timeout with a safe buffer (a
# full 300s / 2x margin today). A mission whose synchronous processing
# legitimately takes longer than the ack deadline would otherwise be
# redelivered by Pub/Sub mid-flight even though the worker is still correctly
# working on it. Duplicate delivery is still possible regardless of this
# alignment (Pub/Sub is at-least-once by design) -- that is exactly why the
# MissionExecutionService atomic lease/idempotency protection
# (backend/app/services/mission_execution_service.py) must remain in place;
# it is the real correctness guarantee, this timing alignment only reduces
# how often it gets exercised.
$ACK_DEADLINE_SECONDS = 600
$MIN_RETRY_BACKOFF_SECONDS = 10
$MAX_RETRY_BACKOFF_SECONDS = 600
$MAX_DELIVERY_ATTEMPTS = 5
$DLQ_TOPIC = "assurance-runs-dlq"
$DLQ_INSPECTION_SUBSCRIPTION = "assurance-runs-dlq-inspect"

Write-Host "`n4b. Verifying Pub/Sub topic/subscription/dead-letter-queue configuration..."

$topicExists = $null
try { $topicExists = gcloud pubsub topics describe assurance-runs --format="value(name)" 2>$null } catch {}
if (-not $topicExists) {
  Write-Host "   Creating Pub/Sub topic 'assurance-runs'..."
  Invoke-CheckedCommand -StageName "Creating topic assurance-runs" -ScriptBlock {
    gcloud pubsub topics create assurance-runs
  }
} else {
  Write-Host "   Pub/Sub topic 'assurance-runs' exists."
}

$dlqTopicExists = $null
try { $dlqTopicExists = gcloud pubsub topics describe "$DLQ_TOPIC" --format="value(name)" 2>$null } catch {}
if (-not $dlqTopicExists) {
  Write-Host "   Creating dead-letter topic '$DLQ_TOPIC'..."
  Invoke-CheckedCommand -StageName "Creating dead-letter topic" -ScriptBlock {
    gcloud pubsub topics create "$DLQ_TOPIC"
  }
} else {
  Write-Host "   Dead-letter topic '$DLQ_TOPIC' exists."
}

$subExists = $null
try { $subExists = gcloud pubsub subscriptions describe assurance-worker --format="value(name)" 2>$null } catch {}
if (-not $subExists) {
  Write-Host "   Creating Pub/Sub subscription 'assurance-worker' (push endpoint configured in step 5)..."
  Invoke-CheckedCommand -StageName "Creating subscription assurance-worker" -ScriptBlock {
    gcloud pubsub subscriptions create assurance-worker `
      --topic=assurance-runs `
      --ack-deadline="$ACK_DEADLINE_SECONDS" `
      --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" `
      --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" `
      --dead-letter-topic="$DLQ_TOPIC" `
      --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
  }
} else {
  Write-Host "   Pub/Sub subscription 'assurance-worker' exists; reapplying timing/retry/dead-letter configuration (idempotent update)..."
  Invoke-CheckedCommand -StageName "Updating subscription assurance-worker" -ScriptBlock {
    gcloud pubsub subscriptions update assurance-worker `
      --ack-deadline="$ACK_DEADLINE_SECONDS" `
      --min-retry-delay="${MIN_RETRY_BACKOFF_SECONDS}s" `
      --max-retry-delay="${MAX_RETRY_BACKOFF_SECONDS}s" `
      --dead-letter-topic="$DLQ_TOPIC" `
      --max-delivery-attempts="$MAX_DELIVERY_ATTEMPTS"
  }
}

# Pull-based inspection subscription on the DLQ topic so a poison message can
# be manually pulled and examined rather than only ever silently retained.
# Not consumed by any running service.
$dlqSubExists = $null
try { $dlqSubExists = gcloud pubsub subscriptions describe "$DLQ_INSPECTION_SUBSCRIPTION" --format="value(name)" 2>$null } catch {}
if (-not $dlqSubExists) {
  Write-Host "   Creating dead-letter inspection subscription '$DLQ_INSPECTION_SUBSCRIPTION'..."
  Invoke-CheckedCommand -StageName "Creating dead-letter inspection subscription" -ScriptBlock {
    gcloud pubsub subscriptions create "$DLQ_INSPECTION_SUBSCRIPTION" --topic="$DLQ_TOPIC"
  }
} else {
  Write-Host "   Dead-letter inspection subscription '$DLQ_INSPECTION_SUBSCRIPTION' exists."
}

# Narrowly-scoped grants required for Pub/Sub to forward undeliverable
# messages to the dead-letter topic: the Pub/Sub service agent needs
# publisher rights on the DLQ topic specifically (not project-wide), and
# subscriber rights on the source subscription specifically. Both
# add-iam-policy-binding calls are naturally idempotent (re-adding an
# existing binding is a no-op, not an error).
Write-Host "   Granting narrowly-scoped dead-letter forwarding permissions to Pub/Sub Service Agent..."
Invoke-CheckedCommand -StageName "Granting publisher on DLQ topic" -ScriptBlock {
  gcloud pubsub topics add-iam-policy-binding "$DLQ_TOPIC" `
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" `
    --role="roles/pubsub.publisher" | Out-Null
}
Invoke-CheckedCommand -StageName "Granting subscriber on assurance-worker for DLQ forwarding" -ScriptBlock {
  gcloud pubsub subscriptions add-iam-policy-binding assurance-worker `
    --member="serviceAccount:$PUBSUB_SERVICE_AGENT" `
    --role="roles/pubsub.subscriber" | Out-Null
}

# 5. Configure Pub/Sub Subscription Push to Private Worker Endpoint
Write-Host "`n5. Configuring Pub/Sub Subscription ('assurance-worker') Push to Private Worker..."
Invoke-CheckedCommand -StageName "Pub/Sub push configuration" -ScriptBlock {
  gcloud pubsub subscriptions modify-push-config assurance-worker `
    --push-endpoint="${WORKER_URL}/internal/pubsub/assurance" `
    --push-auth-service-account="$PUBSUB_PUSH_SA"
}

# 6. Build and Deploy Next.js Frontend with Embedded Build-Time API URL (rateguard-web)
Write-Host "`n6. Building and Deploying Frontend Web Dashboard (rateguard-web)..."
Invoke-CheckedCommand -StageName "Frontend Cloud Build" -ScriptBlock {
  gcloud builds submit ./frontend `
    --config=./frontend/cloudbuild.yaml `
    --substitutions=_NEXT_PUBLIC_RATEGUARD_API_URL="$API_URL"
}

Invoke-CheckedCommand -StageName "rateguard-web deployment" -ScriptBlock {
  gcloud run deploy rateguard-web `
    --image "$FRONTEND_IMAGE" `
    --region "$REGION" `
    --platform managed `
    --allow-unauthenticated
}

$WEB_URL = ""
Invoke-CheckedCommand -StageName "Describing rateguard-web URL" -ScriptBlock {
  $global:WEB_URL_VAL = (gcloud run services describe rateguard-web --region "$REGION" --format "value(status.url)").Trim()
}
$WEB_URL = $global:WEB_URL_VAL
Write-Host "   Public Web Dashboard URL: $WEB_URL"

# 7. Update Backend CORS Config for Deployed Web Origin using env file
Write-Host "`n7. Updating Backend CORS for Deployed Web Origin..."
$TEMP_ENV = "infrastructure/.deploy-env.yaml"
Copy-Item "infrastructure/runtime-env.yaml" "$TEMP_ENV" -Force
$CORS_LINE = "RATEGUARD_CORS_ORIGINS: '[`"http://localhost:3000`",`"$WEB_URL`"]'"
Add-Content -Path "$TEMP_ENV" -Value $CORS_LINE

Invoke-CheckedCommand -StageName "CORS update" -ScriptBlock {
  gcloud run deploy rateguard-api `
    --image "$BACKEND_IMAGE" `
    --region "$REGION" `
    --platform managed `
    --allow-unauthenticated `
    --service-account "$RUNTIME_SA" `
    --memory=512Mi `
    --env-vars-file="$TEMP_ENV"
}

Remove-Item "$TEMP_ENV" -Force -ErrorAction SilentlyContinue

Write-Host "`n========================================================"
Write-Host "DEPLOYMENT COMPLETED SUCCESSFULLY!"
Write-Host "Public API Service:      $API_URL"
Write-Host "Private Worker Service:  $WORKER_URL"
Write-Host "Public Web Dashboard:    $WEB_URL"
Write-Host "Pub/Sub Push Endpoint:   ${WORKER_URL}/internal/pubsub/assurance"
Write-Host "Backend Image:           $BACKEND_IMAGE"
Write-Host "Frontend Image:          $FRONTEND_IMAGE"
Write-Host "Gemini Model:            gemini-3.7-flash"
Write-Host "========================================================"
