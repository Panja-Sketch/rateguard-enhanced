# Claim-to-Evidence Matrix

This document maps every material customer-facing claim added or retained by the positioning-hardening work
(frontend homepage/architecture/positioning pages, README.md, docs/architecture/*.md, docs/demo/*.md) to either
working code or a specific test/session result recorded in `docs/implementation/STATUS.md` and
`docs/demo/ACCEPTANCE_RESULTS.md` / `docs/demo/DEPLOYED_ACCEPTANCE_TEST.md`. It does not restate
`docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md`, which remains the authoritative, locked (read-only) source
of truth; this matrix is a working cross-reference for the claims actually shipped in the UI and docs today.

| # | Claim (as shown to a customer/judge) | Evidence | Source |
|---|---|---|---|
| 1 | RateGuard compiles native IPIR JSON and the RateGuard Controlled Workbook v1 (`.xlsx`) contract deterministically. | `backend/app/ingestion/workbook_v1/compiler.py`; 35 passing tests in `backend/tests/ingestion/workbook_v1/` (27 compiler + 8 sanitizer); canonical workbook compiles to `VERIFIED` with control case `actual="700.00"`. | STATUS.md "Session 2 — CP7", `test_canonical_workbook_compiles_and_produces_700` |
| 2 | RateGuard does **not** accept Excel/PDF beyond the exact Controlled Workbook v1 contract; legacy `.xls`/PDF are rejected with a structured error, not silently approximated. | `PricingSourceIngestionService.register_source` rejects `.xls`/PDF before any adapter runs; `test_ingestion_service_rejects_legacy_xls_and_pdf_uploads`. | STATUS.md "Session 2", D6 in DECISIONS.md |
| 3 | RateGuard reaches a candidate implementation via a versioned, authenticated REST connector contract, not an arbitrary URL. | `backend/app/connectors/{contract,registry,client,security}.py`; `GET /api/v1/connectors` returns credential-free metadata only (`test_connectors_api.py` asserts no `base_url`/`http://`/`https://` in the response); no free-text URL field in `sources/page.tsx`'s connector picker (dropdown only). | STATUS.md "Session 3 — CP8", "Session 4 — CP9" |
| 4 | RateGuard has **not** built or tested a named-vendor (Guidewire/Duck Creek/AS400) adapter — only the connector contract, plus one real demo target. | Grep of `backend/app/connectors/registry.py` config and `backend/app/core/config.py`: the only registered connector target is `backend/rating_engine` (`canonical-v1`/`defective-v1`). No Guidewire/Duck Creek client code exists anywhere in `backend/`. | Direct repository inspection during this session; STATUS.md "Known limitations" (Session 3/4) |
| 5 | The demo connector proves `canonical-v1` → $700.00 and `defective-v1` → $655.00 for the golden case. | `backend/rating_engine/startup_selftest.py::run_startup_selftest()`; reproduced end-to-end through the real supervisor + `ConnectorClient` + real `rating_engine` service (`httpx.ASGITransport`) in `test_supervisor_connector_path.py` and `test_workbook_to_connector_mission_e2e.py`. | STATUS.md "Session 1" (rating-engine golden-value proof) and "Session 4"; ACCEPTANCE_RESULTS.md CW-A1/CW-A2 |
| 6 | A confirmed defect against the connector-backed workbook mission produces `BLOCK_DEPLOYMENT` with the exact $700.00 vs $655.00 divergence and root cause. | `test_golden_case_defective_connector_blocks_with_first_divergent_node`. | ACCEPTANCE_RESULTS.md §2, row CW-A2 |
| 7 | The sample-JSON demo scenario (`AZ_HO3_2026_09` vs `AZ_HO3_2026_09_DEFECTIVE`) quantifies a $868,974.18 gross absolute portfolio exposure and blocks deployment. | Deployed acceptance run against the live production URL, recorded step-by-step. | `docs/demo/DEPLOYED_ACCEPTANCE_TEST.md`, TEST 2 |
| 8 | The clean-control sample-JSON scenario returns `PASS` with `$0.00` exposure and 5/5 matched experiments. | Deployed acceptance run. | `docs/demo/DEPLOYED_ACCEPTANCE_TEST.md`, TEST 1 |
| 9 | Connector hard-failure and partial/incomplete responses never produce a silent `PASS`. | `test_connector_hard_failure_never_produces_pass`, `test_connector_partial_response_forces_review_required_not_pass`. | ACCEPTANCE_RESULTS.md §3 |
| 10 | Duplicate Pub/Sub delivery of a connector-backed mission executes the supervisor and connector exactly once and produces exactly one final decision. | `test_no_duplicate_execution_under_redelivery_for_connector_backed_mission`. | ACCEPTANCE_RESULTS.md §1 row 7 |
| 11 | Every mission records all 20 locked `MissionStage` values (completed/failed/review_required/not_applicable with a reason) — no stage silently disappears. | `test_stage_recorder_wiring.py` asserts `set(stage_outcomes) == set(MISSION_STAGE_ORDER)` across clean, material-drift, and connector-backed paths. | STATUS.md "Session 4" |
| 12 | Evidence includes SHA-256 hashes of source artifacts, connector request/response payloads, and per-stage outcomes, collected into a downloadable bundle with a manifest. | `EvidenceType.SOURCE` (`artifact_sha256`, `compiler_version`), `EvidenceType.CONNECTOR_INVOCATION` (request/response SHA-256); `Evidence Lineage` mission tab. | STATUS.md "Session 4"; ACCEPTANCE_RESULTS.md §1 row 3 |
| 13 | RateGuard's evidence bundle is **SHA-256 hashed with a manifest**, not a cryptographic hash chain linking every record to the previous one. | No hash-chain implementation exists in `backend/app/` (grepped, no matches for "hash chain"/"hash_chain" outside the locked source-of-truth doc's own §4.1.F, which is aspirational spec language). Positioning copy (`frontend/src/app/positioning/page.tsx`, README.md) explicitly states this and avoids the word "hash-chained." | This session's repository audit; see "Known discrepancy" note below |
| 14 | All monetary/portfolio arithmetic is deterministic Python `Decimal`; Gemini never computes a premium, exposure figure, or policy count. | `backend/app/engines/*` (oracle, portfolio); architecture page "Strict Deterministic Boundary Guarantee" panel; README "The Deterministic Boundary" section. | STATUS.md throughout; `docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md` §11.4 |
| 15 | The 50,000-policy portfolio is synthetic, seeded, and disclosed as such everywhere it is referenced. | `data/portfolio/az_ho3_2026_synthetic_50k.csv`; README "Limitations" and new "What RateGuard Does Not Do" sections; homepage step 5 copy. | README.md; `docs/demo/AZ_HO3_DEMO_KIT.md` |
| 16 | RateGuard does not perform a legal fairness/discrimination determination; cohort screening is disclosed with the locked disclaimer. | Locked doc §9.3 disclaimer text is the authoritative wording; this session's positioning copy (`positioning/page.tsx`, README) restates the same limitation without adding a new, broader claim. | `docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md` §9.3 |
| 17 | Backend test suite: 598 passed, 0 failed as of Session 4 (baseline before this positioning session's changes — no backend files were modified by this work). | `cd backend && python -m pytest -q` → 598 passed, 0 failed, 1002.48s. | STATUS.md "Session 4", "Test results (session 4)" |
| 18 | Frontend typecheck is clean after this session's UI changes (homepage, positioning page, architecture page, sources page, navigation). | `npm run typecheck` run in this session — see the test-results section of this session's final report for the exact output. | This session |
| 19 | No browser-driven (Playwright) E2E suite exists in this repository; none was added by CP9 or by this positioning session. | Confirmed by repository inspection: no `playwright.config.*`, no `@playwright/test` dependency in `frontend/package.json`. | STATUS.md "Session 4", ACCEPTANCE_RESULTS.md §5 item 1; confirmed again this session |

## Known discrepancy: "hash-chained" vs. hashed-with-manifest

`docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md` §4.1.F states the MVP must ship a "**Hash-chained final
manifest** referencing the previous event hash." That document is locked and was not edited as part of this work.
However, nothing in the current backend (`backend/app/storage/`, `backend/app/connectors/`, or the evidence-related
services referenced in STATUS.md/DECISIONS.md) implements a hash chain — i.e., no code was found that hashes a
record together with the hash of the prior record to form a linked chain. What is actually built and verified is:
SHA-256 hashes of individual artifacts (source files, compiled IPIR, connector request/response payloads), each
recorded independently, collected into an evidence bundle with a manifest listing those hashes.

This session resolved the customer-facing language by treating §4.1.F as aspirational roadmap language (consistent
with the locked doc's own framing in §23, "Roadmap after the challenge") rather than a claim of built behavior, and
by matching all new/edited customer-facing copy (`positioning/page.tsx`, `page.tsx`, README.md, architecture docs) to
what STATUS.md and ACCEPTANCE_RESULTS.md actually verify: **"tamper-evident, SHA-256 hashed evidence bundle with a
manifest."** No new copy uses the word "hash-chained." This discrepancy between the locked spec's aspirational
wording and the implementation is flagged here rather than silently resolved in either direction — a future session
should either (a) implement an actual hash chain to match §4.1.F, or (b) treat this as accepted, documented scope
reduction.
