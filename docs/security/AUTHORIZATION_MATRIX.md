# RateGuard — Authentication, Authorization and Tenant Scoping

**Status:** implemented and tested (see `docs/implementation/STATUS.md`, "Session 5" and "Session 6").
**Governs:** locked source of truth §4.1.A, §12.2, §13, §14, §15.2, acceptance scenario A12.
**Machine-checked:** `backend/tests/auth/test_route_access_matrix.py` fails if a route is added, removed, or has different roles than this table.

## 1. How a request is authorized

1. The client sends `Authorization: Bearer <Firebase ID token>`. Nothing else is read: not the body, query string, custom headers, or the token's custom claims.
2. `app.auth.verifier.FirebaseTokenVerifier` verifies signature, audience (= Firebase project), issuer (`https://securetoken.google.com/<project>`), expiry and subject using the Firebase Admin SDK with **Application Default Credentials** (no service-account JSON, no `FIREBASE_ADMIN_KEY`). Failures produce a fixed, safe 401.
3. The verified `uid` is looked up in the server-controlled Firestore document `users/{uid}` (`role`, `tenant_id`, `disabled`). No document, malformed document, unknown role, or `disabled: true` → 403. Directory or verifier outage → 503 (fail closed).
4. The route's `require_roles(...)` dependency checks the role. Wrong role → 403 `INSUFFICIENT_ROLE`.
5. Data access is scoped by the user's `tenant_id` (§3).

Response shapes (no token contents, claim values, library text or stack trace):

| Situation | Status | `detail.code` |
|---|---|---|
| No / non-Bearer header | 401 | `AUTHENTICATION_REQUIRED` / `MALFORMED_AUTHORIZATION` |
| Invalid, expired, wrong-audience, wrong-issuer, bad-signature token | 401 | `INVALID_TOKEN` |
| Valid token, no `users/{uid}` record | 403 | `ACCOUNT_NOT_PROVISIONED` |
| Disabled user | 403 | `ACCOUNT_DISABLED` |
| Role not allowed | 403 | `INSUFFICIENT_ROLE` |
| Firebase cert fetch / directory failure | 503 | `AUTH_SERVICE_UNAVAILABLE` |
| Cross-tenant or missing record | 404 | (identical to a genuinely missing record) |

## 2. Route-access matrix

`A` = ADMIN, `R` = RELEASE_OWNER, `C` = CONSUMER_REVIEWER, `V` = VIEWER. **Only listed roles are allowed**; ADMIN is not implicit — it is listed wherever it applies.

### Public (no user authentication)

| Route | Notes |
|---|---|
| `GET /`, `GET /health`, `GET /health/live`, `GET /health/ready` | Liveness/readiness only. Bodies contain no project id, service-account name, topic/subscription name, exception text or configuration (tested). Only served by the `api` and `all` roles as applicable; `/health*` on every role. |

### Internal worker (NOT Firebase user authentication) — see §4

| Route | Protection |
|---|---|
| `POST /internal/pubsub/assurance` | Private Cloud Run IAM (`--no-allow-unauthenticated`) + Pub/Sub push OIDC identity. Served **only** by the `worker` (and local `all`) role; the public `api` service returns 404 for it. |

### Authenticated API (`/api/v1`)

