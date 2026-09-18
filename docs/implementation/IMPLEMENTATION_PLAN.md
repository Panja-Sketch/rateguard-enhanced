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

### Session 1 (this session) — Shared foundations only

Scope explicitly excludes: workbook upload, connector networking (outbound HTTP client/SSRF/registry), frontend pages, deployment execution, and any roadmap feature not listed below.

- **CP0 — IPIR v0.2 contract.** New `backend/app/ipir/v0_2/` package: envelope (`schema_version`, `package_id`, `package_version`, `source`, `product`, `effective_period`, `transaction_types`), discriminated-union expression model (`LiteralExpression`, `ReferenceExpression`, `UnaryExpression`, `BinaryExpression`, `NaryExpression`, `ConditionalExpression`, `TableLookupExpression`, `RoundExpression`), explicit-currency/scale/rounding outputs, `ControlCase`, `Attestation`. Strict `extra="forbid"` throughout, lowercase ID pattern `^[a-z][a-z0-9_]{1,63}$`.
- **CP1 — v0.1 hardening.** Add recursive `extra="forbid"` to existing v0.1 IPIR leaf models (no behavior change otherwise). Add optional, additive `priority` field to `TableRow` and optional `requires_total_coverage`/`default_value` to `RateTable` so range-overlap/gap validation has something to check without breaking existing v0.1 JSON.
- **CP2 — Semantic validation.** Duplicate IDs, unresolved references (walked through the expression tree, not just `depends_on`), calculation-cycle detection (derived dependency graph, not only declared edges), range gap/ambiguous-overlap validation (shared table validator usable by both versions), effective-period validity, explicit currency/scale/rounding enforcement (structural, via required fields), explicit zero-division declaration on `DIVIDE` (`on_zero_behavior`, `REJECT` supported now; other behaviors explicitly deferred and rejected with a clear error, not silently ignored), and the §6.4 package-compatibility gate.
- **CP3 — Compatibility/lowering boundary (D2).** `backend/app/ipir/v0_2/compat.py`: `lower_to_v0_1(pkg: IPIRPackageV2) -> IPIRPackage`, reusing the existing oracle/evaluator unchanged. Documented, fail-closed limitation: only a top-level `RoundExpression` per calculation node is supported by this lowering pass (nested `ROUND` raises a clear error rather than silently mishandling precision).
- **CP4 — Deterministic golden fixtures.** `backend/scripts/generate_ipir_v0_2_golden_fixtures.py` deterministically emits a minimal, hand-authored v0.2 canonical/defective package pair (`roof_age` rate-table factor drift, mirroring the real AZ_HO3 narrative) with an embedded `ControlCase` proving $700.00 / $655.00 through the existing oracle via the CP3 lowering boundary. The existing v0.1 `data/implementations/{canonical,defective}` fixtures are untouched.
- **CP5 — `backend/rating_engine` service foundation (D3).** New sibling package to `backend/app`: `main.py` (`POST /quote`, `/health/live`), `engines/registry.py` + `engines/quote_service.py` (canonical-v1/defective-v1, backed by the CP4 fixtures through the CP3 lowering boundary), `startup_selftest.py` (proves both golden values before readiness, per §8.3), own `Dockerfile`. No outbound connector/SSRF/client code — that is explicitly out of scope this session.
- **CP6 — Tests.** Unit tests for every new v0.2 model and validator, the lowering boundary, the compatibility gate, and the rating-engine self-test/API. Regression run of the full existing `backend/tests` suite to confirm CP0–CP2 introduced no behavior change to current JSON missions.

### Deferred to later sessions (not started now)

- **CP7** — Controlled Workbook v1 compiler (`backend/app/ingestion/workbook_v1/`), ZIP/active-content safety, sheet/formula/DAG validation, compilation receipts, control-case execution against uploaded workbooks.
- **CP8** — Connector registry, REST target client, SSRF/timeout/retry controls, `backend/app/api/connectors.py`.
- **CP9** — `MissionStage` enum (D4) wired into `agents/supervisor.py`/`mission_execution_service.py`; stage-recording/checkpoint abstraction introduced in this session's foundations (see CP2a below) but not yet consumed by the supervisor.
- **CP2a — Stage-recording abstraction (D4, foundation only).** `backend/app/models/stages.py`: the 20-stage `MissionStage` enum and a `StageOutcome` (`COMPLETED`/`FAILED`/`REVIEW_REQUIRED`/`NOT_APPLICABLE` + reason) model, plus a small recorder helper. Not wired into the supervisor this session — that is CP9. Building the enum now (rather than later) satisfies D4's "introduce the enum and abstraction now" instruction without requiring the broader supervisor rewrite in the same pass.
- **CP10** — Frontend workbook upload, connector picker, cohort/consumer-impact UI.
- **CP11** — Deployment of `rating_engine` to Cloud Run, IAM, infra scripts update.
- **CP12** — Full test-pyramid backfill (`contract/`, `property/`, `e2e/`), mutation-test pass.

---

## File inventory for this session (CP0–CP6, CP2a)

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
