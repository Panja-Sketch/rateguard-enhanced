# RateGuard Enhanced — Implementation Decisions Log

Records decisions made in the course of implementing against `docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md`. That document is never edited; this log records how its locked requirements are sequenced and reconciled with the existing codebase. Entries are append-only; a superseded decision is marked superseded, not deleted.

---

## D1 — Remove PDF/PlatformConfig/extraction-strategy from the reachable MVP surface

**Decision (approved by user, 2026-09-17):** Remove PDF ingestion, `PLATFORM_CONFIG` ingestion, and the Gemini `CHOOSE_EXTRACTION_STRATEGY` decision from every buildable and reachable MVP path — registry entries, API routes, UI options, and the Gemini decision flow. The existing Excel/PDF adapter stubs must not return fabricated packages or confidence values. Any temporarily retained files must fail closed with an explicit `UNSUPPORTED_FORMAT` error and must not be registered or reachable. Old tests covering the removed paths must be updated or replaced, not left asserting removed behavior.

**Why:** The locked doc explicitly excludes PDF ingestion from the MVP (§4.1.B) and defers it (§4.2); `PLATFORM_CONFIG` has no basis in the locked source list (strict IPIR JSON + Controlled Workbook v1 only); `CHOOSE_EXTRACTION_STRATEGY` is not on the §11.3 allowed-LLM-decisions list. The current Excel/PDF adapters were found (by direct inspection) to return a hardcoded canonical package regardless of uploaded content — a truth-in-advertising risk under §2.2 if ever exercised.

**Sequencing:** Execution deferred to CP7 (Controlled Workbook v1 compiler), because removing the extraction-strategy path requires changes to `agents/supervisor.py` and `adapters/*`, which are outside this session's shared-foundations scope (the user's session-2 instructions list specific required outcomes that do not include this removal, and explicitly exclude workbook/connector work). Recorded here so it is not dropped. CP7 must not begin without first executing D1.

---

## D2 — IPIR v0.2 as a parallel module with a single lowering boundary

**Decision (approved by user, 2026-09-17):** Build IPIR v0.2 as a new, parallel module at `backend/app/ipir/v0_2/` rather than an in-place breaking rewrite of `backend/app/ipir/package.py`. IPIR v0.1 remains available for backward compatibility during migration. Add an explicit compatibility/migration boundary so the existing deterministic engines (oracle, diff, impact, reconciliation) are reused rather than duplicated for v0.2. IPIR v0.2 is authoritative for the new workbook and REST-connector flows going forward.

**Why:** Lower regression risk against the existing, tested v0.1 pipeline and existing demo/legacy scripts; matches the locked repository structure's `ipir/v0_2/` placement; avoids maintaining two pricing-engine implementations, which the user explicitly ruled out.

**Implementation shape (architectural decisions made while executing D2):**

