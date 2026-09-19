# RateGuard AI — Deployed Acceptance Test Guide

This document provides step-by-step instructions to validate a live deployment of **RateGuard AI**.

---

## TEST 1 — CLEAN RELEASE (`RELEASE_CONFORMANCE` → `PASS`)

1. Open **Start Assurance Mission**.
2. Select **Release Conformance** mode.
3. Select Source A: `AZ_HO3_2026_09` (Authoritative Filing Intent).
4. Select Source B: `AZ_HO3_2026_09_CLEAN` (Clean Control Implementation).
5. Click **Execute Assurance Mission**.

### Expected Verification:
- `semantic_analysis.status` = `SUCCEEDED` (0 material differences).
- `experiments.match_count` = 5/5.
- `blast_radius.absolute_financial_exposure` = `$0.00`.
- `release_decision.status` = `PASS`.

---

## TEST 2 — DEFECTIVE RELEASE (`RELEASE_CONFORMANCE` → `BLOCK_DEPLOYMENT`)

1. Open **Start Assurance Mission**.
2. Select **Release Conformance** mode.
3. Select Source A: `AZ_HO3_2026_09`.
4. Select Source B: `AZ_HO3_2026_09_DEFECTIVE`.
5. Click **Execute Assurance Mission**.

### Expected Verification:
- Material semantic diffs identified (Roof factor 1.35 vs 1.25, effective date drift, fee sequence swap).
- Targeted boundary experiments reproduced calculation mismatches.
- First divergent node identified with root cause finding.
- 50K Portfolio Blast Radius computed at runtime:
  - Exposure: `$868,974.18` gross absolute variance.
  - Leakage: `-$588,742.42` signed net variance.
- Remediation proposal generated and revalidation executed.
- `release_decision.status` = `BLOCK_DEPLOYMENT`.

---

## TEST 3 — SYMMETRIC EQUIVALENCE (`EQUIVALENCE` → `PASS`, both directions)

1. Open **Start Assurance Mission**.
2. Select **Equivalence** mode.
3. Select Source A: `AZ_HO3_2026_09`. Select Source B: `AZ_HO3_2026_09_CLEAN`.
4. Click **Execute Assurance Mission** and confirm `release_decision.status` = `PASS`.
5. Repeat with Source A and Source B swapped (B→A) and confirm the decision is identical.

### Expected Verification:
- Both the A→B and B→A missions report `PASS` with 0 material differences.
- Source labels in the UI read neutrally as "Source A" / "Source B" (neither side is presented as authoritative).

---

## TEST 4 — INVALID INPUT REJECTION

1. Attempt to submit mission with missing product or URL `http://insecure-remote.com`.
2. Verify field-level validation prevents submission with inline error messages.

---

## TEST 5 — HISTORY ARCHIVE & PROTECTED DELETION

1. Navigate to **Assurance History**.
2. Click **Archive** on a completed audit mission → Mission status updates to `ARCHIVED`.
3. Click **Delete** on a completed audit mission → System displays safeguard modal rejecting permanent deletion of completed audit records.
4. Click **Delete** on a disposable draft/sample run → Mission is permanently deleted.


---

## AUTH — Authentication and authorization acceptance (run BEFORE Tests 1–5 on any deployed candidate)

Prerequisites (one-time, by a human, none of them done by code): the Firebase web app's **Authorized domains** include the candidate web host; the demo Firebase user exists (email/password); Firestore security rules deny all client access to `users/*`; the candidate API and worker run with `RATEGUARD_SERVICE_ROLE=api` / `worker` and the API has `RATEGUARD_FIREBASE_PROJECT_ID`, `RATEGUARD_CORS_ORIGINS` (exactly the candidate web origin), `VERTEX_AI_LOCATION=us`, `RATEGUARD_GEMINI_MODEL=gemini-3.1-flash-lite`.

Assign the demo user (dry run first, then apply; requires ADC with Firestore write + Firebase Auth read on the project; grants no IAM):

```bash
python backend/scripts/bootstrap_user.py --project-id rateguard-enhanced --email <demo-user-email> --tenant rateguard-demo --role ADMIN
python backend/scripts/bootstrap_user.py --project-id rateguard-enhanced --email <demo-user-email> --tenant rateguard-demo --role ADMIN --confirm rateguard-enhanced
```

Checklist (record mission ids / timestamps in `ACCEPTANCE_RESULTS.md`):

