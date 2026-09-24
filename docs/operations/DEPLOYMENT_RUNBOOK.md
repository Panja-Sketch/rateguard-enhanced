# Deployment runbook (Cloud Run, immutable digests)

Project `rateguard-enhanced`, region `us-central1`, Artifact Registry
`us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images`. The canonical
candidate → promotion flow is `infrastructure/deploy_candidate_enhanced.sh` (build +
deploy a no-traffic, `--tag candidate` revision and verify it) followed by
`infrastructure/promote_candidate_to_production.sh` (shift production traffic to the
tested candidate); commands below are what those scripts run.

1. **Preconditions** — clean worktree at the commit to release; tests green (`backend`: ruff, pytest with
   the Firestore emulator and `RATEGUARD_REQUIRE_EMULATOR=1`; `frontend`: typecheck, lint, jest, build,
   playwright; `python scripts/secret_scan.py`); Firestore rules tests (`infrastructure/firestore-rules-tests`).
2. **One-time resources** (idempotent): `infrastructure/setup_impact_pubsub.sh` (topic `impact-batches`,
   push subscription `impact-batches-worker-sub` → worker `/internal/pubsub/impact-batch` with OIDC as
   `rateguard-worker-sa`, DLQ `impact-batches-dead-letter`, 5 attempts, 10–600 s backoff);
   `gcloud firestore fields ttls update expires_at --collection-group=batches --enable-ttl`;
   `python infrastructure/monitoring/setup_monitoring.py`.
3. **Build + stage candidate** — `deploy_candidate_enhanced.sh --deploy-candidate`: builds the four images
   tagged with the **full git SHA** (never `latest`) and deploys them as `--no-traffic --tag candidate`
   Cloud Run revisions wired to the same production Firestore/GCS/BigQuery resources production already uses.
