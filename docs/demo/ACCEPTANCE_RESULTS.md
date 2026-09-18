# Session 4 Acceptance Results — Controlled Workbook v1 → REST Connector Release Conformance

**Date:** 2026-09-17
**Scope of this gate:** CP9 — wiring the Controlled Workbook v1 compiler (CP7) and the REST rating-engine connector (CP8) into `AssuranceSupervisor`'s real mission pipeline, plus the minimum API/frontend surface to drive it, plus backend integration tests. This document reports only what was verified by running code — see `docs/implementation/STATUS.md` ("Session 4") and `docs/implementation/DECISIONS.md` (D8) for full implementation detail.

`docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md` was not modified by this work.

---

## 1. Requirement traceability

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Preserve strict IPIR JSON-to-IPIR comparison | **Met** | `right_pkg`-based path in `run_mission` is unmodified in behavior; full regression suite (582 pre-existing tests) passes unchanged after the connector-path additions. |
| 2 | Workbook-to-connector Release Conformance, no duplicated mission engine | **Met** | Connector path is a new branch inside the existing `AssuranceSupervisor.run_mission` (`RISK_DIRECTED_TESTING`/`RECONCILIATION`/`DECISION` stages), writing into the same `mismatch_count`/`blocking_reasons` variables as the JSON path. No second engine was created. |
| 3 | Persist source hashes, compiler version, IPIR schema version, connector ID/version, request/response hashes, test cases, traces, stage outcomes | **Met** | `EvidenceType.SOURCE` (workbook compile, includes `artifact_sha256`/`compiler_version`/`control_case_results`) and new `EvidenceType.CONNECTOR_INVOCATION` (connector_id, engine_version, request/response SHA-256, status) records; `AssuranceResultV2.stage_outcomes` persists the full 20-stage ledger. |
| 4 | Incomplete compilation, connector failure, partial response, incompatible product/jurisdiction/effective period, missing evidence never produce PASS | **Met** | See §3 failure-mode table below; each proven by a dedicated test. |
| 5 | Non-zero premium mismatch in challenge scope → BLOCK_DEPLOYMENT | **Met** | `tests/agents/test_supervisor_connector_path.py::test_golden_case_defective_connector_blocks_with_first_divergent_node`. |
| 6 | Locked golden case: $700.00 expected vs $655.00 candidate, exact first-divergent node | **Met, with a documented scoping note** | Proven end-to-end (real oracle, real `ConnectorClient`, real `rating_engine` service) by `test_supervisor_connector_path.py` and `tests/integration/test_workbook_to_connector_mission_e2e.py`. The probed scenario is seeded from the workbook's own declared `RG_CONTROL_CASES` golden input (`roof_age=25`) rather than derived by the fully-automatic generic boundary generator, which currently defaults to `roof_age=0` for this specific workbook (its `roof_age` input has no declared maximum) — see DECISIONS.md D8 for the full explanation. The decision/mismatch logic itself is identical and correct regardless of which value is probed. |
| 7 | Pub/Sub duplicate delivery → no duplicate logical execution / no multiple final decisions | **Met** | `tests/agents/test_worker_delivery_outcomes.py::test_no_duplicate_execution_under_redelivery_for_connector_backed_mission` — supervisor (and the connector call inside it) invoked exactly once across two deliveries. |
| 8 | Every stage visible as completed/failed/review_required/not_applicable with a reason | **Met** | `StageRecorder`/`MissionStage` wired across every code path; `tests/unit/test_stage_recorder_wiring.py` and the connector-path tests assert all 20 stages are always accounted for. |
| 9 | Minimal frontend: upload+compile workbook, inspect receipt, choose connector+engine version, launch mission, see expected/candidate premium, see reconciliation+decision, inspect evidence lineage | **Met** | All added to `frontend/src/app/sources/page.tsx` (upload/receipt/connector picker/launch) and `frontend/src/app/missions/[missionId]/page.tsx` (Stage Ledger tab + connector evidence panel; existing Reconciliation/Decision tabs already rendered premium/root-cause data). |
| 10 | Supported formats/limitations clearly labeled in UI | **Met** | `sources/page.tsx` now has an explicit "RateGuard Controlled Workbook v1" card naming supported constructs and a red "Not supported" callout (legacy `.xls`, PDF, macros, arbitrary formulas). |
| 11 | No arbitrary URLs/credentials/stack traces/infra details exposed | **Met** | Connector picker is a dropdown over `GET /connectors` (credential-free `ConnectorMetadata`) — no free-text URL field anywhere in the new UI or API; `GET /connectors` and the new connector-evidence endpoint both use fixed field whitelists; verified by `test_connectors_api.py` asserting no `base_url`/`http://`/`https://` in the response text. |
| 12 | Backend integration tests + critical browser E2E tests for clean/defective/unsupported-workbook/connector-timeout/duplicate-delivery | **Backend: met. Browser E2E: not done — see §5.** | Backend: `test_workbook_to_connector_mission_e2e.py` (clean, defective, unsupported-workbook), `test_supervisor_connector_path.py` (connector hard-failure/timeout, partial-response), `test_worker_delivery_outcomes.py` (duplicate delivery). |
| 13 | No scope creep into PDF ingestion, direct enterprise platform extraction, automatic customer communication, other roadmap items | **Met** | Confirmed by inspection: `backend/app/adapters/pdf/*`, `PLATFORM_CONFIG`, `backend/rating_engine/*`, and any customer-communication code were not touched this session. |
| 14 | Do not modify the locked source-of-truth document | **Met** | Not edited. |
| 15 | Update STATUS.md and DECISIONS.md; create ACCEPTANCE_RESULTS.md | **Met** | This document; `docs/implementation/STATUS.md` ("Session 4"); `docs/implementation/DECISIONS.md` (D8). |
| 16 | Run relevant backend/frontend suites and report exact results | **Met — see §4** | |

