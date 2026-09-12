# Asynchronous Assurance Workflow Architecture

## 1. Overview

The deployed Mission V2 pipeline is asynchronous-only: `POST /api/v1/missions` always returns `202 Accepted` and enqueues execution onto Cloud Pub/Sub — there is no synchronous in-process execution mode in the public API.

```text
Source Artifacts ──► Cloud Storage
                          │
                          ▼
              StructuredJSONPricingAdapter ──► IPIR
                                       │
                                       ▼
                              POST /api/v1/missions
                                       │
                                       ▼  202 Accepted
                              Pub/Sub AssuranceJob
                                       │
                                       ▼
                         rateguard-worker (private Cloud Run)
                        AssuranceSupervisor.run_mission()
                                       │
                       ┌───────────────┼───────────────┐
                       ▼               ▼               ▼
                   Firestore       BigQuery      Cloud Storage
                  (State/Logs)    (Analytics)      (Artifacts)
```

## 2. API Endpoints

See [ASSURANCE_API.md](./ASSURANCE_API.md) for the full, current endpoint list. The core async flow:

- `POST /api/v1/missions`: Enqueues a mission, `202 Accepted` with `mission_id`.
- `GET /api/v1/missions/{mission_id}`: Full mission state, workflow stage, decision, and the `AssuranceResultV2` result once complete.
- `GET /api/v1/assurance/runs/{mission_id}/events`, `GET /api/v1/assurance/runs/{mission_id}/evidence`: Audit event timeline and evidence lineage — generic run-store reads, addressable by the mission's `MIS-*` id.

## 3. Configuration

- `RATEGUARD_PUBSUB_TOPIC=assurance-runs`
- `RATEGUARD_PUBSUB_SUBSCRIPTION=assurance-worker`
- `RATEGUARD_RUN_STORE=firestore` (production; `memory` for local/test)