- [ ] **A-1 Startup config.** `GET <api>/api/v1/system/status` (as ADMIN) shows `configured_model_id = gemini-3.1-flash-lite`, `configured_location = us`, `auth_mode = VERTEX_AI`; Cloud Run env of api and worker contains no `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_MODEL`, `FIREBASE_ADMIN_KEY*`, and the revision has **no** secret mounts for them.
- [ ] **A-2 Anonymous rejection.** `curl -i <api>/api/v1/missions`, `/api/v1/sources/SRC-X`, `/api/v1/connectors`, `/api/v1/system/status` each → `401` with a `WWW-Authenticate: Bearer` header and no body detail beyond the fixed code. `GET /health/live` → `200 {"status":"healthy"}`; `/health/ready` body contains no project id, topic, subscription or exception text.
- [ ] **A-3 Public API does not serve the worker route.** `curl -i -X POST <api>/internal/pubsub/assurance` → `404`.
- [ ] **A-4 Worker is private.** `curl -i -X POST <worker>/internal/pubsub/assurance` without an identity token → `403` from Cloud Run IAM (not from the app); the Pub/Sub push subscription's OIDC service account is the only `roles/run.invoker`.
- [ ] **A-5 Browser login.** Open the candidate web URL → redirected to `/login`; wrong password → one generic error; correct login → lands on the app, header shows the email and role **Admin** (from `/api/v1/me`); reload keeps the session; **Sign out** returns to `/login` and `/missions` redirects to login again.
- [ ] **A-6 Token hygiene.** In browser DevTools → Network: every `/api/v1/*` request carries `Authorization: Bearer …`, no token appears in any URL, and Application → Local/Session storage contains no `eyJ…` JWT or password.
- [ ] **A-7 Unprovisioned user.** Sign in with a Firebase user that has **no** `users/{uid}` document → app shows "Your account is not set up"; `curl` with that user's ID token → `403 ACCOUNT_NOT_PROVISIONED`.
- [ ] **A-8 Roles.** Using temporary test users (bootstrap script, then re-run with `--role` to change or set `disabled: true` in Firestore, or delete the test users afterwards): VIEWER cannot create a mission (`403`), cannot download evidence (`403`) but can read it; CONSUMER_REVIEWER can approve/reject an explanation, cannot upload a source; RELEASE_OWNER can upload/compile/create/cancel, cannot approve an explanation, cannot `POST /connectors/{id}/test`; ADMIN can do all of these. Compare against the matrix in `docs/security/AUTHORIZATION_MATRIX.md`.
- [ ] **A-9 Client-supplied identity ignored.** As VIEWER: `curl -H "X-Role: ADMIN" -H "X-Tenant-Id: other" …/api/v1/system/status` → `403`. `POST /missions` with `"tenant_id":"other","role":"ADMIN"` in the body → record is stamped with the caller's own tenant (`GET /missions/{id}` shows `metadata`/`tenant` unchanged by the body).
- [ ] **A-10 Cross-tenant (A12).** Create a second tenant user (`--tenant other-tenant`), then: `GET`, `cancel`, evidence, download and explanation approve on a `rateguard-demo` mission id all return `404` (same body as a nonexistent id); the mission list of `other-tenant` is empty of demo missions.
- [ ] **A-11 Legacy records.** Pre-change demo missions are **hidden** unless `RATEGUARD_LEGACY_RECORD_TENANT_ID=rateguard-demo` is set on the API service; confirm the intended behaviour and record which was chosen.
- [ ] **A-12 CORS.** `curl -i -X OPTIONS <api>/api/v1/missions -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST"` → no `access-control-allow-origin`; the same from the candidate web origin → allowed, and the response has **no** `access-control-allow-credentials`.
- [ ] **A-13 Gemini smoke.** One clean and one defective mission complete with `gemini_invocations[*].model_id = gemini-3.1-flash-lite` (or an honest deterministic fallback recorded), via ADC only.
- [ ] **A-14 Scripted checks.** `RATEGUARD_ID_TOKEN=<demo admin ID token> python backend/scripts/verify_candidate.py --yes-test-candidate --api-url <candidate-api> --frontend-url <candidate-web>` and `RATEGUARD_ID_TOKEN=… python scripts/verify_deployed_system.py …` pass. (Pass the token via the environment only — never on the command line, never committed.)

Only after A-1…A-20 pass is it appropriate to run Tests 1–5 above and, separately and later, to disable/delete the `FIREBASE_ADMIN_KEY` and `GEMINI_API_KEY` Secret Manager secrets (a reviewed, explicitly approved infrastructure change — not part of this work).

### Session 6 additions to the AUTH checklist

Prerequisites (manual, reviewed, **not** performed by any script here): publish `infrastructure/firestore.rules`, build the composite indexes and enable the TTL policy from `infrastructure/firestore.indexes.json` (`firebase deploy --only firestore:rules,firestore:indexes --project rateguard-enhanced`, then wait until the index is `READY`; list endpoints return `FAILED_PRECONDITION` before that).

- [ ] **A-15 Guardrails effective.** Cloud Run env of api and worker shows `RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION=6`, `RATEGUARD_MAX_PROBE_ROUNDS=1`, `RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD=0.60` (or your deliberate overrides) and **none** of the unprefixed `MAX_*`/`LOW_CONFIDENCE_*` names; a defective mission's `ai_runtime.gemini_calls_made` is ≤ the configured cap.
- [ ] **A-16 Tenant-prefixed objects.** After an upload/compile/evidence download by the demo user, `gcloud storage ls -r gs://<bucket>/tenants/rateguard-demo/` shows exactly `sources/SRC-…/raw/SRC-…`, `sources/SRC-…/compiled/IPIR-SRC-…`, `missions/MIS-…/evidence/EVB-…`; no new object appears under the legacy `artifacts/` prefix.
- [ ] **A-17 Cross-tenant artifacts.** With a second tenant's user: `GET /sources/{id}`, `/sources/{id}/artifacts/{id}`, `POST /sources/{id}/compile`, mission evidence download all → `404`, indistinguishable from a random id.
- [ ] **A-18 Database-level queries.** `GET /missions` for each tenant returns only its own missions; Firestore console → Usage/Query explain shows the `tenant_id + created_at` index is used (no full-collection scan).
- [ ] **A-19 Rate limits.** As ADMIN, call `POST /connectors/rating-engine-demo/test` six times within an hour: calls 1-5 succeed, call 6 returns `429` with a numeric `Retry-After`; another user/tenant is unaffected; a `rate_limits` document exists with only tenant/uid/operation/window/count/expires_at fields (no token, IP or payload). Confirm the TTL policy shows `ACTIVE` on `rate_limits.expires_at`.
- [ ] **A-20 Rules.** From the browser console with the Firebase JS SDK (signed in as the demo user), any `getDoc`/`getDocs` on `users`, `assurance_runs*` or `rate_limits` fails with `permission-denied`, and the app keeps working (server-side access unaffected).