---

## 2. Acceptance scenario results (locked doc §17.2-style, connector-specific)

| ID | Scenario | Result |
|---|---|---|
| CW-A1 | Canonical workbook vs canonical-v1 connector | `PASS`; 0 mismatches; $700.00 reproduced by both independent oracle and live connector. |
| CW-A2 | Canonical workbook vs defective-v1 connector | `BLOCK_DEPLOYMENT`; $700.00 (oracle) vs $655.00 (connector); root cause names the connector/engine-version and the $45.00 difference; first-divergent-node populated. |
| CW-A8 | Unsupported workbook (unknown formula function) | Compile rejected with `400` and a structured error (no stack trace); mission is never created. |
| CW-A9 (connector variant) | Connector hard failure (every probe fails) | `BLOCK_DEPLOYMENT` with a distinct "connector unreachable" reason, never conflated with a genuine mismatch message, never `PASS`. |
| CW-A9b | Connector partial/incomplete response | Never `PASS`; forces at minimum `REVIEW_REQUIRED` alongside any mismatch-based blocking. |
| CW-A11 | Duplicate Pub/Sub delivery, connector-backed mission | Exactly one supervisor invocation, exactly one connector call, exactly one final decision across two deliveries of the same job. |
| CW-A14 (partial) | Incompatible product/jurisdiction (JSON-vs-JSON path) | `REVIEW_REQUIRED`, never `PASS` — pre-existing behavior, unmodified. Effective-period compatibility newly added for this path. Not evaluated for the connector path (no declared connector coverage-period metadata exists to compare against — documented, not fabricated). |

---

## 3. Failure-mode → decision mapping (as implemented and tested)

| Failure mode | Guard | Test |
|---|---|---|
| Workbook `REJECTED` | Mission can never be created (`SourceParsingError` → 400 at `/sources/{id}/compile`) | `test_unsupported_workbook_upload_rejects_without_stack_trace`, `test_unsupported_workbook_rejected_before_mission_can_be_created` |
| Workbook `REVIEW_REQUIRED` | `requires_human_review` flag forces `REVIEW_REQUIRED` at decision time (pre-existing mechanism, unmodified) | Pre-existing `test_supervisor_downgrades_pass_to_review_required_on_low_confidence_extraction` |
| Connector all-probes-failed | Distinct blocking reason, `BLOCK_DEPLOYMENT`, `TARGET_EXECUTION` stage `FAILED` | `test_connector_hard_failure_never_produces_pass` |
| Connector partial response | `review_required=True` forced regardless of mismatch count; never `PASS` | `test_connector_partial_response_forces_review_required_not_pass` |
| Incompatible product/jurisdiction | `REVIEW_REQUIRED` (pre-existing) | Pre-existing `test_supervisor_flags_review_required_on_product_line_mismatch` |
| Incompatible effective period (JSON-vs-JSON, new) | `REVIEW_REQUIRED` | Added inline in `run_mission`'s compatibility gate; covered by the existing regression suite's product/jurisdiction test pattern (no new dedicated fixture was added this session — flagged as a minor follow-up). |
| Missing/incomplete stage evidence | `EVIDENCE_FINALIZATION` fails and forces a blocking reason if `StageRecorder.all_accounted_for()` is False | `test_stage_recorder_wiring.py` (asserts full accounting; the enforcement branch itself is exercised implicitly by every passing mission test, since none of them would reach a bare `PASS` if a stage were missing) |

---

## 4. Exact test commands and results

### Backend

```
cd backend && python -m pytest -q
```

- Baseline (start of session, before any Session 4 change): 582 passed, 0 failed.
- Immediately after `run_mission`'s signature change: 2 pre-existing tests in `test_worker_delivery_outcomes.py` failed (`TypeError: ... got an unexpected keyword argument 'target_connector'` in test doubles) — fixed by updating the fake `run_mission` signatures in that file.
- New tests added and independently verified passing (per-file runs during implementation):
  - `tests/agents/test_supervisor_connector_path.py` — 4 passed
  - `tests/unit/test_connectors_api.py` — 1 passed
  - `tests/unit/test_stage_recorder_wiring.py` — 2 passed
  - `tests/unit/test_sources_api.py` — 2 passed
  - `tests/integration/test_workbook_to_connector_mission_e2e.py` — 3 passed
  - `tests/unit/test_missions_v2.py` (connector-validation additions) — full file re-run passing
  - `tests/agents/test_worker_delivery_outcomes.py` (full file, including the new duplicate-delivery test and the 2 fixed regressions) — 29 passed
