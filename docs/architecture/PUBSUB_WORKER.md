# Pub/Sub Assurance Worker Architecture

## 1. Overview
The `AssuranceWorker` processes asynchronous `AssuranceJob` messages published via Cloud Pub/Sub.

## 2. Idempotency & Safety
- **State Check:** When a job is received, the worker checks the run status in Firestore (`BaseRunStore`).
- **Skip Completed Runs:** If `status == COMPLETED`, execution is skipped immediately to prevent duplicate work.
- **Atomic Event Logging:** Stages are logged as structured audit events (`CREATED` -> `QUEUED` -> `PROCESSING` -> `COMPILING_SOURCES` -> `COMPARING` -> `PLANNING_TESTS` -> `EXECUTING_TESTS` -> `ANALYZING_PORTFOLIO` -> `COMPLETED` / `FAILED`).

## 3. Worker Endpoints
- `POST /internal/pubsub/assurance`: Decodes base64 Pub/Sub push envelope data and executes `AssuranceWorker.process_job()`.
- Secured via Cloud Run IAM authentication in production deployments.