| Route | A | R | C | V | Tenant-scoped | Notes |
|---|:-:|:-:|:-:|:-:|:-:|---|
| `GET /me` | ✔ | ✔ | ✔ | ✔ | — | Server's view of caller; used only for navigation hints |
| `GET /system/info` | ✔ | ✔ | ✔ | ✔ | — | Model/location only; deployment topology (project, region, store) only for A |
| `GET /system/status` | ✔ | | | | — | Operational diagnostics |
| `GET /connectors` | ✔ | ✔ | ✔ | ✔ | — | Safe metadata only: never URL, header name, credential or token variable |
| `POST /connectors/{id}/test` | ✔ | | | | — | Golden-case health test; id must be in the admin registry; no URL input exists |
| `POST /sources` (upload) | ✔ | ✔ | | | stamps tenant | Tenant/uploader come from the server record |
| `POST /sources/{id}/compile` | ✔ | ✔ | | | ✔ | |
| `GET /sources/{id}` | ✔ | ✔ | ✔ | ✔ | ✔ | Metadata; never `storage_uri` |
| `GET /sources/{id}/artifacts/{artifact_id}` | ✔ | ✔ | | | ✔ | **Source download**; only `{id}` and `IPIR-{id}` are servable |
| `POST /missions` | ✔ | ✔ | | | stamps tenant | Referenced uploaded sources must belong to the caller's tenant |
| `GET /missions` | ✔ | ✔ | ✔ | ✔ | ✔ | Filters out other tenants and legacy records |
| `GET /missions/{id}` | ✔ | ✔ | ✔ | ✔ | ✔ | |
| `GET /missions/{id}/evidence` | ✔ | ✔ | ✔ | ✔ | ✔ | Sanitized Gemini-invocation summary |
| `GET /missions/{id}/connector-evidence` | ✔ | ✔ | ✔ | ✔ | ✔ | Hashes/status only |
| `GET /missions/{id}/evidence/download` | ✔ | ✔ | ✔ | | ✔ | **Evidence download** (JSON bundle + SHA-256); VIEWER may read summaries but not export |
| `GET /assurance/runs/{id}/events`, `.../evidence` | ✔ | ✔ | ✔ | ✔ | ✔ | Used by the mission page timeline / lineage tabs |
| `POST /missions/{id}/alignment-options` | ✔ | ✔ | | | ✔ | |
| `POST /missions/{id}/cancel` | ✔ | ✔ | | | ✔ | |
| `POST /missions/{id}/retry` | ✔ | ✔ | | | ✔ | |
| `POST /missions/{id}/archive` | ✔ | ✔ | | | ✔ | |
| `DELETE /missions/{id}` | ✔ | | | | ✔ | Destructive; transition rules still apply |
| `POST /missions/{id}/explanations` | ✔ | ✔ | ✔ | | ✔ | Snapshots the mission's own draft; **no request body** |
| `GET /missions/{id}/explanations` | ✔ | ✔ | ✔ | ✔ | ✔ | |
| `POST /explanations/{id}/approve` | ✔ | | ✔ | | ✔ | Records reviewer uid + timestamp in the mission event log |
| `POST /explanations/{id}/reject` | ✔ | | ✔ | | ✔ | Body: `{ "reason": str }` only; unknown fields → 422 |

Locked-role intent mapping: **ADMIN** — connector administration/testing and all reads; **RELEASE_OWNER** — source upload/compile, mission creation/cancellation, mission/evidence reads; **CONSUMER_REVIEWER** — consumer-impact reads, explanation review (approve/reject), evidence reads/downloads; **VIEWER** — read-only.

## 3. Tenant scoping, storage isolation and the legacy-record policy

### 3.1 What carries the tenant
`tenant_id` is stamped **only** from the authenticated user's `users/{uid}` record onto: uploaded sources, missions (`AssuranceRunRecord.tenant_id` + `created_by`), the Pub/Sub job (`AssuranceJob.tenant_id`, log correlation), explanation records, evidence bundles, and every artifact path. A body/query/header/form field/filename carrying `tenant_id`, `role`, or `created_by` is ignored (tested).

### 3.2 Database-level queries (Firestore)
No endpoint fetches a collection and filters it in memory.

| Operation | Mechanism |
|---|---|
| List missions | `where tenant_id == <caller tenant> order_by created_at desc limit N` (composite index `tenant_id ASC, created_at DESC`). Other tenants' documents are never read into the process, so they cannot be returned or counted. |
| Read mission by id | Direct document read, then owner check; a foreign record returns the same 404 as a missing one. |
| Update / cancel / retry | `update_run_for_tenant`: Firestore **transaction** that re-reads the stored document, verifies ownership, and writes; the *stored* `tenant_id` is authoritative (a caller cannot move a record between tenants by editing its own copy). |
| Delete | `delete_run_for_tenant`: ownership verified **inside the transaction**, document deleted in that transaction, sub-collections removed only afterwards. |
| Explanations | `where tenant_id == …` on the sub-collection; approval/rejection re-verifies the mission's tenant. |
| Sources | Uploaded sources are addressed by tenant-prefixed object path (3.3), so ownership is structural. |
| Bounds | List `limit ≤ 200`, `offset ≤ 1000`, store ceiling `MAX_TENANT_LIST_RECORDS = 1000`; events/evidence/explanations sub-collection reads capped at 5000 documents. Status/mode/decision filters are applied to the (bounded, tenant-filtered) window. |