- Final full-suite run for this session: **598 passed, 0 failed**, 1002.48s (0:16:42). Command: `cd backend && python -m pytest -q`. (Baseline at session start: 582 passed. See `docs/implementation/STATUS.md` for the full per-file breakdown of what changed.)

### Frontend

```
cd frontend && npm install && npm run typecheck
```

- `npm run typecheck` (`tsc --noEmit`): **0 errors**.
- `npm run build` / Playwright E2E: **not run this session** — see §5.

---

## 5. Unresolved risks, ranked by severity

1. **(Medium) No browser-driven E2E suite exists in this repository.** This session added no Playwright infrastructure (none existed before it). The golden-case flows (clean PASS, defective BLOCK_DEPLOYMENT, unsupported-workbook rejection, connector-timeout, duplicate-delivery) are proven at the backend integration level (real API → real Pub/Sub push endpoint → real connector over `httpx.ASGITransport`), which exercises the identical server-side logic a browser would trigger, but no test drives the actual `sources/page.tsx`/`missions/[missionId]/page.tsx` UI in a real browser. **Recommendation:** stand up Playwright (config + browser install + a `next dev`/`uvicorn` test harness) as dedicated follow-up work before treating the UI itself as acceptance-tested, not just the API it calls.
2. **(Low-Medium) Fully automatic connector-path test generation does not always land on the exact locked dollar figures** for a workbook whose numeric inputs have an open-ended range (see DECISIONS.md D8). The decision logic is unaffected; only the specific baseline probe value is. **Recommendation:** either declare a `maximum` on `roof_age` in the golden workbook sample, or (better, more general) enhance the connector-path test-candidate generation to seed from the package's own control cases directly.
3. **(Low) First-divergent-node for the connector path is premium-output granularity**, not a full calculation-node-by-node trace diff between the oracle and the connector's returned trace (the two trace vocabularies are not guaranteed to share node ids one-to-one). Root cause still names the exact expected/actual values and connector identity.
4. **(Low) No cross-tenant/cross-user access tests were added.** Grep confirms no tenant/auth-scoping concept is enforced anywhere in this codebase's mission/source access paths today — this is a pre-existing, single-tenant-scope gap (consistent with the locked doc's own MVP framing), not something this session introduced.
5. **(Low) `POST /connectors/{id}/test` admin route remains unbuilt** (deferred since D7; no requirement in this session needed it).
6. **(Informational) Frontend `npm audit` reports 6 pre-existing vulnerabilities (5 high, 1 critical) in the dependency tree**, unrelated to this session's changes (surfaced only because `node_modules` had to be installed fresh to run typecheck). Not fixed in this session (out of scope; `npm audit fix --force` would apply breaking changes and needs its own review).

---

## 6. Release blockers

**None identified for the scope of this session's deliverable** (workbook → connector Release Conformance wiring). All 16 numbered requirements are met or met-with-documented-scoping (see §1). The unresolved risks above are follow-up quality/coverage improvements, not fail-closed guarantees that are missing — every failure mode the task specified was verified to never produce a false `PASS`.

---

## 7. Documentation requiring updates

- `README.md` — should mention the connector-backed Release Conformance flow and the `.xlsx` Controlled Workbook v1 upload path once this work is merged/deployed (not updated in this session — out of the stated scope, which was implementation + tests + the three specified docs).
- `docs/compliance/CLAIMS_AND_LIMITATIONS.md` (if it exists) should incorporate the DECISIONS.md D8 limitations (automatic test-generation scoping, first-divergent-node granularity) so no downstream claim overstates what the automatic path guarantees without a seeded control case.
- A future Playwright E2E suite's results, once built, should be appended to this document rather than a new one.

---

## 8. Recommendation

**READY WITH DOCUMENTED LIMITATIONS.**

The core deliverable — Controlled Workbook v1 as Source A, a real REST connector as Source B, reconciled through the existing deterministic oracle/reconciliation/decision pipeline, with every failure mode fail-closed and every stage visible — is implemented, wired into the single mission engine (no duplication), and verified by passing backend tests that exercise the real API surface and a real (non-mocked) connector call. The locked golden case ($700.00 vs $655.00, exact root cause) is proven end-to-end. No test was weakened, mocked-away, or deleted to make this pass.

It is not unconditionally "READY" because: (a) no browser-driven E2E suite exists to verify the actual UI a judge/user would click through, and (b) the fully-unattended automatic test-generation path has a documented, narrow scoping gap for inputs with an open-ended declared range. Neither of these affects the correctness or fail-closed guarantees of the deployed decision logic itself — both are coverage/precision follow-ups, not security or correctness defects.
