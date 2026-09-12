# Assurance Mission API Specification

## 1. Overview

RateGuard's REST API exposes endpoints for compiling pricing sources, running assurance missions, and retrieving results and evidence lineage. The mission lifecycle is asynchronous: `POST /api/v1/missions` returns `202 Accepted` immediately and enqueues execution onto Pub/Sub; the deployed worker (`app/messaging/worker.py` → `MissionExecutionService`) processes it via `AssuranceSupervisor.run_mission`.

---

## 2. Source Endpoints (`app/api/sources.py`)

### `POST /api/v1/sources`
Uploads and registers a pricing source (`.json` only today — see the README's [Supported Source Format](../../README.md#supported-source-format-json-schema)). Returns a `source_id`.

### `POST /api/v1/sources/{source_id}/compile`
Compiles a registered source into a canonical IPIR package via `structured_json_adapter`. Returns a `compilation_receipt` (product, jurisdiction, effective period, and exact table/rule/output counts parsed) and, on a schema violation, a structured `422` naming the offending field.

---

## 3. Mission Endpoints (`app/api/missions.py`)

### `POST /api/v1/missions`
Creates and enqueues an assurance mission (`RELEASE_CONFORMANCE` or `EQUIVALENCE` mode). `202 Accepted`, no hidden defaults — `source_a`/`source_b` must be explicit.

### `GET /api/v1/missions`
Lists missions with pagination and status/mode/decision filters.

### `GET /api/v1/missions/{mission_id}`
Full mission state and the `AssuranceResultV2` result payload.

### `GET /api/v1/missions/{mission_id}/evidence`
Sanitized, read-only Gemini decision evidence (model id, invocation id, schema validity, latency, token counts — never prompts, raw output, or rationale text).

### `POST /api/v1/missions/{mission_id}/alignment-options`
Equivalence-mode only. Generates a directional alignment patch **on demand**, after the caller picks `{"reference": "A"}` or `{"reference": "B"}` — see [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md#symmetric-equivalence-mode). Purely deterministic; makes no Gemini call.

### `POST /api/v1/missions/{mission_id}/cancel`, `/retry`, `/archive`, `DELETE /api/v1/missions/{mission_id}`
Mission lifecycle actions, gated by `app/services/mission_transitions.py`'s eligibility rules (e.g. a `COMPLETED` audit record cannot be deleted, only archived).

---

## 4. Generic Run-Store Read Endpoints (`app/api/assurance.py`)

### `GET /api/v1/system/info`
Runtime AI model/framework info (`"agent_framework": "Google GenAI SDK"`).

### `GET /api/v1/assurance/runs/{run_id}/events`, `GET /api/v1/assurance/runs/{run_id}/evidence`
Generic reads over the run store, addressable by any `run_id` (Mission V2 `MIS-*` included) — these back the Mission detail page's Agent Action Timeline and Evidence Lineage tabs. Not part of the retired legacy pipeline; see [AGENT_ARCHITECTURE.md §4](./AGENT_ARCHITECTURE.md#4-legacy-pipeline-removed) for what *was* removed alongside them.
