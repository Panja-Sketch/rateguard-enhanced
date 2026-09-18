# RateGuard Enhanced — Implementation Plan

**Status:** Living document. Updated as checkpoints complete.
**Governs:** Work derived from `docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md` (locked, never modified by this plan).
**Scope of this plan:** Controlled Workbook v1 compiler and versioned REST rating-engine connector, and the shared foundations both depend on.

This plan does not restate or reinterpret the locked source of truth. Where an approved decision narrows or sequences a locked requirement, it is recorded in `docs/implementation/DECISIONS.md` and referenced here by decision ID.

---

## Approved scope decisions (see DECISIONS.md for full rationale)

- **D1** — PDF ingestion, `PLATFORM_CONFIG` ingestion, and the Gemini `CHOOSE_EXTRACTION_STRATEGY` decision are removed from every buildable/reachable MVP path (registry, API routes, UI, Gemini decision flow). Any retained files fail closed with `UNSUPPORTED_FORMAT` and are not registered or reachable.
- **D2** — IPIR v0.2 is built as a parallel module (`backend/app/ipir/v0_2/`). IPIR v0.1 remains available for backward compatibility during migration. A single, explicit compatibility/lowering boundary lets the existing deterministic engines (oracle, diff, impact, reconciliation) evaluate v0.2-authored packages without a second parallel pricing-engine implementation.
- **D3** — `backend/rating_engine` is a genuinely separate deployable service now: own entry point, Dockerfile, startup self-test, versioned canonical/defective implementations, private Cloud Run deployment configuration. A local in-process substitute is permitted only in unit tests; the eventual acceptance path calls the separate service through the REST connector (built in a later session).
- **D4** — The full locked 20-stage `MissionStage` enum is introduced now, via a stage-recording/checkpoint abstraction, with existing supervisor internals mapped onto it incrementally rather than rewritten wholesale. Every required stage ends `COMPLETED`, `FAILED`, `REVIEW_REQUIRED`, or `NOT_APPLICABLE` with a reason.

---

## Checkpoint sequence

### Session 2 — CP7 Controlled Workbook v1 compiler — DONE