Tests: `backend/tests/storage/test_firestore_tenant_queries.py` runs against the **real Firestore emulator** and includes a spy proving other tenants' documents are never even parsed by a list query, plus cross-tenant read/update/delete/count/explanation cases and an API-level pass over the emulator.

### 3.3 Tenant-prefixed artifacts (locked doc 14.2)
```
tenants/{tenant_id}/sources/{source_id}/raw/{artifact_id}
tenants/{tenant_id}/sources/{source_id}/compiled/{artifact_id}
tenants/{tenant_id}/missions/{mission_id}/evidence/{artifact_id}
```
* Every artifact is addressed by an `ArtifactKey` whose components are validated against `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (no `.`, `/`, `\`, `%`, whitespace, NUL, non-ASCII) and whose `scope`/`kind` pair is a closed set. The tenant component is the authenticated user's tenant; nothing reads a request field or a filename. The original filename is sanitized metadata only and never part of a path (the local store previously wrote to `base_dir/<filename>`; that traversal risk is gone).
* Reads, exists, descriptors and deletes are all keyed by the **caller's** tenant, so another tenant's object is indistinguishable from a missing one and no post-fetch ownership check can be forgotten. The worker resolves compiled IPIR under the mission's tenant.
* Evidence bundles are stored at `…/missions/{mission_id}/evidence/EVB-<sha256[:32]>`; the hash covers mission content only (not exporter/time), so repeated downloads are idempotent.
* GCS object metadata carries tenant/scope/kind/sanitized filename so any Cloud Run process can read descriptors without per-process caches.
* Per-tenant separation is by object prefix, not by IAM; the bucket stays private with uniform bucket-level access.

### 3.4 Legacy (pre-tenant) records and objects: migration / hiding policy
* **Default: hidden from every tenant.** Legacy run documents (no `tenant_id`, or an explicit `null`) are excluded from lists, and direct reads/updates/deletes return 404. Legacy artifacts live at `artifacts/{artifact_id}`; a tenant key never falls back to that path.
* **Explicit opt-in:** `RATEGUARD_LEGACY_RECORD_TENANT_ID=<tenant>` on the API/worker. Then, **for that one tenant only**: legacy runs with an explicit `null` tenant appear in lists; legacy runs (with or without the field) are readable/updatable by id; legacy `raw`/`compiled` objects are readable at `artifacts/{artifact_id}`. Nothing is rewritten in storage; unsetting the variable hides them again.
* **Limit:** Firestore cannot query for a *missing* field. Legacy documents written before this change that lack `tenant_id` entirely therefore do **not** appear in lists even with the opt-in (they remain readable by id). To list them, run a one-off backfill that writes `tenant_id: null` (or the real tenant) on those documents. That is deliberately **not** included here: it modifies cloud data and needs a reviewed run.
* Demo records: assign them to `rateguard-demo` via the opt-in above, or leave them hidden; all new records are always stamped.

## 4. Pub/Sub worker boundary (separate from user authentication)

* `POST /internal/pubsub/assurance` deliberately has **no** Firebase dependency. It is reached only by the Pub/Sub push subscription, which authenticates with an OIDC token minted for the worker service account (`--push-auth-service-account`, audience = worker URL). Cloud Run IAM (`--no-allow-unauthenticated` + `roles/run.invoker` for that service account only) rejects everything else before application code runs.
* The public `api` service is started with `RATEGUARD_SERVICE_ROLE=api` and **does not register** this route (404); the `worker` service is started with `RATEGUARD_SERVICE_ROLE=worker` and registers **only** this route plus health (no `/api/v1/*`). Local development and tests use the default `all`.
* Tests: `backend/tests/auth/test_worker_boundary.py` (route works without a Firebase token; a Firebase token is irrelevant to it; `api` role has no such route; `worker` role has no business API) and `test_route_access_matrix.py` (worker/public routes have no role dependency).
* Residual: the application does not re-verify the Pub/Sub OIDC token in code; it relies on Cloud Run IAM. Adding in-app OIDC verification (audience + service-account email) is a possible hardening step, recorded in DECISIONS.md.

## 5. Web / API perimeter

* CORS: explicit origin allowlist from `RATEGUARD_CORS_ORIGINS`; startup fails on `*` or (in deployed environments) non-`https` non-local origins; methods `GET, POST, DELETE, OPTIONS`; headers `Authorization, Content-Type`; credentials **off** (bearer header, no cookies).
* Request size: bodies larger than `RATEGUARD_MAX_REQUEST_BYTES` (default 25 MiB) get a fixed 413 before buffering (declared or streamed). Workbook/JSON limits inside ingestion still apply.
* `FIREBASE_AUTH_EMULATOR_HOST` (which disables signature verification in the Admin SDK) fails startup outside development.
* `Retry-After` is exposed to the allowed web origin so the UI can show rate-limit waits.

## 6. Distributed rate limiting

Fixed-window counters per **tenant + authenticated uid + operation**, kept in Firestore (`rate_limits` collection) with a transaction per hit, so limits hold across any number of Cloud Run instances. In-memory limiting is used only for local development and tests. Counter documents contain tenant id, uid, operation, window start, count and `expires_at`: **no** tokens, IP addresses or payloads; the document id is a hash.

| Operation | Routes | Challenge default |
|---|---|---|
| `connector_test` | `POST /connectors/{id}/test` | **5 / hour** (strictest) |
| `mission_create` | `POST /missions`, `POST /missions/{id}/retry` | **10 / hour** |
| `source_upload` | `POST /sources` | 30 / hour |
| `source_compile` | `POST /sources/{id}/compile` | 30 / hour |
| `explanation_create` | `POST /missions/{id}/explanations` | 20 / hour |
| `evidence_download` | `GET /missions/{id}/evidence/download` | 30 / hour |
| `source_download` | `GET /sources/{id}/artifacts/{artifact_id}` | 60 / hour |

* Configure with `RATEGUARD_RATE_LIMITS='{"mission_create":"10/3600"}'` (`limit/window_seconds`, limit 1-10000, window 1-86400 s; unknown operations rejected at startup). `RATEGUARD_RATE_LIMIT_ENABLED=false` is rejected at startup in `candidate`/`staging`/`production`.
* The limit dependency runs **after** authentication and the role check, so anonymous and forbidden requests never consume quota.
* Over the limit: `429` with `Retry-After` (seconds until the window resets) and a fixed body `{"detail":{"code":"RATE_LIMITED",…}}`.
* **Fails closed:** a corrupt counter document denies the action for the rest of that window (the next window uses a fresh document); a Firestore failure returns `503 RATE_LIMIT_UNAVAILABLE` (`Retry-After: 5`) rather than allowing the action. Hot-counter contention is retried with jitter a bounded number of times before failing closed.
* **Expiry / cleanup:** every counter has `expires_at` = window end + 1 h. Enable the Firestore TTL policy on `rate_limits.expires_at` (declared in `infrastructure/firestore.indexes.json`; not applied by any script here). `FirestoreRateLimiter.purge_expired()` is the manual equivalent and is tested.
* Fixed windows allow a burst of up to 2x the limit across a window boundary, acceptable for these cost/abuse guards.

## 7. Firestore rules and indexes (not published in this change)

The browser never talks to Firestore (all traffic goes through the API), so the client rule set is **deny-all** (`infrastructure/firestore.rules`); the Admin SDK / server libraries used by the API and worker bypass rules. `infrastructure/firestore-rules-tests` runs the emulator suite: anonymous, signed-in viewer, signed-in "admin" with admin claims, and a signed-in other-tenant client cannot read, write, update, delete, create, list, query, or collection-group-query any collection, including a self-provisioned `users/{uid}` record. A negative control with a permissive rule set makes 43 of those tests fail, proving they detect leaks.

Publishing steps (**manual, reviewed, not performed**): from `infrastructure/`, `firebase deploy --only firestore:rules,firestore:indexes --project rateguard-enhanced` (composite index `assurance_runs(tenant_id ASC, created_at DESC)` plus the `_staging` collection, and the TTL on `rate_limits.expires_at`). Build the index **before** exposing the candidate: list queries fail with `FAILED_PRECONDITION` until it is `READY`.