- **ID strictness reconciliation.** v0.1's identifier pattern (`^[a-zA-Z0-9_.-]+$`, `ipir/common.py`) is permissive; the locked doc requires `^[a-z][a-z0-9_]{1,63}$` for v0.2. Rather than forking every leaf model, v0.2 reuses v0.1's structurally-sound leaf types (`PricingInput`, `PricingConstant`, `RateTable`, `EffectivePeriod`, `NodeReference`, `LiteralValue`) directly, and enforces the stricter ID pattern as a single aggregate validation pass in `IPIRPackageV2`'s package-level validator over every collected ID, rather than duplicating leaf classes solely to change one regex.
- **Expression model.** v0.1's `Expression` is a single flat `operator`+`operands` class; the locked doc requires 8 discriminated-union types. These are new, v0.2-only classes (`backend/app/ipir/v0_2/expressions.py`) tagged with a `kind` discriminator field. v0.1's `Expression` is untouched.
- **Lowering (`backend/app/ipir/v0_2/compat.py::lower_to_v0_1`)** converts an `IPIRPackageV2` into a plain `IPIRPackage` (v0.1) so the *existing, unmodified* oracle evaluator runs it:
  - `LiteralExpression`/`ReferenceExpression`/`TableLookupExpression` map directly to v0.1's `LiteralValue`/`NodeReference`/`NodeReference` (table results are pre-resolved into evaluation context by the existing v0.1 evaluator exactly as they are today; a v0.2 `TableLookupExpression(table_id=X)` therefore lowers to `NodeReference(ref=X)`, which is exactly how v0.1 already exposes table results to calculations).
  - `UnaryExpression("NEGATE")` lowers to `Expression(SUBTRACT, [0, operand])` — v0.1 has no native negate operator; this is a faithful, zero-risk rewrite since v0.1's evaluator is unmodified.
  - `BinaryExpression`/`NaryExpression` map directly to v0.1's `Expression(operator, operands)` for ADD/SUBTRACT/MULTIPLY/DIVIDE/MIN/MAX.
  - `RoundExpression` at the *top level* of a calculation node's expression is hoisted into v0.1's existing `CalculationNode.rounding_rule` (precision=`scale`, mode=`rounding_mode`), which v0.1's evaluator already applies correctly after expression evaluation — this reuses tested v0.1 rounding behavior exactly rather than adding a new evaluator code path. A `RoundExpression` nested anywhere other than the top level is **not supported by this lowering pass** and raises `LoweringNotSupportedError` rather than silently mis-rounding; this is a documented, fail-closed limitation, not a silent gap.
  - `ConditionalExpression` has no v0.1 inline-expression equivalent (v0.1 conditionals exist only as top-level `PricingRule` objects). Lowering synthesizes a `PricingRule` (using v0.1's existing, tested `ComparisonCondition`/`LogicalCondition`/`evaluate_condition`) and substitutes a `NodeReference` to it in the calculation — this reuses v0.1's existing rule-evaluation machinery unchanged rather than teaching the evaluator a new expression type.
  - `on_zero_behavior` on a `DIVIDE` `BinaryExpression` currently only accepts `"REJECT"` (matching v0.1's existing, hardcoded reject-on-zero behavior exactly, so lowering requires no evaluator change). Any other value is rejected at v0.2 model-validation time with a clear "not yet implemented" error — never silently accepted and silently mishandled.
- **Table range/gap/overlap validation.** Added as new, optional, additive fields directly on the *shared* `ipir/tables.py::RateTable`/`TableRow` (`priority: int | None`, `RateTable.requires_total_coverage: bool = False`, `RateTable.default_value: Decimal | None = None`) rather than a v0.2-only duplicate table model — both v0.1 and v0.2 packages get the benefit, and existing v0.1 JSON without these fields is unaffected (defaults preserve current behavior exactly).
- **Outputs.** v0.2's `PricingOutputV2` requires `currency`, `scale`, and `rounding_mode` as non-optional fields (Pydantic enforces presence — no custom validator needed for that half of §6.2). A package-level validator additionally checks that when an output's `source_ref` calculation has a top-level `RoundExpression`, the output's declared `scale`/`rounding_mode` must match it exactly — preventing the published contract and the actual rounding behavior from silently diverging.

---

## D3 — `backend/rating_engine` as a genuinely separate service, now

**Decision (approved by user, 2026-09-17):** Implement `backend/rating_engine` now as a real, separate deployable service: own entry point, Dockerfile, startup self-test, versioned `canonical-v1`/`defective-v1` implementations, and (in a later infra checkpoint) private Cloud Run deployment configuration. Unit tests may use an in-process substitute (FastAPI `TestClient`, no real network); the eventual acceptance path must call the separate service through the REST connector built in a later session.

**Why:** Matches locked §12.2 service-boundary requirement and avoids building a fake/in-process "connector" that would later need to be thrown away.

**Scope boundary drawn this session:** "Rating-engine foundation" (the target/server side: `/quote` endpoint, versioned engines, startup self-test) is in scope. "Connector networking" (the client side: outbound HTTP calls from the assurance backend, SSRF/timeout/retry, connector registry) is explicitly out of scope this session per the user's instructions and deferred to CP8. `backend/rating_engine` is therefore built and independently tested this session, but nothing in `backend/app` calls it yet.

---

## D4 — Full 20-stage `MissionStage` enum introduced now, wired incrementally

**Decision (approved by user, 2026-09-17):** Introduce the full locked 20-stage enum and a stage-recording/checkpoint abstraction now, rather than only adding two stages to the existing 8-stage model. Map existing supervisor internals onto it incrementally rather than rewriting `agents/supervisor.py` wholesale in one pass. Every required stage must end `COMPLETED`, `FAILED`, `REVIEW_REQUIRED`, or `NOT_APPLICABLE` with a reason — stages may never silently disappear.

**Why:** More faithful to the locked doc's explicit stage list (§7.3) while bounding regression risk on a large, currently-working file.

**Sequencing:** This session builds only the enum and outcome model (`backend/app/models/stages.py`) as a standalone, tested foundation — CP2a. Wiring it into `agents/supervisor.py`/`mission_execution_service.py` (CP9) is deferred, since that wiring is naturally driven by the workbook/connector work landing in later sessions, not by the shared-foundations work done here.

---

## Architecture notes not requiring a user decision (freedom-of-implementation-detail choices)

- **Golden fixtures are hand-authored, not migrated from the real AZ_HO3 spec.** Rather than writing a general v0.1→v0.2 "raiser" for the full, complex production AZ_HO3 rate plan (high effort, high risk, and duplicative of work the future Controlled Workbook v1 compiler will do anyway), this session hand-authors one minimal, deterministic v0.2 package pair (`data/implementations/v0_2/{canonical,defective}/AZ_HO3_GOLDEN_ipir.json`) that reproduces the exact locked golden narrative: `base_rate=500.00` × `roof_age_factor` (canonical `1.40`, defective `1.31`, `roof_age=25`) = `700.00` / `655.00`, generated deterministically by `backend/scripts/generate_ipir_v0_2_golden_fixtures.py`. The existing v0.1 `data/implementations/{canonical,defective}/AZ_HO3_2026_09_ipir.json` fixtures are untouched and continue to back existing legacy demo scripts unchanged.
- **`extra="forbid"` added recursively to all v0.1 IPIR leaf models** (§ required outcome 3), verified by re-running the full existing test suite immediately after, to catch any fixture relying on a previously-silently-ignored extra field.

---

## Post-implementation notes (2026-09-17, end of session 1)

All of D1–D4's session-1-scoped commitments were kept as recorded above:

- **D1**: not executed this session, as planned — deferred to CP7. Nothing new was added to the reachable extraction-strategy surface; no regression risk introduced.
- **D2**: `backend/app/ipir/v0_2/` built as planned. The lowering boundary (`compat.py`) was implemented exactly as designed above, with one addition discovered during implementation: an output-level consistency check (an output's declared `scale`/`rounding_mode` must match its source calculation's top-level `RoundExpression`, when present) was added to `IPIRPackageV2`'s validator — this wasn't in the original decision text but follows directly from it and is covered by `test_output_scale_mismatch_with_source_round_expression_rejected`.
- **D3**: `backend/rating_engine` built as a separate package with its own `main.py`, `Dockerfile`, and startup self-test, importing `app.*` directly (same image family, per locked doc section 19's nesting). Verified end-to-end via FastAPI `TestClient` (in-process, no real network) for `canonical-v1` ($700.00) and `defective-v1` ($655.00), including a `RENEWAL` transaction type and full trace request. No outbound connector/SSRF code was added — confirmed by inspection, nothing in `backend/app` imports from `rating_engine` or vice versa in the calling direction.
- **D4**: `backend/app/models/stages.py` built with the full 20-stage enum, a `StageOutcome` model that requires a reason for any non-`COMPLETED` outcome, and a `StageRecorder` bookkeeping helper. Confirmed not wired into `agents/supervisor.py` (grep found no import of `app.models.stages` outside its own tests) — CP9 remains open.

**One implementation-detail deviation from the original plan**, recorded here per the "append, don't silently revise" rule: the total-coverage gap check in `validate_range_coverage` (`app/ipir/tables.py`) was initially designed to require a table's covered ranges to span from `-Infinity` to `+Infinity`. This was corrected during testing (`test_full_coverage_with_no_gaps_passes` initially failed) because a real-world bounded input (e.g. `roof_age >= 0`) has a legitimate finite domain — requiring literal infinite bounds would have made `requires_total_coverage` effectively unusable for any realistic table. The corrected behavior only flags a gap *between* two declared, non-touching ranges; it does not require the first/last range to be open-ended. This is a strictly narrower (less falsely-restrictive) check than originally planned, not a weakening of a locked requirement — the locked doc's own gap-rejection language ("gaps are rejected... unless an explicit default exists") is about gaps between declared coverage, not a mandate that every table span negative-to-positive infinity.

**Verification summary**: 489/489 tests pass (398 pre-existing + 91 new), zero regressions, golden fixtures reproduce $700.00/$655.00 exactly through both the direct v0.2 control-case runner and the `backend/rating_engine` service's `/quote` endpoint. See `docs/implementation/STATUS.md` for full detail.