Built `backend/app/ingestion/workbook_v1/` (ZIP/XML safety inspection, sheet/column contract, mini-DSL parsing, defensive raw-formula scan, IPIR v0.2 mapping reusing session 1's models/validators/lowering boundary unchanged, compilation receipt, `compile_workbook` entrypoint that never raises), `backend/scripts/generate_workbook_v1_samples.py`, `data/samples/workbook_v1/{canonical,defective,negative}/`, and `backend/tests/ingestion/workbook_v1/`. Also wired `.xlsx` into the real `PricingSourceIngestionService` ingestion boundary (`backend/app/services/ingestion_service.py`) so it is genuinely reachable end-to-end, not just a standalone module (D6) — review found the legacy fabricating Excel/PDF adapter path was already unreachable from that boundary before this session, so this wiring did not need to touch `agents/supervisor.py`. See `docs/implementation/STATUS.md` ("Session 2") for verified test counts and `docs/implementation/DECISIONS.md` (D5, D6) for implementation-shape decisions. Full D1 execution (removing PDF/PLATFORM_CONFIG/the Gemini `CHOOSE_EXTRACTION_STRATEGY` decision entirely) remains open — see D6's last paragraph. CP8–CP12 remain deferred as below.

### Session 3 — CP8 REST rating-engine connector — DONE

Built `backend/app/connectors/` (`errors.py` typed `ConnectorFailureCategory`/`ConnectorException` mirroring CP7's `WorkbookError` convention, `contract.py` versioned request/response models with trace node/operation allowlisting, `registry.py` config-driven fixed registry with fail-closed `select_connector`, `security.py` HTTPS/SSRF/DNS destination validation, `retry.py` pure backoff/jitter function, `budget.py` mission-level 60s budget tracker, `client.py` the real `httpx`-based HTTP client wiring all of section 8.2's controls together, `health.py` the golden-case health-test function), plus `backend/tests/connectors/` (58 tests). Calls the real `backend/rating_engine` service (session 1) over real HTTP semantics via `httpx.ASGITransport` in tests. Added four new additive `Settings` fields to `backend/app/core/config.py`. Did not modify `backend/rating_engine/*` or any CP7 file. See `docs/implementation/STATUS.md` ("Session 3") for exact test counts and locked-doc coverage, and `docs/implementation/DECISIONS.md` (D7) for implementation-shape judgment calls (registry config shape, jurisdiction reconciliation, DNS-rebinding residual limitation, idempotency scoping, auth-header design, response-size constant). No FastAPI route was added for `POST /connectors/{connector_id}/test`; no wiring into `app/agents/supervisor.py`/`app/api/*`/`app/missions/*` was added — both remain future integration-session work, confirmed by grep.

### Session 1 — Shared foundations only

Scope explicitly excludes: workbook upload, connector networking (outbound HTTP client/SSRF/registry), frontend pages, deployment execution, and any roadmap feature not listed below.

- **CP0 — IPIR v0.2 contract.** New `backend/app/ipir/v0_2/` package: envelope (`schema_version`, `package_id`, `package_version`, `source`, `product`, `effective_period`, `transaction_types`), discriminated-union expression model (`LiteralExpression`, `ReferenceExpression`, `UnaryExpression`, `BinaryExpression`, `NaryExpression`, `ConditionalExpression`, `TableLookupExpression`, `RoundExpression`), explicit-currency/scale/rounding outputs, `ControlCase`, `Attestation`. Strict `extra="forbid"` throughout, lowercase ID pattern `^[a-z][a-z0-9_]{1,63}$`.
- **CP1 — v0.1 hardening.** Add recursive `extra="forbid"` to existing v0.1 IPIR leaf models (no behavior change otherwise). Add optional, additive `priority` field to `TableRow` and optional `requires_total_coverage`/`default_value` to `RateTable` so range-overlap/gap validation has something to check without breaking existing v0.1 JSON.
- **CP2 — Semantic validation.** Duplicate IDs, unresolved references (walked through the expression tree, not just `depends_on`), calculation-cycle detection (derived dependency graph, not only declared edges), range gap/ambiguous-overlap validation (shared table validator usable by both versions), effective-period validity, explicit currency/scale/rounding enforcement (structural, via required fields), explicit zero-division declaration on `DIVIDE` (`on_zero_behavior`, `REJECT` supported now; other behaviors explicitly deferred and rejected with a clear error, not silently ignored), and the §6.4 package-compatibility gate.
- **CP3 — Compatibility/lowering boundary (D2).** `backend/app/ipir/v0_2/compat.py`: `lower_to_v0_1(pkg: IPIRPackageV2) -> IPIRPackage`, reusing the existing oracle/evaluator unchanged. Documented, fail-closed limitation: only a top-level `RoundExpression` per calculation node is supported by this lowering pass (nested `ROUND` raises a clear error rather than silently mishandling precision).
- **CP4 — Deterministic golden fixtures.** `backend/scripts/generate_ipir_v0_2_golden_fixtures.py` deterministically emits a minimal, hand-authored v0.2 canonical/defective package pair (`roof_age` rate-table factor drift, mirroring the real AZ_HO3 narrative) with an embedded `ControlCase` proving $700.00 / $655.00 through the existing oracle via the CP3 lowering boundary. The existing v0.1 `data/implementations/{canonical,defective}` fixtures are untouched.
- **CP5 — `backend/rating_engine` service foundation (D3).** New sibling package to `backend/app`: `main.py` (`POST /quote`, `/health/live`), `engines/registry.py` + `engines/quote_service.py` (canonical-v1/defective-v1, backed by the CP4 fixtures through the CP3 lowering boundary), `startup_selftest.py` (proves both golden values before readiness, per §8.3), own `Dockerfile`. No outbound connector/SSRF/client code — that is explicitly out of scope this session.
- **CP6 — Tests.** Unit tests for every new v0.2 model and validator, the lowering boundary, the compatibility gate, and the rating-engine self-test/API. Regression run of the full existing `backend/tests` suite to confirm CP0–CP2 introduced no behavior change to current JSON missions.

### Deferred to later sessions (not started now)

- **CP9** — `MissionStage` enum (D4) wired into `agents/supervisor.py`/`mission_execution_service.py`; stage-recording/checkpoint abstraction introduced in this session's foundations (see CP2a below) but not yet consumed by the supervisor. Also: wiring the CP8 connector module into the mission pipeline (mission-facing `SOURCE_B_LOAD_OR_CONNECTOR_CHECK`/`TARGET_EXECUTION` stages), and adding an actual `backend/app/api/connectors.py` route (`GET /connectors`, `POST /connectors/{connector_id}/test`) — CP8 built the connector module itself (registry, contract, client, health-test function) and it is genuinely reachable/tested standalone, but nothing yet calls it from `app/agents/supervisor.py` or `app/api/*`.
- **CP2a — Stage-recording abstraction (D4, foundation only).** `backend/app/models/stages.py`: the 20-stage `MissionStage` enum and a `StageOutcome` (`COMPLETED`/`FAILED`/`REVIEW_REQUIRED`/`NOT_APPLICABLE` + reason) model, plus a small recorder helper. Not wired into the supervisor this session — that is CP9. Building the enum now (rather than later) satisfies D4's "introduce the enum and abstraction now" instruction without requiring the broader supervisor rewrite in the same pass.
- **CP10** — Frontend workbook upload, connector picker, cohort/consumer-impact UI.
- **CP11** — Deployment of `rating_engine` to Cloud Run, IAM, infra scripts update.
- **CP12** — Full test-pyramid backfill (`contract/`, `property/`, `e2e/`), mutation-test pass.

---

## File inventory for session 2 (CP7)

New:
- `backend/app/ingestion/__init__.py`
- `backend/app/ingestion/workbook_v1/__init__.py`
- `backend/app/ingestion/workbook_v1/limits.py`
- `backend/app/ingestion/workbook_v1/errors.py`
- `backend/app/ingestion/workbook_v1/zip_safety.py`
- `backend/app/ingestion/workbook_v1/sheets.py`
- `backend/app/ingestion/workbook_v1/formulas.py`
- `backend/app/ingestion/workbook_v1/mapping.py`
- `backend/app/ingestion/workbook_v1/receipt.py`
- `backend/app/ingestion/workbook_v1/sanitize.py`
- `backend/app/ingestion/workbook_v1/compiler.py`
- `backend/scripts/generate_workbook_v1_samples.py`
- `data/samples/workbook_v1/canonical/AZ_HO3_GOLDEN_workbook.xlsx` (generated)
- `data/samples/workbook_v1/defective/AZ_HO3_GOLDEN_workbook.xlsx` (generated)
- `data/samples/workbook_v1/negative/*.xlsx` (21 fixtures, generated)
- `backend/tests/ingestion/__init__.py`
- `backend/tests/ingestion/workbook_v1/*` (new test files)

Modified: `backend/app/services/ingestion_service.py` (D6 — routes `.xlsx` to the new compiler at the real ingestion boundary), `backend/tests/agents/test_extraction_orchestration.py` (narrowed/added tests reflecting that `.xlsx` is now accepted there — see DECISIONS.md D6).

Explicitly not touched this session: `backend/app/adapters/*` (full D1 removal remains open — see DECISIONS.md D6), `backend/app/api/*` (no HTTP route added), `backend/app/agents/*` (supervisor/Gemini extraction-strategy code untouched, confirmed unreachable for `.xlsx`), `backend/app/engines/target/*`, `frontend/*`, `infrastructure/*`.

---

## File inventory for session 3 (CP8)

New:
- `backend/app/connectors/__init__.py`
- `backend/app/connectors/errors.py`
- `backend/app/connectors/contract.py`
- `backend/app/connectors/redact.py`
- `backend/app/connectors/retry.py`
- `backend/app/connectors/budget.py`
- `backend/app/connectors/security.py`
- `backend/app/connectors/registry.py`
- `backend/app/connectors/client.py`
- `backend/app/connectors/health.py`
- `backend/tests/connectors/__init__.py`
- `backend/tests/connectors/conftest.py`
- `backend/tests/connectors/test_registry.py`
- `backend/tests/connectors/test_contract.py`
- `backend/tests/connectors/test_security.py`
- `backend/tests/connectors/test_retry.py`
- `backend/tests/connectors/test_redaction.py`
- `backend/tests/connectors/test_client_golden.py`
- `backend/tests/connectors/test_client_negative.py`
- `backend/tests/connectors/test_health.py`

Modified: `backend/app/core/config.py` (four new additive `Settings` fields — `rating_engine_connector_base_url`, `rating_engine_connector_is_local_dev`, `rating_engine_connector_auth_header_name`, `rating_engine_connector_auth_token_env_var` — all with safe defaults, no existing field changed).

Explicitly not touched this session: `backend/rating_engine/*` (no narrow reason arose to modify it — see DECISIONS.md D7's jurisdiction bullet), `backend/app/ingestion/*`/`backend/app/services/ingestion_service.py`/`backend/tests/agents/test_extraction_orchestration.py` (CP7's uncommitted work, confirmed untouched by `git status`), `backend/app/api/*` (no route added), `backend/app/agents/*` (no mission-pipeline wiring — confirmed by grep that nothing outside `backend/app/connectors/`/`backend/tests/connectors/` imports `app.connectors`), `frontend/*`, `infrastructure/*`.

---

## File inventory for session 1 (CP0–CP6, CP2a)

New:
- `backend/app/ipir/v0_2/__init__.py`
- `backend/app/ipir/v0_2/envelope.py`
- `backend/app/ipir/v0_2/expressions.py`
- `backend/app/ipir/v0_2/outputs.py`
- `backend/app/ipir/v0_2/control_cases.py`
- `backend/app/ipir/v0_2/attestation.py`
- `backend/app/ipir/v0_2/calculations.py`
- `backend/app/ipir/v0_2/package.py`
- `backend/app/ipir/v0_2/compatibility.py`
- `backend/app/ipir/v0_2/compat.py`
- `backend/app/ipir/v0_2/errors.py`
- `backend/app/models/stages.py`
- `backend/scripts/generate_ipir_v0_2_golden_fixtures.py`
- `data/implementations/v0_2/canonical/AZ_HO3_GOLDEN_ipir.json` (generated)
- `data/implementations/v0_2/defective/AZ_HO3_GOLDEN_ipir.json` (generated)
- `backend/rating_engine/__init__.py`
- `backend/rating_engine/main.py`
- `backend/rating_engine/engines/__init__.py`
- `backend/rating_engine/engines/registry.py`
- `backend/rating_engine/engines/quote_service.py`
- `backend/rating_engine/startup_selftest.py`
- `backend/rating_engine/Dockerfile`
- `backend/tests/ipir/v0_2/*` (new test files)
- `backend/tests/rating_engine/*` (new test files)

Modified (additive only, no removed behavior):
- `backend/app/ipir/common.py`, `inputs.py`, `constraints.py`, `tables.py`, `calculations.py`, `product.py`, `modifiers.py`, `rules.py`, `provenance.py`, `expressions.py`, `package.py` — add `model_config = ConfigDict(extra="forbid")`; `tables.py` gains optional `priority`/`requires_total_coverage`/`default_value` fields and a shared range-validation helper.

Explicitly not touched this session: `backend/app/adapters/*`, `backend/app/api/*`, `backend/app/agents/*`, `backend/app/engines/target/*`, `frontend/*`, `infrastructure/*`.

---

## Definition of done for this session

- All CP0–CP6 files exist and pass their own tests.
- Full existing `backend/tests` suite passes unchanged (no v0.1 behavior regression).
- `backend/rating_engine`'s startup self-test proves `canonical-v1` = $700.00 and `defective-v1` = $655.00.
- `docs/implementation/STATUS.md` and `docs/implementation/DECISIONS.md` reflect the actual, verified end state — not aspirational claims.