4. **Verify candidate** — an explicit three-step lifecycle (`deploy_candidate_enhanced.sh`), all guarded by the
   clean-tree / pushed-HEAD / project checks. Run it from the repo root at the candidate's commit, with operator
   Application Default Credentials (`gcloud auth application-default login`) and the backend dependencies
   installed (set `VERIFY_PYTHON` to the backend venv's interpreter if needed). Never paste a password or token
   into a terminal; the scripts neither read nor store one.
   1. `--prepare-verification` — confirms all four candidate revisions exist at 0 % traffic and differ from
      production; records the production revisions and the **byte-for-byte** live configuration of
      `assurance-runs-worker-sub` and `impact-batches-worker-sub`; writes the local pending-state file
      `infrastructure/.candidate-verified/<sha>.pending.json` (names, revisions, SHA, digests, URLs, original
      env values — no credentials) **before** the first change; creates SHA-scoped **mission**
      (`assurance-runs-candidate-verify-<sha12>`) and **impact** (`impact-batches-candidate-verify-<sha12>`)
      topics + push subscriptions, each pushing only to the candidate worker's own route
      (`/internal/pubsub/assurance`, `/internal/pubsub/impact-batch`) with the worker's **stable, untagged** URL
      as OIDC audience; points the candidate API (`RATEGUARD_PUBSUB_TOPIC`, `RATEGUARD_IMPACT_TOPIC`) and worker
      (`RATEGUARD_IMPACT_TOPIC`) at those topics only; then records the **final** candidate worker revision.
      Neither production topic is ever published to or subscribed to, so a production worker can never receive a
      candidate mission or impact batch. On any failure or signal it restores and deletes everything itself; on
      success it leaves the environment in place and prints the candidate web URL and instructions.
   2. **Observed manual mission** — in the candidate web URL, signed in with the usual demo-tenant account, create
      a mission named `[CANDIDATE-VERIFY-<sha12>] controlled workbook vs versioned REST connector` (paste it into the optional **Mission name** field on the Sources page; blank keeps the default name, which the verification refuses) (Source A: a
      controlled workbook; Source B: the versioned REST connector with an explicit engine version) and wait for
      a terminal decision. QUEUED/RUNNING/202 is not success.
   3. `--complete-verification --mission-id=<MIS-…>` — requires the pending-state file. Verifies the mission
      reached a completed decision and carries the synthetic marker; retrieves the evidence bundle with
      operator ADC and validates it (`deployment.json.cloud_run_revision` = the recorded candidate worker
      revision and ≠ production, `git_sha` = the full SHA, image digest, controlled-workbook + versioned REST
      connector provenance, manifest hashes); publishes the same job envelope twice to the isolated mission
      topic and confirms one terminal decision and unchanged evidence; confirms both production subscriptions
      are byte-for-byte unchanged; restores the candidate env; deletes the temporary resources; pins all four
      image digests in `<sha>.evidence`; only then clears the pending file. Any failed check exits non-zero,
      writes no evidence and leaves the isolated environment in place (fix and re-run, or abort).
   4. `--abort-verification` — idempotent restore + delete from the pending state (already-absent resources are
      fine; only the SHA-scoped names derived from the commit are ever deleted). Firestore/GCS acceptance
      evidence is preserved. It records `<sha>.aborted`, which makes promotion of that SHA refuse until a new
      prepare/complete succeeds.
   Then run `--record-verified`: it works **only** after `--complete-verification` succeeded (and refuses while a
   verification is pending or aborted, or if the candidate has moved off the pinned digests).
   `promote_candidate_to_production.sh` also refuses a pending or aborted verification — even with
   `--skip-verification-check`.
5. **Promote** — `promote_candidate_to_production.sh --promote`: refuses to proceed unless the
   candidate-tagged revisions still match the pinned verified digests, reuses those exact digests
   unchanged (no rebuild, including web), repoints worker/api at the production Pub/Sub topic, the
   **stable** production rating-engine URL, and the production CORS origin, and deploys the web image with
   `RATEGUARD_API_URL` set as **deployment-time runtime config** (a plain Cloud Run env var, never baked
   into the image — see `frontend/src/lib/runtimeConfig.ts`) rather than rebuilding it. Verifies Pub/Sub
   routing (endpoint + OIDC audience both stable, non-tag) after promotion. Optional
   `--auto-rollback-on-failure` invokes `rollback.sh` on a failed post-promotion check (which still requires
   its own explicit `--rollback` confirmation). Verify a live mission after promotion.
6. **Rollback** — `infrastructure/rollback.sh --rollback ...` (see `ROLLBACK_DR_RUNBOOK.md`).

### Rating-engine connector: endpoint vs. ID-token audience

The connector has two **separate, explicit** settings (never derive one from the other):

| Setting | Candidate | Production |
| --- | --- | --- |
| `RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL` (request endpoint) | candidate-**tagged** engine URL (`candidate---rateguard-rating-engine-…`) | stable engine URL |
| `RATEGUARD_RATING_ENGINE_CONNECTOR_AUDIENCE` (Google ID-token audience) | stable engine URL | stable engine URL |

(`RATEGUARD_VENDOR_GATEWAY_CONNECTOR_*` mirror these for the second registered wire shape.) Cloud Run
authenticates a token against the **service** URL; a token minted for a traffic-tagged URL is rejected with
HTTP 401 before the request reaches the engine, which surfaces as `CONNECTOR_AUTH_DENIED` on every probe and a
`REVIEW_REQUIRED` decision. Startup **fails closed** in `candidate`/`staging`/`production` if the audience is
missing, not https, tagged, unrelated to the endpoint, or if auth mode is not `google_id_token` /
`IS_LOCAL_DEV` is not `false`. The candidate script writes both settings into the worker and API env files
(after discovering the engine's stable URL); promotion sets endpoint and audience to the stable URL.

Triage: `gcloud logging read` on `rateguard-rating-engine` request logs — a 401 with
`www-authenticate: invalid_token` is an audience/identity problem; a 403 is a missing `roles/run.invoker`
binding for the calling service account; a 5xx/timeout is an engine problem. Operator-facing classes:
`CONNECTOR_AUTH_DENIED`, `CONNECTOR_TIMEOUT`, `CONNECTOR_CONTRACT_ERROR`, `CONNECTOR_VERSION_UNSUPPORTED`,
`CONNECTOR_UNAVAILABLE`. Rollback of a bad connector change is the ordinary `rollback.sh` revision rollback
(settings are per-revision env vars, so the prior revision restores the prior endpoint and audience).

## Known deployment-tooling limitations (documented, scheduled for operational hardening)

Neither item affects runtime security, authorization, rate limiting, data routing, monitoring, evidence or
rollback; both were classified after the `f2df35d` production promotion.

1. **`RATEGUARD_ENVIRONMENT=candidate` on promoted API/worker revisions.** The candidate env files set it and
   `promote_candidate_to_production.sh` repoints only the data-plane and connector variables. The application reads
   the value in exactly one place (`startup_checks.py`), where `candidate`, `staging` and `production` receive the
   *same* strict validation (no auth emulator, https CORS origins, rate limiting enforced, authenticated https
   connector with an explicit stable audience). Rate limits are set explicitly and enabled; data routing and Pub/Sub
   use explicit variables; evidence records git SHA, service, revision and digest, not the label; monitoring keys on
   service names. The previous production revisions carried the same label. Cosmetic/hygiene only; the promote
   script should set it to `production` in a later release (and `runtime-env.rateguard-enhanced.yaml` already does).
2. **`promote_candidate_to_production.sh --promote --canary-percent=N` cannot be completed by a bare `--promote`.**
   The script captures the prior production revision as the one at exactly 100% traffic; after a canary traffic is
   split, the capture is empty and the script exits before making any change (fail-closed). Workaround used:
   return traffic to 100% on the prior revisions (`rollback.sh` prints the `update-traffic` commands), then run a
   direct `--promote`. The fix is to record the prior revision before the canary starts, or accept it as an argument.
   `rollback.sh` itself only prints commands by design; it is not affected.

Compatibility rule: Firestore/GCS changes in a release must be backward compatible with the previous
revision (additive collections/fields only). Prompt 8 adds `impact_jobs` (+`batches` TTL), never mutating
existing documents, so the previous revisions run unchanged.
