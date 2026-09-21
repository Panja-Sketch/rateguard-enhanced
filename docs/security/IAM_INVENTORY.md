# IAM inventory and least-privilege cleanup (Prompt 8)

Project `rateguard-enhanced` (316435199506). Snapshot taken 2026-09-20 before any change.
Google-managed service agents (`service-316435199506@…`, `…@cloudbuild.gserviceaccount.com`) are
**not** modified. Every change was applied incrementally with a focused live check; rollback
commands are listed per row.

## Runtime identities

| Service | Runtime service account | Invocation |
|---|---|---|
| `rateguard-web` | `rateguard-web-sa` | public |
| `rateguard-api` | `rateguard-api-sa` | public, Firebase-authenticated in-app |
| `rateguard-worker` | `rateguard-worker-sa` | private; invoker = `rateguard-worker-sa` only (Pub/Sub OIDC push identity) |
| `rateguard-rating-engine` | `rateguard-rating-engine-sa` (zero data roles) | private; invoker = `rateguard-api-sa`, `rateguard-worker-sa` |

## Matrix: before → after

| Principal | Role (before) | Scope (before) | Actual need | After | Rollback |
|---|---|---|---|---|---|
| api-sa | `roles/datastore.user` | project | Firestore runs, evidence, rate limits | **kept** | – |
| api-sa | `roles/pubsub.publisher` | project | publish mission jobs to `assurance-runs` | topic-scoped on `assurance-runs` | `gcloud projects add-iam-policy-binding rateguard-enhanced --member=serviceAccount:rateguard-api-sa@rateguard-enhanced.iam.gserviceaccount.com --role=roles/pubsub.publisher` |
| api-sa | `roles/storage.objectAdmin` | project | source/evidence objects in `rateguard-enhanced-artifacts` | `roles/storage.objectUser` on the bucket | `… projects add-iam-policy-binding … --role=roles/storage.objectAdmin` |
| api-sa | `roles/firebase.sdkAdminServiceAgent` | project | **none** — service-agent role mis-assigned to a runtime SA; ID-token verification via ADC needs no IAM | **removed** | `… add-iam-policy-binding … --role=roles/firebase.sdkAdminServiceAgent` |
| api-sa | `roles/secretmanager.secretAccessor` | project | **none** — no secret is mounted or read | **removed** | `… --role=roles/secretmanager.secretAccessor` |
| api-sa | `roles/run.invoker` | rating-engine service | connector `/quote` (ID token) | **kept** (resource-scoped) | – |
| worker-sa | `roles/datastore.user` | project | Firestore | **kept** | – |
| worker-sa | `roles/aiplatform.user` | project | Vertex AI Gemini via ADC | **kept** | – |
| worker-sa | `roles/bigquery.jobUser` | project | BigQuery jobs (portfolio dataset tooling) | **kept** | – |
| worker-sa | `roles/bigquery.dataEditor` | project | reads only (no runtime write path) | `roles/bigquery.dataViewer` on dataset `rateguard_portfolio` | `… --role=roles/bigquery.dataEditor` |
| worker-sa | `roles/storage.objectAdmin` | project | source/evidence objects | `roles/storage.objectUser` on the bucket | as api-sa |
| worker-sa | `roles/pubsub.subscriber` | project | **none** — push subscription; the worker never pulls | **removed** | `… --role=roles/pubsub.subscriber` |
| worker-sa | – | – | publish impact batch references | `roles/pubsub.publisher` on topic `impact-batches` (new) | remove binding on the topic |
| worker-sa | `roles/firebase.sdkAdminServiceAgent`, `roles/secretmanager.secretAccessor` | project | none | **removed** | as api-sa |
| worker-sa | `roles/run.invoker` | worker service + rating-engine | Pub/Sub OIDC push identity; connector | **kept** (resource-scoped) | – |
| web-sa | `roles/run.invoker` | project | none — the web tier is public and never calls a private service | **removed** | `… add-iam-policy-binding … web-sa … --role=roles/run.invoker` |
| rating-engine-sa | – | – | none | **zero roles (unchanged)** | – |
| firebase-adminsdk-fbsvc | `sdkAdminServiceAgent`, `serviceAccountTokenCreator` | project | Firebase-managed | unchanged (Google/Firebase-managed) | – |
| default compute SA | `roles/editor` | project | not used by any RateGuard service (each runs as a dedicated SA) | **unchanged, flagged** — Cloud Build may still rely on it; see limitations | – |

Pub/Sub push identity: `rateguard-worker-sa` holds `run.invoker` on **only** the worker service.
Pub/Sub service agent bindings (publisher on DLQ topics, subscriber on subscriptions) are
resource-scoped and Google-managed in effect.

## Legacy credentials

| Item | Finding | Action |
|---|---|---|
| Secret `FIREBASE_ADMIN_KEY` (versions 1, 2) | payload `private_key_id` `1bb0ac90b2a9d5a10b54fcedd4220f26bd9d739a` matches the USER_MANAGED key of `firebase-adminsdk-fbsvc@…`; no Cloud Run revision (current or rollback) mounts it; app uses ADC only | key **deleted**, secret versions **disabled** (container kept for audit) |
| Secret `GEMINI_API_KEY` (version 1) | value is an 11-character placeholder, not a credential; no matching API key resource exists in the project; Vertex AI runs on the worker identity | secret version **disabled**; nothing to revoke |
| API key `Browser key (auto created by Firebase)` (`a7734454-d5d9-41a8-a427-df78f695a89f`) | **public Firebase web key required by browser sign-in** | **kept** |

Rollback limitation: a deleted service-account key cannot be restored (a new key would have to be
minted). Credential revocation is therefore **not** part of application rollback; the application
never depended on it.
